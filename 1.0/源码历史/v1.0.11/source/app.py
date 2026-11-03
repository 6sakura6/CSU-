"""CSU Peace Check-in Helper -- local Windows desktop application."""
from __future__ import annotations

import ctypes
from datetime import timedelta
from dataclasses import replace
import json
import hashlib
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import queue
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox

from core import Settings, now_cn, RunResult, ManualLocation
from storage import Store, SingleInstance, app_dir, crypt, atomic_write
from browser import BrowserRunner
from campus_network import CampusNetwork, NetworkCredentials, CARRIERS, parse_portal_response
from tray import SystemTray, ICON_PATH
from startup import StartupRegistration, startup_is_authorized, self_test_registration

VERSION = "1.0.11"
DISCLAIMER_TEXT = "本程序仅供学习和测试使用。\n\n请在下载后 24 小时内删除。"
STARTUP_CONSENT = hashlib.sha256(DISCLAIMER_TEXT.encode("utf-8")).hexdigest()


def show_disclaimer(root):
    root.title("平安打卡助手 · 免责声明")
    root.geometry("560x260")
    root.resizable(False, False)
    root.configure(bg="white")
    accepted = tk.BooleanVar(root, value=False)
    content = tk.Frame(root, bg="white", padx=28, pady=24)
    content.pack(fill="both", expand=True)
    tk.Label(content, text="免责声明", bg="white", fg="#153A76",
             font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w")
    tk.Label(content, text=DISCLAIMER_TEXT, bg="white", fg="#24344B",
             font=("Microsoft YaHei UI", 11), wraplength=500,
             justify="left").pack(anchor="w", pady=(15, 20))
    actions = tk.Frame(content, bg="white")
    actions.pack(fill="x", side="bottom")
    tk.Button(actions, text="我已阅读并同意", command=lambda: accepted.set(True),
              bg="#2466DA", fg="white", activebackground="#1955BC", activeforeground="white",
              relief="flat", padx=16, pady=9, font=("Microsoft YaHei UI", 10)).pack(side="right")
    tk.Button(actions, text="退出程序", command=lambda: accepted.set(False),
              bg="#EDF1F7", fg="#24344B", activebackground="#DFE6F0",
              relief="flat", padx=16, pady=9, font=("Microsoft YaHei UI", 10)).pack(side="right", padx=(0, 10))
    root.protocol("WM_DELETE_WINDOW", lambda: accepted.set(False))
    root.wait_variable(accepted)
    result = accepted.get()
    content.destroy()
    root.resizable(True, True)
    return result


class App:
    def __init__(self, root, store=None, tray_factory=SystemTray, startup_factory=StartupRegistration):
        self.root, self.store = root, store or Store()
        self.lock = SingleInstance(self.store.root)
        self.messages = queue.Queue()
        self.stop_event = threading.Event()
        self.worker = None
        self.campus_network = CampusNetwork(self.store, self.log)
        self.network_dialog_open = False
        self.enabled = False
        self.closing = False
        self.closed = False
        self.hidden = False
        self.tray_hint_shown = False
        self.tray = tray_factory(lambda action: self.messages.put(("tray", action)))
        self.next_attempt = None
        self.completed_day = ""
        self.checked_day = ""
        self.current_mode = ""
        self.current_day = ""
        self.logger = logging.getLogger("peace-checkin")
        self.logger.setLevel(logging.INFO)
        handler = RotatingFileHandler(self.store.root / "activity.log", maxBytes=512000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        self.logger.addHandler(handler)
        self.settings = Settings()
        self.startup = startup_factory()
        load_error = None
        try:
            self.settings = self.store.settings()
            credentials = self.store.credentials()
            username = credentials["username"] if credentials else ""
        except Exception:
            username = ""
            load_error = "本地配置无法读取，请重新填写并保存。加密文件只能由原 Windows 账户读取。"
        self.username = tk.StringVar(value=username)
        self.password = tk.StringVar()
        self.start_time = tk.StringVar(value=self.settings.start)
        self.end_time = tk.StringVar(value=self.settings.end)
        self.retry = tk.StringVar(value=str(self.settings.retry_minutes))
        try:
            login_enabled = startup_is_authorized(self.settings, self.startup, STARTUP_CONSENT)
        except OSError:
            login_enabled = False
        self.login_start = tk.BooleanVar(value=login_enabled)
        self.location_text = tk.StringVar(value=self.location_summary())
        self.network_text = tk.StringVar(value=self.network_summary())
        self.status = tk.StringVar(value="等待配置")
        self.next_text = tk.StringVar(value="保存账户和时间后，点击开始定时。")
        self.saved_text = tk.StringVar(value="已保存账户；密码留空表示沿用已保存密码。" if username else "账户密码只在本机输入和加密保存。")
        self.build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        if load_error:
            self.log(load_error)
        else:
            self.log("程序已就绪。使用北京时间；验证码出现时请在弹出的浏览器处理。")
        if self.settings.auto_start and username:
            self.root.after(700, self.start_schedule)
        self.root.after(200, self.tick)

    def build_ui(self):
        self.root.title(f"平安打卡助手  {VERSION}")
        # Leave room for the startup option and several visible log entries.
        x = max(0, (self.root.winfo_screenwidth() - 870) // 2)
        y = max(0, (self.root.winfo_screenheight() - 1000) // 2)
        self.root.geometry(f"870x960+{x}+{y}")
        self.root.minsize(770, 730)
        self.root.configure(bg="#F2F5FA")
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background="#F2F5FA")
        style.configure("Card.TFrame", background="white")
        style.configure("TLabel", background="white", foreground="#24344B", font=("Microsoft YaHei UI", 10))
        style.configure("Muted.TLabel", foreground="#627086", font=("Microsoft YaHei UI", 9))
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("TButton", padding=(15, 9), font=("Microsoft YaHei UI", 10), width=0)
        style.configure("Exit.TButton", padding=(10, 9))
        style.configure("Option.TCheckbutton", background="white", foreground="#24344B", font=("Microsoft YaHei UI", 9))
        style.map("Option.TCheckbutton", background=[("active", "white")])
        style.configure("Primary.TButton", background="#2466DA", foreground="white", borderwidth=0)
        style.map("Primary.TButton", background=[("active", "#1955BC"), ("disabled", "#B5C4DA")])
        style.configure("Running.TButton", background="#D93025", foreground="white", borderwidth=0)
        style.map("Running.TButton", background=[("disabled", "#C6423B"), ("pressed", "#A81F17"), ("active", "#B3261E")],
                  foreground=[("disabled", "white")])
        style.configure("TEntry", padding=7, font=("Microsoft YaHei UI", 10))
        outer = ttk.Frame(self.root, padding=22)
        outer.pack(fill="both", expand=True)
        heading = tk.Frame(outer, bg="#F2F5FA")
        heading.pack(fill="x", pady=(0, 17))
        tk.Label(heading, text="平安打卡助手", bg="#F2F5FA", fg="#153A76",
                 font=("Microsoft YaHei UI", 23, "bold")).pack(anchor="w")
        tk.Label(heading, text="中南大学 · 校园账户登录 · 定位设置 · 每日定时执行",
                 bg="#F2F5FA", fg="#64758C", font=("Microsoft YaHei UI", 10)).pack(anchor="w", pady=(4, 0))

        account = ttk.Frame(outer, style="Card.TFrame", padding=17)
        account.pack(fill="x", pady=(0, 12))
        account.columnconfigure(1, weight=1)
        account.columnconfigure(3, weight=1)
        ttk.Label(account, text="校园账户", style="Title.TLabel").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 13))
        ttk.Label(account, text="学号 / 工号").grid(row=1, column=0, sticky="w", padx=(0, 10))
        self.user_entry = ttk.Entry(account, textvariable=self.username, width=24)
        self.user_entry.grid(row=1, column=1, sticky="ew", padx=(0, 20))
        ttk.Label(account, text="密码").grid(row=1, column=2, sticky="w", padx=(0, 10))
        self.pass_entry = ttk.Entry(account, textvariable=self.password, show="●", width=24)
        self.pass_entry.grid(row=1, column=3, sticky="ew")
        ttk.Label(account, textvariable=self.saved_text, style="Muted.TLabel").grid(row=2, column=0, columnspan=4, sticky="w", pady=(10, 0))

        schedule = ttk.Frame(outer, style="Card.TFrame", padding=17)
        schedule.pack(fill="x", pady=(0, 12))
        ttk.Label(schedule, text="每日执行时间", style="Title.TLabel").grid(row=0, column=0, columnspan=6, sticky="w", pady=(0, 13))
        ttk.Label(schedule, text="打卡时间").grid(row=1, column=0, padx=(0, 8))
        ttk.Entry(schedule, textvariable=self.start_time, width=8).grid(row=1, column=1, padx=(0, 23))
        ttk.Label(schedule, text="重试截止").grid(row=1, column=2, padx=(0, 8))
        ttk.Entry(schedule, textvariable=self.end_time, width=8).grid(row=1, column=3, padx=(0, 23))
        ttk.Label(schedule, text="重试间隔（分钟）").grid(row=1, column=4, padx=(0, 8))
        ttk.Spinbox(schedule, textvariable=self.retry, from_=1, to=60, width=5).grid(row=1, column=5)
        ttk.Label(schedule, text="每天重新读取学校开放时段；超出学校时段不提交。时间格式为 HH:MM。", style="Muted.TLabel").grid(row=2, column=0, columnspan=6, sticky="w", pady=(11, 0))
        ttk.Label(schedule, textvariable=self.location_text, style="Muted.TLabel").grid(row=3, column=0, columnspan=5, sticky="w", pady=(8, 0))
        self.location_button = ttk.Button(schedule, text="定位设置", command=self.configure_location)
        self.location_button.grid(row=3, column=5, pady=(8, 0))
        ttk.Label(schedule, textvariable=self.network_text, style="Muted.TLabel").grid(row=5, column=0, columnspan=5, sticky="w", pady=(8, 0))
        self.network_button = ttk.Button(schedule, text="校园网设置", command=self.configure_network)
        self.network_button.grid(row=5, column=5, pady=(8, 0))
        self.login_start_button = ttk.Checkbutton(schedule, text="开机自启动（登录 Windows 后在托盘运行）",
                                                 style="Option.TCheckbutton", variable=self.login_start, command=self.toggle_login_start)
        self.login_start_button.grid(row=4, column=0, columnspan=6, sticky="w", pady=(8, 0))

        controls = ttk.Frame(outer)
        controls.pack(fill="x", pady=(1, 13))
        self.save_button = ttk.Button(controls, text="保存配置", command=self.save)
        self.save_button.pack(side="left", padx=(0, 7))
        self.start_button = ttk.Button(controls, text="开始定时", style="Primary.TButton", command=self.start_schedule)
        self.start_button.pack(side="left", padx=(0, 7))
        ttk.Button(controls, text="停止", command=self.stop_schedule).pack(side="left", padx=(0, 7))
        self.check_button = ttk.Button(controls, text="仅检查登录与定位", command=lambda: self.launch("check"))
        self.check_button.pack(side="left", padx=(0, 7))
        self.run_button = ttk.Button(controls, text="立即检查并打卡", command=lambda: self.launch("manual"))
        self.run_button.pack(side="left")
        self.exit_button = ttk.Button(controls, text="退出程序", style="Exit.TButton", command=self.request_exit)
        self.exit_button.pack(side="right")

        state = ttk.Frame(outer, style="Card.TFrame", padding=17)
        state.pack(fill="x", pady=(0, 12))
        ttk.Label(state, textvariable=self.status, foreground="#1859B5", wraplength=690, font=("Microsoft YaHei UI", 12, "bold")).pack(anchor="w")
        ttk.Label(state, textvariable=self.next_text, wraplength=690, style="Muted.TLabel").pack(anchor="w", pady=(7, 0))

        log_frame = ttk.Frame(outer, style="Card.TFrame", padding=(17, 12))
        log_frame.pack(fill="both", expand=True)
        ttk.Label(log_frame, text="运行记录", style="Title.TLabel").pack(anchor="w", pady=(0, 8))
        self.log_text = tk.Text(log_frame, height=9, wrap="word", state="disabled", bg="#F7F9FC", fg="#354761",
                                relief="flat", padx=10, pady=8, font=("Microsoft YaHei UI", 9))
        self.log_text.pack(fill="both", expand=True)
        tk.Label(outer, text="点击右上角 × 将转入系统托盘，定时继续运行；右键托盘图标可显示窗口或退出程序。运行中请保持电脑联网。",
                 bg="#F2F5FA", fg="#65758B", wraplength=790, justify="left", font=("Microsoft YaHei UI", 9)).pack(side="bottom", before=log_frame, anchor="w", pady=(12, 0))

    def log(self, text, kind="info"):
        # Safe to call from the browser worker; Tk is only touched on the UI thread.
        self.messages.put(("log", (text, kind)))

    def set_awake(self, active):
        ctypes.windll.kernel32.SetThreadExecutionState(0x80000001 if active else 0x80000000)

    def network_summary(self):
        return "校园网：打卡前自动检查与认证已开启" if self.settings.campus_network_enabled else "校园网：自动认证未开启"

    def configure_network(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("任务进行中", "请先停止并等待当前任务结束，再修改校园网设置。", parent=self.root)
            return
        saved = {}
        try:
            saved = self.store.load_secret("network.dat") or {}
            if not isinstance(saved, dict):
                saved = {}
        except Exception:
            self.log("校园网加密配置无法读取，请重新填写校园网账号和密码。", "attention")
        dialog = tk.Toplevel(self.root)
        dialog.title("校园网设置")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        content = ttk.Frame(dialog, style="Card.TFrame", padding=22)
        content.pack(fill="both", expand=True)
        content.columnconfigure(1, weight=1)
        enabled = tk.BooleanVar(dialog, value=self.settings.campus_network_enabled)
        username = tk.StringVar(dialog, value=saved.get("username", ""))
        password = tk.StringVar(dialog)
        label = next((label for label, key in CARRIERS.items() if key == saved.get("carrier")), "请选择认证类型")
        carrier = tk.StringVar(dialog, value=label)
        ttk.Label(content, text="校园网自动认证", style="Title.TLabel").grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 14))
        ttk.Checkbutton(content, text="在每次检查／打卡前检查网络并按需认证", variable=enabled,
                        style="Option.TCheckbutton").grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 14))
        for row, title, variable in ((2, "校园网账号", username), (3, "校园网密码", password)):
            ttk.Label(content, text=title).grid(row=row, column=0, sticky="w", padx=(0, 16), pady=6)
            ttk.Entry(content, textvariable=variable, width=36, show="●" if row == 3 else "").grid(row=row, column=1, sticky="ew", pady=6)
        ttk.Label(content, text="认证类型").grid(row=4, column=0, sticky="w", pady=6)
        ttk.Combobox(content, textvariable=carrier, values=list(CARRIERS), state="readonly", width=34).grid(row=4, column=1, sticky="ew", pady=6)
        ttk.Label(content, text="无需运营商：保留原始账号；其他类型：使用对应运营商后缀。\n"
                  "请按实际认证页面选择，不确定时先在浏览器核对。\n"
                  "账号密码独立加密保存；同一账号的密码留空可沿用已保存密码。\n"
                  "需先连接校园 Wi-Fi 或网线；此功能不切换 Wi-Fi。\n"
                  "测试可能执行校园网认证，不会提交打卡；证书异常时停止认证。",
                  style="Muted.TLabel", wraplength=480, justify="left").grid(row=5, column=0, columnspan=2, sticky="w", pady=(12, 16))

        def save_network(test=False):
            if self.worker and self.worker.is_alive():
                messagebox.showinfo("任务进行中", "请等待当前任务结束再保存校园网设置。", parent=dialog)
                return
            try:
                key = CARRIERS.get(carrier.get(), "")
                user = username.get().strip()
                secret = password.get()
                preserve = bool(saved.get("password") and saved.get("username") == user)
                if not secret and preserve:
                    secret = saved["password"]
                # Disabling is always possible, even when the old secret is corrupt.
                if enabled.get() or test:
                    credentials = NetworkCredentials(user, secret, key)
                    credentials.account()
                else:
                    credentials = None
                settings = replace(self.settings, campus_network_enabled=enabled.get()).validate()
                if credentials:
                    self.store.save_secret("network.dat", {"username": user, "password": secret, "carrier": key})
                self.store.save_settings(settings)
                self.settings = settings
                self.campus_network.reset()
                self.network_text.set(self.network_summary())
                password.set("")
                dialog.destroy()
                self.log("校园网设置已保存。" + ("打卡前将自动检查网络。" if enabled.get() else "打卡前自动认证已关闭。"))
                if test:
                    self.launch("network")
            except Exception as error:
                message = str(error) if isinstance(error, ValueError) else "校园网设置保存失败，请检查本地文件权限。"
                messagebox.showerror("无法保存校园网设置", message, parent=dialog)

        actions = ttk.Frame(content, style="Card.TFrame")
        actions.grid(row=6, column=0, columnspan=2, sticky="e")
        ttk.Button(actions, text="取消", command=dialog.destroy).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="保存并测试连接", command=lambda: save_network(True)).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="保存设置", command=save_network).pack(side="left")
        dialog.grab_set()
        self.network_dialog_open = True
        try:
            self.root.wait_window(dialog)
        finally:
            self.network_dialog_open = False

    def update_schedule_button(self):
        self.start_button.configure(text="定时已启动" if self.enabled else "开始定时",
                                    style="Running.TButton" if self.enabled else "Primary.TButton")
        self.tray.set_running(self.enabled)

    def location_summary(self):
        return "定位：手动输入坐标（WGS84 / GPS）" if self.settings.location_mode == "manual" else "定位：使用电脑自动定位"

    def toggle_login_start(self):
        requested = self.login_start.get()
        if requested and not messagebox.askokcancel(
                "启用开机自启动", DISCLAIMER_TEXT + "\n\n启用后，登录 Windows 时会直接进入系统托盘，沿用本次免责声明确认，"
                "并恢复上次已开启的定时。手动打开仍会显示免责声明。\n\n是否启用？", parent=self.root):
            self.login_start.set(False)
            return
        previous = self.settings
        updated = replace(previous, launch_at_login=requested,
                          startup_consent=STARTUP_CONSENT if requested else "")
        try:
            self.store.save_settings(updated)
            try:
                self.startup.set_enabled(requested)
            except Exception:
                self.store.save_settings(previous)
                raise
            self.settings = updated
            self.log("已启用开机自启动：登录 Windows 后进入托盘，恢复上次已开启的定时。" if requested else "已关闭开机自启动。")
        except Exception:
            try:
                self.login_start.set(startup_is_authorized(previous, self.startup, STARTUP_CONSENT))
            except OSError:
                self.login_start.set(False)
            messagebox.showerror("无法保存自启动设置", "开机自启动设置未能保存，请检查本机写入权限；原定时设置不变。", parent=self.root)

    def start_from_login(self):
        self.tray_hint_shown = True
        self.close()
        self.log(DISCLAIMER_TEXT.replace("\n", " "))
        if self.hidden:
            state = "将恢复上次已开启的定时。" if self.settings.auto_start else "定时尚未开启，可从托盘打开窗口设置。"
            self.tray.notify(DISCLAIMER_TEXT + "\n\n已随 Windows 登录启动。" + state)
        if self.settings.auto_start and not self.username.get():
            self.show_window()
            self.status.set("开机启动完成，但账户不可用，请重新保存账户。")

    def configure_location(self):
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("任务进行中", "请先停止并等待本次操作结束，再修改定位。", parent=self.root)
            return
        saved = None
        try:
            saved = self.store.manual_location()
        except Exception:
            pass
        dialog = tk.Toplevel(self.root)
        dialog.title("定位设置")
        dialog.geometry("610x440")
        dialog.resizable(False, False)
        dialog.configure(bg="white")
        dialog.transient(self.root)
        mode = tk.StringVar(dialog, value=self.settings.location_mode)
        latitude = tk.StringVar(dialog, value=f"{saved.latitude:.8f}" if saved else "")
        longitude = tk.StringVar(dialog, value=f"{saved.longitude:.8f}" if saved else "")
        preview = tk.StringVar(dialog, value="填好后点击“校验并换算”，可查看十进制度结果。")
        body = ttk.Frame(dialog, style="Card.TFrame", padding=22)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="定位方式", style="Title.TLabel").pack(anchor="w", pady=(0, 10))
        options = ttk.Frame(body, style="Card.TFrame")
        options.pack(fill="x")
        ttk.Radiobutton(options, text="电脑自动定位", variable=mode, value="device", command=lambda: refresh()).pack(side="left", padx=(0, 20))
        ttk.Radiobutton(options, text="手动输入定位", variable=mode, value="manual", command=lambda: refresh()).pack(side="left")
        fields = ttk.Frame(body, style="Card.TFrame")
        fields.pack(fill="x", pady=(18, 12))
        fields.columnconfigure(1, weight=1)
        ttk.Label(fields, text="纬度（北纬 / 南纬）").grid(row=0, column=0, sticky="w", padx=(0, 12), pady=(0, 10))
        lat_entry = ttk.Entry(fields, textvariable=latitude, width=34)
        lat_entry.grid(row=0, column=1, sticky="ew", pady=(0, 10))
        ttk.Label(fields, text="经度（东经 / 西经）").grid(row=1, column=0, sticky="w", padx=(0, 12))
        lng_entry = ttk.Entry(fields, textvariable=longitude, width=34)
        lng_entry.grid(row=1, column=1, sticky="ew")
        ttk.Label(body, text="格式：小数（如 28.123456），或度分秒（如 28°7′24″）。\n"
                  "分别填写纬度、经度，不要把两个数粘贴到同一输入框。\n"
                  "北纬、东经为正；南纬、西经可填负数或标注方向。\n"
                  "使用本人当前所在地的 WGS84 / GPS 坐标；其他坐标系请先转换。",
                  style="Muted.TLabel", wraplength=560, justify="left").pack(anchor="w")
        ttk.Label(body, textvariable=preview, wraplength=560, foreground="#1859B5").pack(anchor="w", pady=(14, 12))

        def parse():
            location = ManualLocation.from_inputs(latitude.get(), longitude.get())
            preview.set(f"换算结果：纬度 {location.latitude:.8f}，经度 {location.longitude:.8f}")
            return location

        def check():
            try:
                parse()
            except ValueError as error:
                messagebox.showerror("坐标格式不正确", str(error), parent=dialog)

        def save_location():
            if self.worker and self.worker.is_alive():
                messagebox.showinfo("任务进行中", "请等待本次操作结束后再保存定位。", parent=dialog)
                return
            try:
                updated = replace(self.settings, location_mode=mode.get()).validate()
                if updated.location_mode == "manual":
                    self.store.save_manual_location(parse())
                self.store.save_settings(updated)
                self.settings = updated
                self.location_text.set(self.location_summary())
                self.log("定位设置已保存；坐标在本机加密保存。" if updated.location_mode == "manual" else "已切换为电脑自动定位。")
                dialog.destroy()
            except Exception as error:
                message = str(error) if isinstance(error, (ValueError, OSError)) else "无法保存定位设置，请检查本地文件。"
                messagebox.showerror("无法保存定位", message, parent=dialog)

        actions = ttk.Frame(body, style="Card.TFrame")
        actions.pack(side="bottom", fill="x")
        check_button = ttk.Button(actions, text="校验并换算", command=check)
        check_button.pack(side="left")
        ttk.Button(actions, text="保存定位设置", style="Primary.TButton", command=save_location).pack(side="right")
        ttk.Button(actions, text="取消", command=dialog.destroy).pack(side="right", padx=(0, 8))

        def refresh():
            state = "normal" if mode.get() == "manual" else "disabled"
            for widget in (lat_entry, lng_entry, check_button):
                widget.configure(state=state)

        refresh()
        dialog.grab_set()
        self.root.wait_window(dialog)

    def save(self, quiet=False):
        if self.worker and self.worker.is_alive():
            if not quiet:
                messagebox.showinfo("任务进行中", "请先停止并等待本次操作结束，再修改配置。", parent=self.root)
            return False
        try:
            settings = replace(self.settings, start=self.start_time.get().strip(), end=self.end_time.get().strip(),
                               retry_minutes=int(self.retry.get()), auto_start=self.enabled).validate()
            user, password = self.username.get().strip(), self.password.get()
            try:
                existing = self.store.credentials()
            except Exception:
                if not password:
                    raise ValueError("已保存的账户文件无法读取，请重新填写账户密码。") from None
                existing = None
            if password:
                self.store.save_credentials(user, password)
                if not existing or existing["username"] != user:
                    self.completed_day, self.checked_day = "", ""
            elif not existing or existing["username"] != user:
                raise ValueError("首次保存或更换账户时，请填写密码。")
            self.store.save_settings(settings)
            self.settings = settings
            self.password.set("")
            self.saved_text.set("账户密码已加密保存；密码留空表示沿用已保存密码。")
            if not quiet:
                self.log("配置已保存；账户密码和登录状态使用 Windows 当前用户加密。")
            return True
        except Exception as error:
            message = str(error) if isinstance(error, (ValueError, OSError)) else "配置保存失败，请检查文件夹写入权限。"
            messagebox.showerror("无法保存", message, parent=self.root)
            return False

    def start_schedule(self):
        if self.worker and self.worker.is_alive():
            return
        if not self.save(quiet=True):
            return
        self.enabled = True
        self.settings.auto_start = True
        self.store.save_settings(self.settings)
        self.next_attempt = None
        self.set_awake(True)
        self.update_schedule_button()
        self.status.set("定时已启动")
        self.log(f"每天 {self.settings.start} 开始，失败后每 {self.settings.retry_minutes} 分钟重试，至 {self.settings.end} 截止。")

    def stop_schedule(self):
        self.enabled = False
        self.update_schedule_button()
        self.stop_event.set()
        self.settings.auto_start = False
        try:
            self.store.save_settings(self.settings)
        except Exception:
            self.log("暂停状态未能保存，请检查文件写入权限。")
        self.set_awake(False)
        self.status.set("正在停止…" if self.worker and self.worker.is_alive() else "定时已停止")
        self.next_text.set("点击开始定时可恢复。")
        self.log("已停止定时，并请求停止当前操作。")

    def launch(self, mode):
        if self.worker and self.worker.is_alive():
            return
        if mode not in ("scheduled", "network") and not self.save(quiet=True):
            return
        self.current_mode, self.current_day = mode, now_cn().date().isoformat()
        self.stop_event = threading.Event()
        self.set_awake(True)
        self.status.set("正在测试校园网连接…" if mode == "network" else "正在检查登录与定位…" if mode == "check" else "正在检查并执行打卡…")
        for button in (self.save_button, self.start_button, self.check_button, self.run_button, self.location_button, self.network_button):
            button.configure(state="disabled")
        self.log("开始测试校园网（不会提交打卡）。" if mode == "network" else "开始仅检查（不会提交）。" if mode == "check" else "开始执行；先查询今天是否已经打卡。")
        runner = None if mode == "network" else BrowserRunner(self.store, self.settings, self.log, self.stop_event, scheduled=mode == "scheduled")
        network_enabled = self.settings.campus_network_enabled
        def work():
            try:
                result = None
                if network_enabled or mode == "network":
                    result = self.campus_network.ensure_online(self.stop_event)
                    if mode != "network" and result.code == "campus_ready":
                        self.log(result.message)
                if mode != "network" and (result is None or result.code == "campus_ready"):
                    if self.stop_event.is_set():
                        result = RunResult("cancelled", "本次操作已停止。")
                    else:
                        result = runner.run(dry_run=mode == "check")
            except Exception:
                result = RunResult("internal", "本次任务未完成，请检查运行环境后重试。", needs_user=True)
            self.messages.put(("result", result))
        self.worker = threading.Thread(target=work, name="checkin-browser", daemon=False)
        self.worker.start()

    def handle_result(self, result):
        # The worker has sent its last message; re-enable actions only after its exit.
        if self.worker:
            self.worker.join(timeout=0.3)
        if self.worker and self.worker.is_alive():
            self.root.after(100, lambda: self.handle_result(result))
            return
        self.worker = None
        for button in (self.save_button, self.start_button, self.check_button, self.run_button, self.location_button, self.network_button):
            button.configure(state="normal")
        self.log(result.message, "attention" if result.needs_user else "info")
        self.status.set(result.message)
        if self.current_mode == "network":
            # A network-only test must not mark check-in done or delay its schedule.
            if not self.enabled:
                self.set_awake(False)
            self.update_schedule_button()
            return
        if result.completed and self.current_mode != "check":
            self.completed_day = self.current_day
            try:
                self.store.mark_done(self.current_day)
            except Exception:
                self.log("结果已核验，但本地完成记录未保存；下次仍会先查询学校记录。")
        if self.enabled:
            at = now_cn()
            self.load_schedule_day(at)
            self.next_attempt = (at + timedelta(minutes=self.settings.retry_minutes)
                                 if (self.current_mode == "check" or not result.completed)
                                 and self.current_day == at.date().isoformat()
                                 else None)
            self.refresh_next_attempt(at)
            if result.completed and self.current_mode != "check":
                self.status.set(result.message + " 每日定时继续运行。")
                self.log("本日任务已完成，每日定时保持开启；" + self.next_text.get())
            elif self.current_mode != "check":
                self.status.set(result.message + " 定时继续运行。")
                self.log("本次未完成，将按设定间隔继续检查（不超过当日截止时间）；" + self.next_text.get())
        else:
            self.set_awake(False)

        self.update_schedule_button()

    def load_schedule_day(self, at):
        day = at.date().isoformat()
        if self.checked_day != day:
            self.checked_day = day
            # A previous day's retry must never delay the new day's first task.
            self.next_attempt = None
            try:
                if self.store.done(day):
                    self.completed_day = day
            except Exception:
                self.log("本地完成记录不可用，将以学校服务端状态为准。")

    def refresh_next_attempt(self, at):
        day = at.date().isoformat()
        self.next_attempt = self.settings.next_run(
            at, finished=self.completed_day == day,
            retry_at=self.next_attempt)
        self.next_text.set("下一次检查：" + self.next_attempt.strftime("%m月%d日 %H:%M") + "（北京时间）")

    def tick(self):
        if self.closed:
            return
        command = self.lock.command()
        if command:
            self.handle_tray_action(command)
            if self.closed:
                return
        try:
            while True:
                kind, value = self.messages.get_nowait()
                if kind == "tray":
                    self.handle_tray_action(value)
                    if self.closed:
                        return
                elif kind == "result":
                    self.handle_result(value)
                else:
                    text, level = value
                    stamp = now_cn().strftime("%m-%d %H:%M:%S")
                    line = f"{stamp}  {text}"
                    self.logger.info(line)
                    self.log_text.configure(state="normal")
                    self.log_text.insert("end", line + "\n")
                    if int(self.log_text.index("end-1c").split(".")[0]) > 600:
                        self.log_text.delete("1.0", "100.0")
                    self.log_text.see("end")
                    self.log_text.configure(state="disabled")
                    if level == "attention":
                        if self.hidden and not self.closing:
                            self.show_window()
                        self.root.bell()
        except queue.Empty:
            pass
        if self.closing:
            if self.worker is None:
                self.finish_close()
                return
        elif self.enabled and not self.worker and not self.network_dialog_open:
            at = now_cn()
            self.load_schedule_day(at)
            self.refresh_next_attempt(at)
            if at >= self.next_attempt:
                self.launch("scheduled")
        self.root.after(300, self.tick)

    def close(self):
        if self.closing or self.closed:
            return
        self.tray.set_running(self.enabled)
        if not self.tray.start():
            self.show_window()
            messagebox.showerror("无法转入后台", "系统托盘图标未能启动，窗口将保持打开，请稍后重试。", parent=self.root)
            return
        self.root.withdraw()
        self.hidden = True
        if not self.tray_hint_shown:
            self.tray.notify("已转入后台。已启用的定时会继续运行；右键此图标可显示窗口或退出程序。")
            self.tray_hint_shown = True

    def show_window(self):
        if self.closed:
            return
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()
        self.hidden = False

    def handle_tray_action(self, action):
        if action == "show":
            self.show_window()
        elif action == "exit":
            self.request_exit()
        elif action == "unavailable" and not self.closed:
            self.show_window()
            self.log("系统托盘暂时不可用，已恢复主窗口；当前定时状态不变。", "attention")

    def request_exit(self):
        if self.closed or self.closing:
            return
        self.closing = True
        self.enabled = False
        self.stop_event.set()
        self.tray.set_closing()
        if self.worker and self.worker.is_alive():
            self.status.set("正在停止当前操作并退出，请稍候…")
        else:
            self.finish_close()

    def finish_close(self):
        if self.closed:
            return
        self.closed = True
        self.tray.stop()
        self.set_awake(False)
        self.lock.close()
        self.root.destroy()


def self_test():
    """Packaged-runtime test: no login, permissions or school submission."""
    result = {"version": VERSION, "encryption": False, "browser_driver": False, "system_tray": False,
              "startup_registration": False, "campus_network": False}
    try:
        sample = b"packaged-dpapi-roundtrip"
        result["encryption"] = crypt(crypt(sample), decrypt=True) == sample
        expected = {"campus": "runtime-check", "cmcc": "runtime-check@cmccn",
                    "unicom": "runtime-check@unicomn", "telecom": "runtime-check@telecomn"}
        result["campus_network"] = (all(NetworkCredentials("runtime-check", "offline", key).account() == value
                                        for key, value in expected.items())
                                    and parse_portal_response('csuStatus({"result":0});')["result"] == 0)
        result["startup_registration"] = self_test_registration()
        tray = SystemTray(lambda action: None)
        try:
            result["system_tray"] = tray.start()
        finally:
            tray.stop()
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(channel="msedge", headless=True)
            page = browser.new_page()
            page.set_content("<title>runtime-check</title><p>runtime ready</p>")
            result["browser_driver"] = page.title() == "runtime-check"
            browser.close()
    except Exception as error:
        result["error_type"] = type(error).__name__
    atomic_write(app_dir() / "data" / "self-test.json", json.dumps(result, indent=2).encode())
    return 0 if all(result.get(key) for key in ("encryption", "browser_driver", "system_tray", "startup_registration", "campus_network")) else 1


def main():
    if "--self-test" in sys.argv:
        return self_test()
    if "--quit" in sys.argv:
        return 0 if SingleInstance.signal(app_dir() / "data", "exit") else 1
    from_login = "--startup" in sys.argv
    if from_login:
        try:
            if not startup_is_authorized(Store().settings(), StartupRegistration(), STARTUP_CONSENT):
                return 0
        except Exception:
            return 0
        if SingleInstance.is_running(app_dir() / "data"):
            return 0
    elif SingleInstance.signal(app_dir() / "data", "show"):
        return 0
    root = tk.Tk()
    root.iconbitmap(default=str(ICON_PATH))
    if not from_login and not show_disclaimer(root):
        root.destroy()
        return 0
    try:
        application = App(root)
        if from_login:
            application.start_from_login()
    except Exception as error:
        messagebox.showerror("无法启动", str(error), parent=root)
        root.destroy()
        return 1
    if "--ui-smoke" in sys.argv:
        root.after(2000, root.destroy)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
