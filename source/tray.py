"""Windows tray callbacks send messages; only the Tk thread changes the UI."""
from __future__ import annotations

import threading
from pathlib import Path


ICON_PATH = Path(__file__).resolve().parent / "assets" / "app.ico"


class SystemTray:
    def __init__(self, emit):
        self.emit = emit
        self.icon = None
        self.thread = None
        self.running = False
        self.exiting = False
        self.ready = threading.Event()
        self.stopping = threading.Event()
        self.failed = False
        self.was_visible = False

    @property
    def status(self):
        return "正在退出…" if self.exiting else "定时已启动" if self.running else "定时未启动"

    def start(self):
        if self.thread and self.thread.is_alive():
            return bool(self.icon and self.icon.visible and not self.stopping.is_set())
        self.ready.clear()
        self.stopping.clear()
        self.failed = self.was_visible = False
        try:
            from PIL import Image
            from pystray import Icon, Menu, MenuItem
            with Image.open(ICON_PATH) as source:
                image = source.convert("RGBA").resize((64, 64), Image.Resampling.LANCZOS)
            self.icon = Icon("csu-peace-checkin", image, "平安打卡助手 · " + self.status,
                             menu=Menu(
                                 MenuItem("显示主窗口", lambda: self.emit("show"), default=True),
                                 MenuItem(lambda item: self.status, None, enabled=False),
                                 Menu.SEPARATOR,
                                 MenuItem("退出程序", lambda: self.emit("exit")),
                             ))
            self.thread = threading.Thread(target=self._run, name="checkin-tray", daemon=True)
            self.thread.start()
            if self.ready.wait(3) and not self.failed and self.thread.is_alive() and self.icon.visible:
                return True
        except Exception:
            self.failed = True
        self.stop()
        return False

    def _setup(self, icon):
        if self.stopping.is_set():
            threading.Thread(target=icon.stop, daemon=True).start()
            return
        icon.visible = True
        self.was_visible = True
        self.ready.set()

    def _run(self):
        try:
            self.icon.run(setup=self._setup)
        except Exception:
            self.failed = True
        finally:
            self.ready.set()
            if self.was_visible and not self.stopping.is_set():
                self.emit("unavailable")

    def _update(self):
        if self.icon and self.icon.visible:
            try:
                self.icon.title = "平安打卡助手 · " + self.status
                self.icon.update_menu()
            except Exception:
                pass

    def set_running(self, running):
        self.running = bool(running)
        self._update()

    def set_closing(self):
        self.exiting = True
        self._update()

    def notify(self, message):
        if self.icon and self.icon.visible:
            try:
                self.icon.notify(message, "平安打卡助手")
            except Exception:
                pass

    def stop(self):
        self.stopping.set()
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)
