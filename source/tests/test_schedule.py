"""Simulate several days of UI scheduling without any school connections."""
from datetime import datetime, timedelta
import gc
from pathlib import Path
import sys
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import App
from core import RunResult, SHANGHAI, Settings
from storage import Store
from test_tray import FakeTray


def moment(day, hour=21, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=SHANGHAI)


class DailyScheduleTests(unittest.TestCase):
    def setUp(self):
        # Destroyed Tk interpreters must be collected on the UI thread, before
        # these tests create browser workers or native tray threads.
        gc.collect()
        self.random_patch = patch('storage.random.randint', return_value=0)
        self.draw = self.random_patch.start()
        self.addCleanup(self.random_patch.stop)
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

    def test_completion_sets_tomorrow_delay_and_each_next_day_draws_again(self):
        self.draw.side_effect = [37, 12]
        self.run_at(moment(19), RunResult('success', 'Offline success', completed=True))
        self.assert_continuing(moment(20, 21, 37))
        self.tick_at(moment(20)).assert_not_called()
        self.tick_at(moment(20, 21, 36)).assert_not_called()
        self.run_at(moment(20, 21, 37), RunResult('success', 'Offline success', completed=True))
        self.assert_continuing(moment(21, 21, 12))
        self.assertEqual(self.draw.call_count, 2)
        self.draw.assert_called_with(0, 60)

    def test_retry_and_restart_reuse_the_saved_draw(self):
        self.draw.return_value = 60
        self.run_at(moment(19), RunResult('success', 'Offline success', completed=True))
        self.assert_continuing(moment(20, 22))
        # Empty the in-memory state to exercise the encrypted persisted plan.
        self.app.daily_delays.clear()
        self.app.prepared_days.clear()
        self.app.checked_day = ''
        self.app.completed_day = ''
        self.app.next_attempt = None
        self.assertEqual(Store(self.store.root).schedule_delay('2026-10-20'), 60)
        self.tick_at(moment(20, 21, 59)).assert_not_called()
        self.run_at(moment(20, 22), RunResult('network', 'Offline retry'))
        self.assert_continuing(moment(20, 22, 5))
        self.assertEqual(self.draw.call_count, 1)
        self.app.stop_schedule()
        self.app.start_schedule()
        self.tick_at(moment(20, 21, 59)).assert_not_called()
        self.assertEqual(self.draw.call_count, 1)

    def test_repeated_completion_and_ticks_do_not_redraw_or_rewrite(self):
        self.draw.return_value = 24
        self.run_at(moment(19), RunResult('success', 'Offline success', completed=True))
        with patch.object(self.store, 'schedule_delay', wraps=self.store.schedule_delay) as lookup:
            for _ in range(5):
                self.tick_at(moment(19, 22)).assert_not_called()
            lookup.assert_not_called()
        self.app.current_mode = 'manual'
        self.app.current_day = '2026-10-19'
        with patch('app.now_cn', return_value=moment(19, 22)):
            self.app.handle_result(RunResult('already_done', 'Offline completed', completed=True))
        self.assert_continuing(moment(20, 21, 24))
        self.assertEqual(self.draw.call_count, 1)

    def test_manual_completed_task_creates_plan_even_when_timer_stopped(self):
        self.draw.return_value = 15
        self.app.stop_schedule()
        self.app.current_mode = 'manual'
        self.app.current_day = '2026-10-19'
        self.app.handle_result(RunResult('success', 'Offline manual success', completed=True))
        self.assertEqual(self.store.schedule_delay('2026-10-20'), 15)
        self.assertFalse(self.app.enabled)
        self.tick_at(moment(20)).assert_not_called()

    def test_check_only_and_network_only_never_prepare_random_plan(self):
        for mode in ('check', 'network', 'network_auto'):
            self.app.current_mode = mode
            self.app.current_day = '2026-10-19'
            with patch('app.now_cn', return_value=moment(19)):
                self.app.handle_result(RunResult('checked', 'Offline test'))
        self.draw.assert_not_called()
        self.assertFalse((self.store.root / 'schedule.dat').exists())

    def test_migration_from_existing_completion_prepares_next_day(self):
        self.draw.return_value = 30
        self.store.mark_done('2026-10-19')
        self.tick_at(moment(19)).assert_not_called()
        self.assert_continuing(moment(20, 21, 30))
        self.tick_at(moment(19, 22)).assert_not_called()
        self.assertEqual(self.draw.call_count, 1)

    def test_changed_base_time_keeps_draw_but_recalculates_actual_time(self):
        self.draw.return_value = 42
        self.run_at(moment(19), RunResult('success', 'Offline success', completed=True))
        self.app.start_time.set('20:00')
        self.assertTrue(self.app.save(quiet=True))
        self.tick_at(moment(19, 22)).assert_not_called()
        self.assert_continuing(moment(20, 20, 42))
        self.assertEqual(self.draw.call_count, 1)

    def test_short_window_keeps_delay_before_cutoff(self):
        self.app.start_time.set('23:20')
        self.app.end_time.set('23:25')
        self.app.start_schedule()
        self.draw.return_value = 5
        self.run_at(moment(19, 23, 20), RunResult('success', 'Offline success', completed=True))
        self.draw.assert_called_once_with(0, 5)
        self.assert_continuing(moment(20, 23, 25))
        self.tick_at(moment(20, 23, 24)).assert_not_called()
        self.run_at(moment(20, 23, 25), RunResult('network', 'Offline last-minute retry'))
        self.assertEqual(self.app.next_attempt.date(), moment(21).date())

    def test_corrupt_random_ledger_stops_timer_without_redrawing(self):
        (self.store.root / 'schedule.dat').write_bytes(b'corrupt')
        self.tick_at(moment(19)).assert_not_called()
        self.assertFalse(self.app.enabled)
        self.assertIn('随机排期', self.app.status.get())
        self.draw.assert_not_called()

    def test_plan_write_failure_stops_timer_after_verified_completion(self):
        with patch.object(self.store, 'save_secret', side_effect=lambda filename, value:
                          (_ for _ in ()).throw(PermissionError()) if filename == 'schedule.dat' else None):
            self.app.current_mode = 'scheduled'
            self.app.current_day = '2026-10-19'
            self.app.handle_result(RunResult('success', 'Offline success', completed=True))
        self.assertFalse(self.app.enabled)
        self.assertIn('随机排期', self.app.status.get())


class RandomPlanStorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = Store(Path(self.directory.name))
        self.store.save_credentials('offline-account-a', 'offline-secret')

    def test_draw_is_persistent_encrypted_and_per_account(self):
        with patch('storage.random.randint', side_effect=[11, 59]) as draw:
            self.assertEqual(self.store.schedule_delay('2026-10-30'), 0)
            self.assertEqual(self.store.schedule_delay('2026-10-30', create=True), 11)
            self.assertEqual(Store(self.store.root).schedule_delay('2026-10-30', create=True), 11)
            self.store.save_credentials('offline-account-b', 'offline-secret')
            self.assertEqual(self.store.schedule_delay('2026-10-30'), 0)
            self.assertEqual(self.store.schedule_delay('2026-10-30', create=True), 59)
            self.store.save_credentials('offline-account-a', 'offline-secret')
            self.assertEqual(self.store.schedule_delay('2026-10-30'), 11)
            self.assertEqual(draw.call_count, 2)
        raw = (self.store.root / 'schedule.dat').read_bytes()
        self.assertNotIn(b'2026-10-30', raw)
        self.assertNotIn(b'offline-account-a', raw)

    def test_zero_and_sixty_are_valid_and_invalid_saved_values_are_rejected(self):
        for delay in (0, 60):
            day = f'2026-10-{29 + delay // 60}'
            with patch('storage.random.randint', return_value=delay):
                self.assertEqual(self.store.schedule_delay(day, create=True), delay)
        for value in (-1, 61, True, '15'):
            self.store.save_secret('schedule.dat', {self.store.account_key(): {'2026-10-31': value}})
            with self.assertRaises(ValueError):
                self.store.schedule_delay('2026-10-31')

    def test_saved_delay_respects_shortened_cutoff_without_new_draw(self):
        settings = Settings(start='23:20', end='23:25')
        with patch('storage.random.randint', return_value=60) as draw:
            self.store.schedule_delay('2026-10-30', create=True)
            delay = self.store.schedule_delay('2026-10-30', create=True, limit=5)
            self.assertEqual(settings.daily_start(moment(30).date(), delay), moment(30, 23, 25))
            self.assertEqual(draw.call_count, 1)

    def test_random_start_does_not_override_a_later_retry(self):
        settings = Settings()
        start = moment(29, 20)
        self.assertEqual(settings.next_run(start, delay_minutes=45), moment(29, 21, 45))
        self.assertEqual(settings.next_run(start, delay_minutes=45, retry_at=moment(29, 22)), moment(29, 22))
        self.assertEqual(settings.next_run(moment(29, 23, 26), next_delay_minutes=60), moment(30, 22))
