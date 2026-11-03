"""Qt Quick interface; all live tasks still use App's existing scheduler.

--design-preview uses a temporary store and simulated tasks, never school traffic.
"""
from __future__ import annotations

import ctypes
import base64
from dataclasses import replace
from datetime import datetime, timedelta
import os
from pathlib import Path
import queue
import re
import sys
import tempfile
import time

from PySide6.QtCore import QObject, Property, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QFont, QIcon
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication, QMessageBox

from app import App, DISCLAIMER_TEXT, VERSION, NETWORK_CHECK_SECONDS
from campus_network import CARRIERS, NetworkCredentials
from core import ManualLocation, SHANGHAI, now_cn
from storage import Store
from tray import ICON_PATH, SystemTray
from startup import StartupRegistration

UI_DIR = Path(__file__).resolve().parent / "ui"


class Value:
    """Small observable value, independent of a Tk interpreter."""
    def __init__(self, value, notify):
        self.value, self.notify = value, notify

    def get(self):
        return self.value

    def set(self, value):
        if value != self.value:
            self.value = value
            self.notify()


class ActionState:
    def __init__(self, bridge, name):
        self.bridge, self.name = bridge, name
        self.options = {"state": "normal"}

    def configure(self, **options):
        self.options.update(options)
        self.bridge.publish()

    def cget(self, key):
        return self.options.get(key, "")


class QtRoot:
    def __init__(self, application, window):
        self.application, self.window = application, window
        self.alive = True
        self.timers = set()

    def after(self, milliseconds, callback):
        timer = QTimer(self.window)
        timer.setSingleShot(True)
        self.timers.add(timer)

        def invoke():
            self.timers.discard(timer)
            timer.deleteLater()
            if self.alive:
                callback()
        timer.timeout.connect(invoke)
        timer.start(milliseconds)
        return timer

    def protocol(self, *_):
        pass  # Window.onClosing is connected to Bridge.closeWindow.

    def withdraw(self):
        self.window.hide()

    def deiconify(self):
        self.window.showNormal()

    def lift(self):
        self.window.raise_()

    def focus_force(self):
        self.window.requestActivate()

    def bell(self):
        self.application.beep()

    def destroy(self):
        self.alive = False
        for timer in self.timers:
            timer.stop()
            timer.deleteLater()
        self.timers.clear()
        self.window.hide()
        self.application.quit()


class QtDialogs:
    """Synchronous native dialogs keep existing save/confirmation semantics."""
    def _show(self, title, text, question=False):
        box = QMessageBox()
        box.setWindowTitle(title)
        box.setWindowIcon(QIcon(str(ICON_PATH)))
        box.setText(text)
        box.setIcon(QMessageBox.Icon.Question if question else QMessageBox.Icon.Information)
        box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
                               if question else QMessageBox.StandardButton.Ok)
        box.button(QMessageBox.StandardButton.Ok).setText("我已阅读并同意" if title == "使用前请阅读" else "确认" if question else "知道了")
        if question:
            box.button(QMessageBox.StandardButton.Cancel).setText("退出程序" if title == "使用前请阅读" else "取消")
            box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        return box.exec() == QMessageBox.StandardButton.Ok

    def showinfo(self, title, text, **_):
        return self._show(title, text)

    showerror = showinfo

    def askokcancel(self, title, text, **_):
        return self._show(title, text, question=True)


class PreviewTray:
    def __init__(self, emit):
        self.running = False

    def start(self):
        return False  # A preview must stay visible rather than hide without a tray.

    def set_running(self, running):
        self.running = running

    def set_closing(self):
        pass

    def stop(self):
        pass

    def notify(self, message):
        pass


class PreviewStartup:
    def is_enabled(self):
        return False

    def set_enabled(self, enabled):
        pass


class GlassApp(App):
    def __init__(self, root, bridge, store, tray_factory, startup_factory):
        self.bridge = bridge
        bridge.scheduler = self
        self.dialogs = QtDialogs()
        super().__init__(root, store, tray_factory, startup_factory)

    def variable(self, value=""):
        return Value(value, self.bridge.publish)

    def build_ui(self):
        for name in ("save", "start", "check", "run", "location", "network", "exit"):
            setattr(self, name + "_button", ActionState(self.bridge, name))

    def append_visible_log(self, line, level):
        # Keep the complete in-session log bounded and newest-first in the view.
        stamp, _, message = line.partition("  ")
        self.bridge.records.insert(0, {"time": stamp[-8:-3], "date": stamp[:5],
                                       "message": message, "attention": level == "attention"})
        del self.bridge.records[600:]
        self.bridge.publish()

    def configure_location(self):
        self.bridge.openPanel("location")

    def configure_network(self):
        self.bridge.openPanel("network")

    def tick(self):
        if not self.bridge.preview:
            super().tick()
        elif not self.closed:
            # Deliberately no scheduler, browser, network, or single-instance
            # commands in the isolated visual preview.
            try:
                while True:
                    kind, value = self.messages.get_nowait()
                    if kind == "log":
                        text, level = value
                        if level != "background":
                            self.append_visible_log("11-02 20:46:00  " + text, level)
            except queue.Empty:
                pass
            self.root.after(300, self.tick)
        self.bridge.publish()

    def launch(self, mode):
        if not self.bridge.preview:
            super().launch(mode)
            self.bridge.publish()
            return
        if self.bridge.mock_busy:
            return
        self.bridge.mock_busy = True
        self.bridge.preview_task += 1
        task = self.bridge.preview_task
        self.current_mode = mode
        self.status.set("正在检查登录与定位…" if mode == "check" else
                        "正在测试校园网连接…" if mode == "network" else "正在检查并执行打卡…")
        self.bridge.publish()

        def finish():
            if task != self.bridge.preview_task:
                return
            self.bridge.mock_busy = False
            if mode == "manual":
                self.completed_day = "2026-11-02"
                self.next_attempt = datetime(2026, 11, 3, 21, 18, tzinfo=SHANGHAI)
            text = ("预览：登录与定位检查完成，未提交打卡。" if mode == "check" else
                    "预览：校园网连接测试完成。" if mode == "network" else
                    "预览：今日打卡已完成（模拟结果）。")
            self.status.set(text)
            self.log(text)
            self.bridge.publish()
        self.root.after(1600, finish)

    def set_awake(self, active):
        if not self.bridge.preview:
            super().set_awake(active)


def system_reduced_motion():
    enabled = ctypes.c_int(1)
    try:
        ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0)
    except Exception:
        pass
    return not bool(enabled.value) or os.environ.get("CHECKIN_REDUCED_MOTION") == "1"


class Bridge(QObject):
    stateChanged = Signal()
    panelRequested = Signal(str, "QVariantMap")
    toastRequested = Signal(str)

    def __init__(self, preview=False):
        super().__init__()
        self.preview, self.ready, self.mock_busy = preview, False, False
        self.preview_task = 0
        self.scheduler = None
        self.records = []
        self.icon_cache = {}
        self._state = {"ready": False, "busy": False, "enabled": False, "completed": False,
                       "headline": "欢迎使用", "status": "请先阅读使用说明", "username": "",
                       "maskedUser": "尚未配置账户", "start": "21:00", "end": "23:25", "retry": "5",
                       "nextTime": "--:--", "nextLabel": "下次执行", "nextDate": "北京时间",
                       "nextDetail": "保存账户和时间后，点击开始定时。", "loginStart": False,
                       "networkEnabled": False, "locationManual": False, "records": [], "logCount": 0,
                       "preview": preview, "reducedMotion": system_reduced_motion(), "version": VERSION}

    @Property("QVariantMap", notify=stateChanged)
    def state(self):
        return self._state

    @Slot(str, QColor, result=str)
    def iconUrl(self, name, tint):
        # Recolor the licensed source SVG rather than drawing approximate icons.
        key = (name, tint.name())
        if key not in self.icon_cache:
            if not re.fullmatch(r"[a-z-]+", name):
                return ""
            path = UI_DIR / "assets" / (name + ".svg")
            if not path.is_file():
                return ""
            svg = re.sub(r'fill="(?!none)[^"]+"', 'fill="' + tint.name() + '"', path.read_text(encoding="utf-8"))
            if not re.search(r'<svg[^>]*\bfill=', svg):
                svg = svg.replace("<svg ", '<svg fill="' + tint.name() + '" ', 1)
            self.icon_cache[key] = "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()
        return self.icon_cache[key]

    def publish(self):
        if not self.ready:
            return
        app = self.scheduler
        at = datetime(2026, 11, 2, 20, 46, tzinfo=SHANGHAI) if self.preview else now_cn()
        # A finished worker remains busy until its queued result is handled.
        busy = bool(self.mock_busy or app.worker is not None)
        done = app.completed_day == at.date().isoformat()
        has_user = bool(app.username.get())
        headline = ("正在检查…" if busy else "今日已完成" if done else
                    "等待今日打卡" if has_user else "先设置校园账户")
        next_at = app.next_attempt if app.enabled else None
        next_day = "今天" if next_at and next_at.date() == at.date() else "明天" if (
            next_at and next_at.date() == at.date() + timedelta(days=1)) else ""
        user = app.username.get()
        masked = (user[:3] + "****" + user[-4:]) if len(user) > 7 else (user[:1] + "****" if user else "尚未配置")
        weekdays = "一二三四五六日"
        state = {**self._state, "ready": True, "busy": busy, "enabled": app.enabled,
                 "completed": done, "headline": headline, "status": app.status.get(),
                 "username": user, "maskedUser": masked, "start": app.settings.start,
                 "end": app.settings.end, "retry": str(app.settings.retry_minutes),
                 "nextTime": next_at.strftime("%H:%M") if next_at else "--:--",
                 "nextLabel": "下次执行" + (" · " + next_day if next_day else ""),
                 "nextDate": (next_at or at).strftime("%Y年%m月%d日") + "  星期" + weekdays[(next_at or at).weekday()],
                 "nextDetail": app.next_text.get(), "loginStart": bool(app.login_start.get()),
                 "networkEnabled": app.settings.campus_network_enabled,
                 "locationManual": app.settings.location_mode == "manual",
                 "records": self.records[:], "logCount": len(self.records),
                 "canEdit": not busy and not app.closing,
                 "preview": self.preview, "reducedMotion": self._state["reducedMotion"]}
        if state != self._state:
            self._state = state
            self.stateChanged.emit()

    def editable(self):
        if not self.ready:
            return False
        if self.mock_busy or self.scheduler.worker is not None:
            self.toastRequested.emit("任务进行中，请停止并等待结束后再修改设置。")
            return False
        return not self.scheduler.closing

    @Slot(str)
    def openPanel(self, name):
        if name != "logs" and not self.editable():
            return
        app = self.scheduler
        data = {}
        if name == "account":
            data = {"username": app.username.get(), "saved": bool(app.username.get())}
        elif name == "schedule":
            data = {"start": app.settings.start, "end": app.settings.end,
                    "retry": app.settings.retry_minutes}
        elif name == "location":
            location = None
            try:
                location = app.store.manual_location()
            except Exception:
                app.log("本地定位配置无法读取，请重新填写。", "attention")
            data = {"manual": app.settings.location_mode == "manual",
                    "latitude": f"{location.latitude:.8f}" if location else "",
                    "longitude": f"{location.longitude:.8f}" if location else ""}
        elif name == "network":
            try:
                saved = app.store.load_secret("network.dat") or {}
                if not isinstance(saved, dict):
                    saved = {}
            except Exception:
                saved = {}
                app.log("校园网加密配置无法读取，请重新填写或关闭自动认证。", "attention")
            data = {"enabled": app.settings.campus_network_enabled,
                    "username": saved.get("username", ""),
                    "carrierIndex": list(CARRIERS.values()).index(saved["carrier"]) if saved.get("carrier") in CARRIERS.values() else -1}
        if name != "logs":
            app.network_dialog_open = True  # Settings pause new work, just as in Tk.
        self.panelRequested.emit(name, data)

    @Slot()
    def panelClosed(self):
        if self.ready:
            self.scheduler.network_dialog_open = False
            self.publish()

    @Slot(str, str, result=bool)
    def saveAccount(self, username, password):
        if not self.editable():
            return False
        app = self.scheduler
        old_user, old_password = app.username.get(), app.password.get()
        app.username.set(username)
        app.password.set(password)
        if not app.save():
            app.username.set(old_user)
            app.password.set(old_password)
            return False
        self.toastRequested.emit("校园账户已加密保存")
        self.publish()
        return True

    @Slot(str, str, int, result=bool)
    def saveSchedule(self, start, end, retry):
        if not self.editable():
            return False
        app = self.scheduler
        try:
            updated = replace(app.settings, start=start.strip(), end=end.strip(), retry_minutes=retry).validate()
            app.store.save_settings(updated)
            app.settings = updated
            app.start_time.set(updated.start)
            app.end_time.set(updated.end)
            app.retry.set(str(updated.retry_minutes))
            app.next_attempt = None
            if app.enabled and not self.preview:
                app.refresh_next_attempt(now_cn())
            elif self.preview and app.enabled:
                app.next_attempt = updated.daily_start(datetime(2026, 11, 2).date())
            app.log("每日执行时间已保存。")
            self.toastRequested.emit("定时设置已保存")
            self.publish()
            return True
        except (ValueError, OSError) as error:
            app.dialogs.showerror("无法保存时间", str(error))
            return False

    @Slot(str, str, result=str)
    def validateLocation(self, latitude, longitude):
        try:
            location = ManualLocation.from_inputs(latitude, longitude)
            return f"纬度 {location.latitude:.8f}，经度 {location.longitude:.8f}"
        except ValueError as error:
            return str(error)

    @Slot(bool, str, str, result=bool)
    def saveLocation(self, manual, latitude, longitude):
        if not self.editable():
            return False
        app = self.scheduler
        try:
            if manual:
                app.store.save_manual_location(ManualLocation.from_inputs(latitude, longitude))
            updated = replace(app.settings, location_mode="manual" if manual else "device").validate()
            app.store.save_settings(updated)
            app.settings = updated
            app.location_text.set(app.location_summary())
            app.log("定位设置已保存；使用本人当前所在地的真实位置。")
            self.toastRequested.emit("定位设置已保存")
            self.publish()
            return True
        except (ValueError, OSError) as error:
            app.dialogs.showerror("无法保存定位", str(error))
            return False

    @Slot(bool, str, str, int, bool, result=bool)
    def saveNetwork(self, enabled, username, password, carrier_index, test):
        if not self.editable():
            return False
        app = self.scheduler
        try:
            try:
                saved = app.store.load_secret("network.dat") or {}
                if not isinstance(saved, dict):
                    saved = {}
            except Exception:
                saved = {}
            user = username.strip()
            if not password and user == saved.get("username"):
                password = saved.get("password", "")
            credentials = None
            if enabled or test:
                if not 0 <= carrier_index < len(CARRIERS):
                    raise ValueError("请选择认证类型：无需运营商、移动、联通或电信。")
                carrier = list(CARRIERS.values())[carrier_index]
                credentials = NetworkCredentials(user, password, carrier)
                credentials.account()
            updated = replace(app.settings, campus_network_enabled=enabled).validate()
            if credentials:
                app.store.save_secret("network.dat", {"username": user, "password": password, "carrier": credentials.carrier})
            app.store.save_settings(updated)
            app.settings = updated
            app.campus_network.reset()
            app.next_network_check = time.monotonic() if enabled else None
            app.network_text.set(app.network_summary())
            app.log("校园网设置已保存。" + ("每小时独立检测并按需认证。" if enabled else "自动认证已关闭。"))
            self.toastRequested.emit("校园网设置已保存")
            self.publish()
            if test:
                app.root.after(100, lambda: app.launch("network"))
            return True
        except (ValueError, OSError) as error:
            app.dialogs.showerror("无法保存校园网设置", str(error))
            return False

    @Slot(bool)
    def toggleStartup(self, checked):
        if not self.editable():
            return
        app = self.scheduler
        if self.preview:
            app.login_start.set(checked)
            self.toastRequested.emit("预览：已切换开机自启（未修改系统设置）")
        else:
            app.login_start.set(checked)
            app.toggle_login_start()
        self.publish()

    @Slot()
    def toggleSchedule(self):
        if not self.ready:
            return
        app = self.scheduler
        if app.enabled or app.worker is not None or self.mock_busy:
            app.stop_schedule()
            if self.preview:
                self.mock_busy = False
                self.preview_task += 1
        elif self.editable():
            app.start_schedule()
            if self.preview and app.enabled:
                app.next_attempt = datetime(2026, 11, 2, 21, 18, tzinfo=SHANGHAI)
        self.publish()

    @Slot(str)
    def runTask(self, mode):
        if self.editable() and mode in ("check", "manual"):
            self.scheduler.launch(mode)

    @Slot()
    def closeWindow(self):
        if not self.ready or self.preview:
            if self.scheduler:
                self.scheduler.request_exit()
        else:
            self.scheduler.close()

    @Slot()
    def exitApp(self):
        if self.ready:
            self.scheduler.request_exit()


def run(from_login=False, preview=False):
    QQuickWindow.setDefaultAlphaBuffer(True)
    application = QApplication.instance() or QApplication(sys.argv)
    application.setQuitOnLastWindowClosed(False)
    application.setApplicationName("平安打卡助手")
    application.setWindowIcon(QIcon(str(ICON_PATH)))
    application.setFont(QFont("Microsoft YaHei UI", 10))
    application.setStyle("Fusion")
    QQuickStyle.setStyle("Basic")
    application.setStyleSheet("QMessageBox{background:#f7faff;color:#172540;} "
                             "QLabel{min-width:280px;font:11pt 'Microsoft YaHei UI';padding:12px;} "
                             "QPushButton{background:#e6eef9;border:0;border-radius:10px;padding:9px 22px;color:#1855a0;} "
                             "QPushButton:default{background:#007aff;color:white;}")
    if not preview and not from_login and not QtDialogs().askokcancel("使用前请阅读", DISCLAIMER_TEXT):
        return 0
    temporary = tempfile.TemporaryDirectory(prefix="peace-checkin-preview-") if preview else None
    store = Store(Path(temporary.name)) if temporary else Store()
    if preview:
        store.save_credentials("20230006789", "isolated-preview-only")
    bridge = Bridge(preview)
    engine = QQmlApplicationEngine()
    engine.warnings.connect(lambda warnings: [print(item.toString(), file=sys.stderr) for item in warnings])
    engine.rootContext().setContextProperty("bridge", bridge)
    engine.load(QUrl.fromLocalFile(str(UI_DIR / "Main.qml")))
    if not engine.rootObjects():
        if temporary:
            temporary.cleanup()
        return 1
    window = engine.rootObjects()[0]
    root = QtRoot(application, window)
    try:
        scheduler = GlassApp(root, bridge, store, PreviewTray if preview else SystemTray,
                             PreviewStartup if preview else StartupRegistration)
    except Exception as error:
        QtDialogs().showerror("无法启动", str(error))
        if temporary:
            temporary.cleanup()
        return 1
    bridge.ready = True
    if preview:
        scheduler.enabled = True
        scheduler.settings.campus_network_enabled = True
        scheduler.login_start.set(True)
        scheduler.status.set("已设定定时任务，将在下方时间自动执行")
        scheduler.next_attempt = datetime(2026, 11, 2, 21, 18, tzinfo=SHANGHAI)
        bridge.records = [
            {"time": "20:46", "date": "11-02", "message": "定时已开启，等待执行", "attention": False},
            {"time": "20:45", "date": "11-02", "message": "校园网连接正常", "attention": False},
            {"time": "20:45", "date": "11-02", "message": "配置已保存", "attention": False},
        ]
        # Drop the synthetic startup log to keep the reference state deterministic.
        while not scheduler.messages.empty():
            scheduler.messages.get_nowait()
    else:
        scheduler.load_schedule_day(now_cn())
        if scheduler.username.get():
            scheduler.status.set("账户已保存，可以开始定时或立即检查。")
    bridge.publish()
    if "--preview-size" in sys.argv:
        width, height = sys.argv[sys.argv.index("--preview-size") + 1].split("x")
        window.setWidth(int(width))
        window.setHeight(int(height))
    if from_login:
        scheduler.start_from_login()
    else:
        window.show()
    if preview and "--preview-panel" in sys.argv:
        panel = sys.argv[sys.argv.index("--preview-panel") + 1]
        if panel in ("account", "schedule", "location", "network", "logs"):
            root.after(200, lambda: bridge.openPanel(panel))
    if "--capture" in sys.argv and preview:
        capture = Path(sys.argv[sys.argv.index("--capture") + 1]).resolve()
        capture.parent.mkdir(parents=True, exist_ok=True)

        def capture_window():
            if "--inspect-ui" in sys.argv:
                import json
                def item_tree(item):
                    info = {"type": item.metaObject().className(), "x": item.x(), "y": item.y(),
                            "width": item.width(), "height": item.height(), "visible": item.isVisible(),
                            "text": item.property("text"), "name": item.objectName()}
                    info["children"] = [item_tree(child) for child in item.childItems()]
                    return info
                capture.with_suffix(".json").write_text(json.dumps(item_tree(window.contentItem()), ensure_ascii=False, default=str), encoding="utf-8")
            if not window.grabWindow().save(str(capture)):
                print("Capture failed", file=sys.stderr)
            scheduler.request_exit()
        root.after(1300, capture_window)
    elif "--ui-smoke" in sys.argv:
        root.after(1800, scheduler.request_exit)
    code = application.exec()
    if not scheduler.closed:
        scheduler.request_exit()
    for handler in list(scheduler.logger.handlers):
        handler.close()
        scheduler.logger.removeHandler(handler)
    if temporary:
        temporary.cleanup()
    return code
