"""Network configuration and check-in sequencing use offline mocks only."""
from datetime import datetime, timedelta
import gc
from pathlib import Path
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import App
from core import RunResult, now_cn, SHANGHAI
from storage import Store
from test_tray import FakeTray


def descendants(widget):
    for child in widget.winfo_children():
        yield child
        yield from descendants(child)


class NetworkUiTests(unittest.TestCase):
    def setUp(self):
        gc.collect()
        self.directory = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.store = Store(Path(self.directory.name))
        self.app = App(self.root, self.store, tray_factory=FakeTray)
        self.network_mock = patch.object(self.app.campus_network, 'ensure_online',
                                         return_value=RunResult('campus_ready', 'Offline connected'))
        self.ensure_online = self.network_mock.start()
        self.addCleanup(self.network_mock.stop)
        self.app.next_network_check = float('inf')
        self.root.update_idletasks()

    def tearDown(self):
        for timer in self.root.tk.splitlist(self.root.tk.call('after', 'info')):
            self.root.after_cancel(timer)
        self.app.finish_close()
        for handler in list(self.app.logger.handlers):
            handler.close()
            self.app.logger.removeHandler(handler)
        self.app = self.root = None
        gc.collect()
        self.directory.cleanup()

    def dialog_action(self, action):
        errors = []
        def interact():
            dialog = next(child for child in self.root.winfo_children() if isinstance(child, tk.Toplevel))
            try:
                dialog.update_idletasks()
                action(dialog, list(descendants(dialog)))
            except Exception as error:
                errors.append(error)
                if dialog.winfo_exists():
                    dialog.destroy()
        self.root.after(60, interact)
        self.app.configure_network()
        if errors:
            raise errors[0]

    def fields(self, items):
        entries = [item for item in items if type(item) is ttk.Entry]
        choice = next(item for item in items if isinstance(item, ttk.Combobox))
        toggle = next(item for item in items if isinstance(item, ttk.Checkbutton))
        buttons = {item.cget('text'): item for item in items if isinstance(item, ttk.Button)}
        return entries, choice, toggle, buttons

    def test_requires_explicit_type_and_shows_all_four_choices(self):
        def action(dialog, items):
            entries, choice, toggle, buttons = self.fields(items)
            self.assertEqual(choice.get(), '请选择认证类型')
            self.assertEqual(tuple(choice.cget('values')), ('无需运营商', '中国移动', '中国联通', '中国电信'))
            toggle.invoke()
            entries[0].insert(0, 'offline-network')
            entries[1].insert(0, 'offline-secret')
            buttons['保存设置'].invoke()
            self.assertTrue(dialog.winfo_exists())
            self.assertFalse(self.store.settings().campus_network_enabled)
            buttons['取消'].invoke()
        with patch('app.messagebox.showerror') as error:
            self.dialog_action(action)
            error.assert_called_once()
        self.assertIsNone(self.store.load_secret('network.dat'))

    def test_save_no_carrier_separate_from_checkin_and_preserve_password(self):
        self.store.save_credentials('checkin-user', 'checkin-secret')
        def save(dialog, items):
            entries, choice, toggle, buttons = self.fields(items)
            toggle.invoke()
            entries[0].insert(0, 'network-user')
            entries[1].insert(0, 'network-secret')
            choice.set('无需运营商')
            for item in items:
                if isinstance(item, (ttk.Button, ttk.Entry, ttk.Label, ttk.Checkbutton)):
                    self.assertTrue(item.winfo_ismapped())
                    self.assertLessEqual(item.winfo_rootx() + item.winfo_width(), dialog.winfo_rootx() + dialog.winfo_width())
                    self.assertLessEqual(item.winfo_rooty() + item.winfo_height(), dialog.winfo_rooty() + dialog.winfo_height())
            buttons['保存设置'].invoke()
        with patch('app.messagebox.showerror') as error:
            self.dialog_action(save)
            error.assert_not_called()
        self.assertTrue(self.store.settings().campus_network_enabled)
        self.assertEqual(self.store.credentials()['username'], 'checkin-user')
        self.assertEqual(self.store.load_secret('network.dat'), {'username': 'network-user', 'password': 'network-secret', 'carrier': 'campus'})
        def change_carrier(dialog, items):
            entries, choice, toggle, buttons = self.fields(items)
            self.assertEqual(entries[1].get(), '')
            choice.set('中国联通')
            buttons['保存设置'].invoke()
        self.dialog_action(change_carrier)
        saved = self.store.load_secret('network.dat')
        self.assertEqual((saved['password'], saved['carrier']), ('network-secret', 'unicom'))

    def test_changed_network_account_requires_new_password(self):
        self.app.settings.campus_network_enabled = True
        self.store.save_secret('network.dat', {'username': 'old', 'password': 'old-secret', 'carrier': 'campus'})
        def action(dialog, items):
            entries, choice, toggle, buttons = self.fields(items)
            entries[0].delete(0, 'end')
            entries[0].insert(0, 'new')
            buttons['保存设置'].invoke()
            self.assertEqual(self.store.load_secret('network.dat')['username'], 'old')
            buttons['取消'].invoke()
        with patch('app.messagebox.showerror') as error:
            self.dialog_action(action)
            error.assert_called_once()

    def test_corrupt_secret_can_always_be_disabled(self):
        self.app.settings.campus_network_enabled = True
        (self.store.root / 'network.dat').write_bytes(b'corrupt')
        def action(dialog, items):
            entries, choice, toggle, buttons = self.fields(items)
            toggle.invoke()
            buttons['保存设置'].invoke()
        with patch('app.messagebox.showerror') as error:
            self.dialog_action(action)
            error.assert_not_called()
        self.assertFalse(self.store.settings().campus_network_enabled)

    def test_cancel_does_not_save_or_reset_authentication_failure(self):
        self.app.campus_network.rejected = True
        def action(dialog, items):
            _, _, _, buttons = self.fields(items)
            self.assertTrue(self.app.network_dialog_open)
            self.app.enabled = True
            with patch.object(self.app, 'launch') as launch:
                self.app.tick()
                launch.assert_not_called()
            self.app.enabled = False
            buttons['取消'].invoke()
        self.dialog_action(action)
        self.assertFalse(self.app.network_dialog_open)
        self.assertFalse(self.store.settings().campus_network_enabled)
        self.assertTrue(self.app.campus_network.rejected)

    def run_worker(self, mode):
        self.app.launch(mode)
        self.assertIsNotNone(self.app.worker)
        self.app.worker.join(3)
        self.assertFalse(self.app.worker.is_alive())
        self.app.tick()

    def test_test_connection_has_no_checkin_account_requirement_and_no_browser(self):
        next_attempt = now_cn() + timedelta(hours=1)
        self.app.next_attempt = next_attempt
        with patch.object(self.app.campus_network, 'ensure_online', return_value=RunResult('campus_ready', 'Offline connected')) as ensure, \
             patch('app.BrowserRunner') as browser, patch.object(self.store, 'mark_done') as done:
            self.run_worker('network')
        ensure.assert_called_once()
        browser.assert_not_called()
        done.assert_not_called()
        self.assertEqual(self.app.next_attempt, next_attempt)
        self.assertEqual(self.app.completed_day, '')

    def test_test_button_saves_then_launches_network_only(self):
        def action(dialog, items):
            entries, choice, _, buttons = self.fields(items)
            entries[0].insert(0, 'offline-network')
            entries[1].insert(0, 'offline-secret')
            choice.set('中国移动')
            buttons['保存并测试连接'].invoke()
        with patch.object(self.app, 'launch') as launch:
            self.dialog_action(action)
            launch.assert_called_once_with('network')
        self.assertFalse(self.app.settings.campus_network_enabled)
        self.assertEqual(self.store.load_secret('network.dat')['carrier'], 'cmcc')

    def test_checkin_retries_do_not_trigger_extra_network_checks(self):
        self.app.settings.campus_network_enabled = True
        self.app.username.set('offline-checkin')
        self.app.password.set('offline-secret')
        for code in ('network', 'credentials', 'checked'):
            self.app.password.set('offline-secret')
            with patch('app.BrowserRunner') as browser:
                browser.return_value.run.return_value = RunResult(code, 'Offline check')
                self.run_worker('check')
                browser.return_value.run.assert_called_once_with(dry_run=True)
        self.ensure_online.assert_not_called()

    def test_disabled_preflight_does_not_touch_network(self):
        self.app.username.set('offline-checkin')
        self.app.password.set('offline-secret')
        with patch.object(self.app.campus_network, 'ensure_online') as ensure, patch('app.BrowserRunner') as browser:
            browser.return_value.run.return_value = RunResult('checked', 'Offline check')
            self.run_worker('check')
            ensure.assert_not_called()
            browser.return_value.run.assert_called_once_with(dry_run=True)

    def test_hourly_checks_run_without_checkin_account_or_timer(self):
        self.app.settings.campus_network_enabled = True
        self.app.next_network_check = 100
        morning = datetime(2026, 10, 29, 8, tzinfo=SHANGHAI)
        with patch('app.BrowserRunner') as browser, patch('app.now_cn', return_value=morning):
            for value, expected_calls in ((100, 1), (3699, 1), (3700, 2)):
                with patch('app.time.monotonic', return_value=value):
                    self.app.tick()
                    if self.app.worker:
                        self.app.worker.join(3)
                        self.app.tick()
                    self.assertEqual(self.ensure_online.call_count, expected_calls)
            browser.assert_not_called()
        self.assertFalse(self.app.enabled)
        self.assertIsNone(self.store.credentials())
        self.assertEqual(self.app.next_network_check, 7300)

    def test_hourly_failure_and_manual_test_both_wait_an_hour(self):
        self.app.settings.campus_network_enabled = True
        self.ensure_online.return_value = RunResult('campus_unavailable', 'Offline unavailable')
        with patch('app.time.monotonic', return_value=200):
            self.run_worker('network_auto')
        self.assertEqual(self.app.next_network_check, 3800)
        with patch('app.time.monotonic', return_value=3799), patch.object(self.app, 'launch') as launch:
            self.app.tick()
            launch.assert_not_called()
        with patch('app.time.monotonic', return_value=300):
            self.run_worker('network')
        self.assertEqual(self.app.next_network_check, 3900)

    def test_sleep_skips_missed_hours_without_catchup(self):
        self.app.settings.campus_network_enabled = True
        self.app.next_network_check = 1
        with patch('app.time.monotonic', return_value=25000):
            self.app.tick()
            self.app.worker.join(3)
            self.app.tick()
        self.ensure_online.assert_called_once()
        self.assertEqual(self.app.next_network_check, 28600)

    def test_stopping_checkin_keeps_hourly_checks_and_disabled_network_stops_them(self):
        self.app.settings.campus_network_enabled = True
        self.app.stop_schedule()
        self.app.next_network_check = 0
        with patch.object(self.app, 'launch') as launch:
            self.app.tick()
            launch.assert_called_once_with('network_auto')
        self.app.settings.campus_network_enabled = False
        with patch.object(self.app, 'launch') as launch:
            self.app.tick()
            launch.assert_not_called()

    def test_finished_checkin_and_hidden_window_do_not_stop_hourly_checks(self):
        self.app.settings.campus_network_enabled = True
        self.app.completed_day = now_cn().date().isoformat()
        self.app.close()
        self.app.next_network_check = 0
        with patch.object(self.app, 'launch') as launch:
            self.app.tick()
            launch.assert_called_once_with('network_auto')
        self.assertTrue(self.app.hidden)

    def test_busy_worker_defers_network_and_exit_prevents_it(self):
        self.app.settings.campus_network_enabled = True
        self.app.next_network_check = 0
        release = threading.Event()
        self.app.worker = threading.Thread(target=lambda: release.wait(3))
        self.app.worker.start()
        try:
            with patch.object(self.app, 'launch') as launch:
                self.app.tick()
                launch.assert_not_called()
        finally:
            release.set()
            self.app.worker.join(3)
            self.app.worker = None
        with patch.object(self.app, 'launch') as launch:
            self.app.tick()
            launch.assert_called_once_with('network_auto')
        self.app.closing = True
        with patch.object(self.app, 'launch') as launch:
            self.app.tick()
            launch.assert_not_called()

    def test_due_network_runs_first_then_due_checkin(self):
        self.app.settings.campus_network_enabled = True
        self.app.next_network_check = 100
        self.app.enabled = True
        self.app.start_time.set('21:00')
        self.app.settings.start = '21:00'
        at = datetime(2026, 10, 29, 21, tzinfo=SHANGHAI)
        with patch('app.time.monotonic', return_value=100), patch('app.now_cn', return_value=at):
            self.app.tick()
            self.app.worker.join(3)
            with patch.object(self.app, 'launch') as launch:
                self.app.tick()
                launch.assert_called_once_with('scheduled')
        self.assertEqual(self.app.next_network_check, 3700)

    def test_startup_restores_checkin_after_initial_network_check(self):
        self.app.settings.campus_network_enabled = True
        self.app.settings.auto_start = True
        self.app.username.set('offline-checkin')
        self.app.password.set('offline-secret')
        release = threading.Event()
        self.ensure_online.side_effect = lambda stop: (release.wait(2) or None) and RunResult('campus_ready', 'Offline connected')
        self.app.launch('network_auto')
        self.root.after(20, self.app.restore_schedule)
        self.root.after(70, release.set)
        self.root.after(480, self.root.quit)
        noon = datetime(2026, 10, 29, 12, tzinfo=SHANGHAI)
        try:
            with patch('app.now_cn', return_value=noon):
                self.root.mainloop()
        finally:
            release.set()
            if self.app.worker:
                self.app.worker.join(3)
                self.app.tick()
        self.assertTrue(self.app.enabled)
        self.assertTrue(self.store.settings().auto_start)

    def test_stopped_timer_is_not_restored_by_pending_startup_callback(self):
        self.app.settings.auto_start = True
        self.app.stop_schedule()
        with patch.object(self.app, 'start_schedule') as start:
            self.app.restore_schedule()
            start.assert_not_called()


if __name__ == '__main__':
    unittest.main()
