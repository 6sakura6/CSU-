"""Simulate several days of UI scheduling without any school connections."""
from datetime import datetime
import gc
from pathlib import Path
import sys
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import App
from core import RunResult, SHANGHAI
from storage import Store
from test_tray import FakeTray


def moment(day, hour=21, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=SHANGHAI)


class DailyScheduleTests(unittest.TestCase):
    def setUp(self):
        # Destroyed Tk interpreters must be collected on the UI thread, before
        # these tests create browser workers or native tray threads.
        gc.collect()
        self.directory = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.store = Store(Path(self.directory.name))
        self.app = App(self.root, self.store, tray_factory=FakeTray)
        self.app.username.set('offline-daily-account')
        self.app.password.set('offline-test-password')
        self.app.start_schedule()
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

    def tick_at(self, at):
        with patch('app.now_cn', return_value=at), patch.object(self.app, 'launch') as launch:
            self.app.tick()
        return launch

    def run_at(self, at, result):
        with patch('app.now_cn', return_value=at), patch('app.BrowserRunner') as runner:
            runner.return_value.run.return_value = result
            self.app.tick()
            runner.assert_called_once()
            self.app.worker.join(2)
            self.assertFalse(self.app.worker.is_alive())
            self.app.tick()
            runner.return_value.run.assert_called_once_with(dry_run=False)

    def assert_continuing(self, next_at):
        self.assertTrue(self.app.enabled)
        self.assertTrue(self.store.settings().auto_start)
        self.assertEqual(self.app.start_button.cget('style'), 'Running.TButton')
        self.assertEqual(self.app.next_attempt, next_at)
        self.assertIn(next_at.strftime('%m月%d日 %H:%M'), self.app.next_text.get())

    def test_success_runs_three_days_without_manual_restart(self):
        for day in (19, 20, 21):
            self.tick_at(moment(day, 20, 59)).assert_not_called()
            self.run_at(moment(day), RunResult('success', 'Offline verified success', completed=True))
            self.assert_continuing(moment(day + 1))
            self.tick_at(moment(day, 22)).assert_not_called()
            self.assertTrue(self.store.done(moment(day).date().isoformat()))

    def test_already_done_and_exempt_also_continue_tomorrow(self):
        for day, code in ((19, 'already_done'), (20, 'exempt')):
            self.run_at(moment(day), RunResult(code, 'Offline completed task', completed=True))
            self.assert_continuing(moment(day + 1))

    def test_attention_results_keep_retrying_until_success(self):
        for day, code in enumerate(('menu_missing', 'location', 'credentials', 'manual_login', 'internal'), 19):
            with self.subTest(code=code):
                self.run_at(moment(day), RunResult(code, 'Offline temporary problem', needs_user=True))
                self.assert_continuing(moment(day, 21, 5))
                self.tick_at(moment(day, 21, 4)).assert_not_called()
                self.run_at(moment(day, 21, 5), RunResult(code, 'Offline repeated problem', needs_user=True))
                self.assert_continuing(moment(day, 21, 10))
                self.run_at(moment(day, 21, 10), RunResult('success', 'Offline success', completed=True))
                self.assert_continuing(moment(day + 1))
                self.tick_at(moment(day, 22)).assert_not_called()

    def test_explicit_restart_can_retry_after_attention_today(self):
        self.run_at(moment(19), RunResult('login', 'Offline account issue', needs_user=True))
        self.app.start_schedule()
        self.tick_at(moment(19, 21, 1)).assert_called_once_with('scheduled')

    def test_network_retry_uses_interval_then_cutoff_schedules_tomorrow(self):
        self.run_at(moment(19), RunResult('network', 'Offline network issue'))
        self.assert_continuing(moment(19, 21, 5))
        self.tick_at(moment(19, 21, 4)).assert_not_called()
        self.tick_at(moment(19, 21, 5)).assert_called_once_with('scheduled')
        self.run_at(moment(19, 23, 24), RunResult('network', 'Offline network issue'))
        self.assert_continuing(moment(20))
        self.tick_at(moment(20)).assert_called_once_with('scheduled')

    def test_midnight_discards_previous_days_retry(self):
        self.app.start_time.set('00:00')
        self.app.end_time.set('23:59')
        self.app.start_schedule()
        self.run_at(moment(19, 23, 59), RunResult('network', 'Offline network issue'))
        self.assert_continuing(moment(20, 0))
        self.tick_at(moment(20, 0)).assert_called_once_with('scheduled')

    def test_resume_after_missing_a_day_uses_current_day(self):
        self.run_at(moment(19), RunResult('success', 'Offline success', completed=True))
        self.tick_at(moment(22, 21, 1)).assert_called_once_with('scheduled')

    def test_persisted_completion_skips_today_only(self):
        self.store.mark_done(moment(19).date().isoformat())
        self.tick_at(moment(19)).assert_not_called()
        self.assert_continuing(moment(20))
        self.tick_at(moment(20)).assert_called_once_with('scheduled')

    def test_stop_after_success_does_not_resume_tomorrow(self):
        self.run_at(moment(19), RunResult('success', 'Offline success', completed=True))
        self.app.stop_schedule()
        self.tick_at(moment(20)).assert_not_called()
        self.assertFalse(self.app.enabled)
        self.assertFalse(self.store.settings().auto_start)

    def test_completion_after_stop_does_not_enable_schedule(self):
        self.app.current_mode = 'scheduled'
        self.app.current_day = moment(19).date().isoformat()
        self.app.stop_schedule()
        with patch('app.now_cn', return_value=moment(19)):
            self.app.handle_result(RunResult('success', 'Offline late result', completed=True))
        self.tick_at(moment(20)).assert_not_called()
        self.assertFalse(self.app.enabled)
        self.assertFalse(self.store.settings().auto_start)

    def test_check_only_error_does_not_pause_daily_task(self):
        self.app.current_mode = 'check'
        self.app.current_day = moment(19).date().isoformat()
        with patch('app.now_cn', return_value=moment(19)):
            self.app.handle_result(RunResult('location', 'Offline check issue', needs_user=True))
        self.assert_continuing(moment(19, 21, 5))

    def test_missing_menu_retries_at_custom_interval_in_background(self):
        self.app.retry.set('2')
        self.app.start_schedule()
        self.app.close()
        with patch.object(self.app, 'show_window'), patch.object(self.root, 'bell'):
            self.run_at(moment(22), RunResult('menu_missing', 'Offline missing menu', needs_user=True))
            self.assert_continuing(moment(22, 21, 2))
            self.tick_at(moment(22, 21, 1)).assert_not_called()
            self.run_at(moment(22, 21, 2), RunResult('success', 'Offline success', completed=True))
        self.assertTrue(self.app.tray.running)
        self.assert_continuing(moment(23))

    def test_attention_result_past_retry_cutoff_waits_until_tomorrow(self):
        self.run_at(moment(22, 23, 24), RunResult('menu_missing', 'Offline missing menu', needs_user=True))
        self.assert_continuing(moment(23))
        self.tick_at(moment(22, 23, 25)).assert_not_called()
        self.tick_at(moment(22, 23, 26)).assert_not_called()
        self.tick_at(moment(23)).assert_called_once_with('scheduled')

    def test_last_minute_still_allows_a_due_retry(self):
        self.run_at(moment(22, 23, 20), RunResult('menu_missing', 'Offline missing menu', needs_user=True))
        self.assert_continuing(moment(22, 23, 25))
        self.run_at(moment(22, 23, 25), RunResult('menu_missing', 'Offline missing menu', needs_user=True))
        self.assert_continuing(moment(23))
        self.tick_at(moment(22, 23, 26)).assert_not_called()

    def test_stop_during_retry_wait_cancels_future_attempts(self):
        self.run_at(moment(22), RunResult('menu_missing', 'Offline missing menu', needs_user=True))
        self.app.stop_schedule()
        self.tick_at(moment(22, 21, 5)).assert_not_called()
        self.tick_at(moment(23)).assert_not_called()
        self.assertFalse(self.app.enabled)

    def test_attention_result_after_stop_does_not_enable_schedule(self):
        self.app.current_mode = 'scheduled'
        self.app.current_day = moment(22).date().isoformat()
        self.app.stop_schedule()
        with patch('app.now_cn', return_value=moment(22)):
            self.app.handle_result(RunResult('menu_missing', 'Offline late error', needs_user=True))
        self.tick_at(moment(22, 21, 5)).assert_not_called()
        self.assertFalse(self.store.settings().auto_start)

    def test_hidden_window_keeps_next_days_task_active(self):
        self.app.close()
        self.run_at(moment(19), RunResult('success', 'Offline success', completed=True))
        self.assertTrue(self.app.hidden)
        self.assertTrue(self.app.tray.running)
        self.run_at(moment(20), RunResult('success', 'Offline success', completed=True))
        self.assertTrue(self.app.hidden)
        self.assert_continuing(moment(21))

    def test_old_days_result_does_not_delay_new_days_task(self):
        self.app.start_time.set('00:00')
        self.app.end_time.set('23:59')
        self.app.start_schedule()
        self.tick_at(moment(19, 23, 59))
        self.app.current_mode = 'scheduled'
        self.app.current_day = moment(19).date().isoformat()
        with patch('app.now_cn', return_value=moment(20, 0)):
            self.app.handle_result(RunResult('day_changed', 'Offline date rollover'))
        self.tick_at(moment(20, 0)).assert_called_once_with('scheduled')
