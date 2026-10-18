"""Tray lifecycle checks use temporary data and never connect to the school."""
from datetime import datetime
from pathlib import Path
import sys
import tempfile
import threading
import tkinter as tk
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import App
from core import RunResult, SHANGHAI
from storage import Store, SingleInstance
from tray import SystemTray


class FakeTray:
    def __init__(self, emit):
        self.emit = emit
        self.available = True
        self.running = self.closing = self.stopped = False
        self.notifications = []

    def start(self):
        return self.available

    def set_running(self, value):
        self.running = value

    def set_closing(self):
        self.closing = True

    def notify(self, message):
        self.notifications.append(message)

    def stop(self):
        self.stopped = True


class TrayUiTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.store = Store(Path(self.directory.name))
        self.app = App(self.root, self.store, tray_factory=FakeTray)
        self.root.update_idletasks()

    def tearDown(self):
        for timer in self.root.tk.splitlist(self.root.tk.call('after', 'info')):
            self.root.after_cancel(timer)
        self.app.finish_close()
        for handler in list(self.app.logger.handlers):
            handler.close()
            self.app.logger.removeHandler(handler)
        self.directory.cleanup()

    def test_close_keeps_schedule_and_tick_running(self):
        self.app.enabled = True
        self.app.settings.auto_start = True
        self.store.save_settings(self.app.settings)
        self.app.close()
        self.assertEqual(self.root.state(), 'withdrawn')
        self.assertTrue(self.app.enabled)
        self.assertFalse(self.app.closing)
        self.assertFalse(self.app.stop_event.is_set())
        at = datetime(2026, 10, 18, 21, 0, tzinfo=SHANGHAI)
        with patch('app.now_cn', return_value=at), patch.object(self.app, 'launch') as launch:
            self.app.tick()
            launch.assert_called_once_with('scheduled')
        self.assertTrue(self.store.settings().auto_start)

    def test_close_during_work_does_not_cancel(self):
        release = threading.Event()
        worker = threading.Thread(target=lambda: release.wait(3))
        self.app.worker = worker
        worker.start()
        try:
            self.app.close()
            self.assertTrue(worker.is_alive())
            self.assertFalse(self.app.stop_event.is_set())
            self.assertFalse(self.app.closed)
        finally:
            release.set()
            worker.join(3)
            self.app.worker = None

    def test_show_and_repeated_hide_notify_only_once(self):
        self.app.close()
        self.app.show_window()
        self.root.update_idletasks()
        self.assertFalse(self.app.hidden)
        self.assertNotEqual(self.root.state(), 'withdrawn')
        self.app.close()
        self.assertTrue(self.app.hidden)
        self.assertEqual(len(self.app.tray.notifications), 1)

    def test_tray_failure_does_not_hide_window(self):
        self.app.tray.available = False
        with patch('app.messagebox.showerror') as error:
            self.app.close()
            error.assert_called_once()
        self.assertNotEqual(self.root.state(), 'withdrawn')
        self.assertFalse(self.app.hidden)
        self.assertFalse(self.app.closed)

    def test_tray_callback_only_changes_tk_after_message_dispatch(self):
        self.app.close()
        callback = threading.Thread(target=lambda: self.app.tray.emit('show'))
        callback.start()
        callback.join(2)
        self.assertTrue(self.app.hidden)
        self.app.tick()
        self.assertFalse(self.app.hidden)

    def test_exit_stops_worker_then_releases_tray_and_lock(self):
        release = threading.Event()
        worker = threading.Thread(target=lambda: release.wait(3))
        self.app.worker = worker
        worker.start()
        try:
            self.app.request_exit()
            self.assertTrue(self.app.stop_event.is_set())
            self.assertTrue(self.app.closing)
            self.assertFalse(self.app.closed)
            self.assertFalse(self.app.tray.stopped)
        finally:
            release.set()
            worker.join(3)
        self.app.handle_result(RunResult('cancelled', 'offline cancellation'))
        self.app.tick()
        self.assertTrue(self.app.closed)
        self.assertTrue(self.app.tray.stopped)
        self.assertIsNone(self.app.lock.handle)

    def test_existing_instance_can_restore_and_exit(self):
        self.app.close()
        self.assertTrue(SingleInstance.signal(self.store.root, 'show'))
        self.app.tick()
        self.assertFalse(self.app.hidden)
        self.assertTrue(SingleInstance.signal(self.store.root, 'exit'))
        self.app.tick()
        self.assertTrue(self.app.closed)
        self.assertFalse(SingleInstance.signal(self.store.root, 'show'))

    def test_attention_restores_hidden_window(self):
        self.app.close()
        self.app.log('offline attention', 'attention')
        with patch.object(self.root, 'bell'):
            self.app.tick()
        self.assertFalse(self.app.hidden)

    def test_explicit_exit_button_works_without_a_tray_icon(self):
        self.app.tray.available = False
        self.app.exit_button.invoke()
        self.assertTrue(self.app.closed)
        self.assertTrue(self.app.tray.stopped)
        self.assertIsNone(self.app.lock.handle)

    def test_controls_fit_minimum_window_with_running_label(self):
        self.root.geometry('770x730')
        self.app.enabled = True
        self.app.update_schedule_button()
        with patch.object(self.app, 'launch'):
            self.root.update()
        for button in (self.app.save_button, self.app.start_button, self.app.check_button,
                       self.app.run_button, self.app.exit_button):
            self.assertTrue(button.winfo_ismapped())
            self.assertGreaterEqual(button.winfo_width(), button.winfo_reqwidth())
            self.assertLessEqual(button.winfo_rootx() + button.winfo_width(), self.root.winfo_rootx() + self.root.winfo_width())


class NativeTrayTests(unittest.TestCase):
    def test_real_windows_icon_menu_and_clean_shutdown(self):
        actions = []
        tray = SystemTray(actions.append)
        try:
            self.assertTrue(tray.start())
            self.assertTrue(tray.icon.visible)
            tray.set_running(True)
            self.assertIn('定时已启动', tray.icon.title)
            tray.icon.menu.items[0](tray.icon)
            tray.icon.menu.items[-1](tray.icon)
            self.assertEqual(actions, ['show', 'exit'])
            tray.set_running(False)
            self.assertIn('定时未启动', tray.icon.title)
        finally:
            tray.stop()
        self.assertFalse(tray.thread.is_alive())
        tray.stop()
