"""Headless controller regressions using real DPAPI files and synthetic task results."""
from datetime import datetime, timedelta
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import SHANGHAI, RunResult, Settings
from service import CheckinService, STARTUP_CONSENT
from storage import Store


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.folder.name))
        self.store.save_credentials('fixture-a', 'synthetic-password')
        self.at = datetime(2026, 11, 3, 21, tzinfo=SHANGHAI)
        self.mono = 0
        self.runner = Mock()
        self.runner.return_value.run.return_value = RunResult('network', 'fixture retry')
        self.network = Mock(offline_detected=False)
        self.network.ensure_online.return_value = RunResult('campus_ready', 'fixture network ready')
        self.awake = Mock()
        self.startup = Mock()
        self.service = self.make_service()

    def make_service(self, **options):
        return CheckinService(self.store, runner_factory=self.runner,
            network_factory=lambda *_: self.network, clock=lambda: self.at,
            monotonic=lambda: self.mono, acquire_lock=False, awake=self.awake,
            startup=self.startup, restore=False, **options)

    def tearDown(self):
        self.service.shutdown()
        self.folder.cleanup()

    def finish(self):
        if self.service.worker:
            self.service.worker.join(3)
        self.service.tick()

    def run_result(self, result, mode='manual'):
        self.runner.return_value.run.return_value = result
        self.assertTrue(self.service.launch(mode))
        self.finish()

    def test_snapshot_contains_no_secrets_or_toolkit_dependencies(self):
        self.service.tick()
        text = json.dumps(self.service.snapshot())
        self.assertNotIn('synthetic-password', text)
        self.assertNotIn('cookies', text)

    def test_same_account_keeps_password_but_new_account_requires_one(self):
        self.service.save_account('fixture-a', '')
        self.assertEqual(self.store.credentials()['password'], 'synthetic-password')
        with self.assertRaises(ValueError):
            self.service.save_account('fixture-b', '')

    def test_account_change_clears_session_and_in_memory_schedule(self):
        self.store.save_secret('session.dat', {'cookies': ['fixture']})
        self.service.completed_day = self.service.checked_day = '2026-11-03'
        self.service.daily_delays['2026-11-04'] = 30
        self.service.prepared_days.add('2026-11-04')
        self.service.save_account('fixture-b', 'another-synthetic-password')
        self.assertIsNone(self.store.load_secret('session.dat'))
        self.assertEqual(self.service.completed_day, '')
        self.assertEqual(self.service.daily_delays, {})
        self.assertEqual(self.service.prepared_days, set())

    def test_corrupt_account_can_be_replaced_explicitly(self):
        (self.store.root/'credentials.dat').write_bytes(b'corrupt')
        with self.assertRaises(ValueError):
            self.service.save_account('fixture-a')
        self.service.save_account('fixture-a', 'replacement-synthetic')
        self.assertEqual(self.store.credentials()['password'], 'replacement-synthetic')

    def test_busy_includes_finished_but_unhandled_worker(self):
        self.assertTrue(self.service.launch('check'))
        self.service.worker.join(3)
        self.assertTrue(self.service.snapshot()['busy'])
        self.assertFalse(self.service.launch('manual'))
        with self.assertRaises(ValueError):
            self.service.save_account('fixture-a')
        self.service.tick()
        self.assertFalse(self.service.snapshot()['busy'])

    def test_only_one_worker_is_allowed(self):
        gate = threading.Event()
        self.runner.return_value.run.side_effect = lambda **_: (gate.wait(3), RunResult('cancelled', 'stopped'))[1]
        try:
            self.assertTrue(self.service.launch('manual'))
            self.assertFalse(self.service.launch('network'))
            with self.assertRaises(ValueError):
                self.service.save_schedule('20:00', '23:25', 5)
            self.service.stop_current()
            self.assertTrue(self.service.stop_event.is_set())
        finally:
            gate.set()
            self.finish()

    def test_check_never_marks_done_or_creates_random_draw(self):
        self.run_result(RunResult('checked', 'fixture checked', completed=True), 'check')
        self.assertFalse(self.store.done('2026-11-03'))
        self.assertFalse((self.store.root/'schedule.dat').exists())
        self.runner.return_value.run.assert_called_once_with(dry_run=True)

    def test_success_prepares_tomorrow_and_retains_daily_schedule(self):
        self.service.start_schedule()
        with patch('storage.random.randint', return_value=37) as draw:
            self.run_result(RunResult('success', 'fixture verified', completed=True))
            self.assertTrue(self.service.enabled)
            self.assertTrue(self.store.done('2026-11-03'))
            self.assertEqual(self.service.next_attempt, self.at.replace(day=4, minute=37))
            for _ in range(3):
                self.service.tick()
            draw.assert_called_once_with(0, 60)

    def test_manual_completion_while_paused_still_prepares_tomorrow(self):
        with patch('storage.random.randint', return_value=20):
            self.run_result(RunResult('already_done', 'fixture done', completed=True))
        self.assertFalse(self.service.enabled)
        self.assertEqual(self.store.schedule_delay('2026-11-04'), 20)

    def test_attention_result_retries_until_cutoff(self):
        self.service.start_schedule()
        self.run_result(RunResult('credentials', 'fixture attention', needs_user=True))
        self.assertTrue(self.service.enabled)
        self.assertEqual(self.service.next_attempt, self.at + timedelta(minutes=5))
        self.at = self.at.replace(hour=23, minute=25)
        self.run_result(RunResult('network', 'fixture retry'))
        self.assertEqual(self.service.next_attempt.date(), self.at.date() + timedelta(days=1))

    def test_midnight_discards_old_retry(self):
        self.service.start_schedule()
        self.service.checked_day = '2026-11-03'
        self.service.next_attempt = self.at.replace(day=4, hour=23)
        self.at = self.at.replace(day=4, hour=20)
        self.service.tick()
        self.assertEqual(self.service.next_attempt, self.at.replace(hour=21))
        self.runner.assert_not_called()

    def test_previous_days_completion_does_not_complete_today(self):
        gate, entered = threading.Event(), threading.Event()
        def run(**_):
            entered.set()
            gate.wait(3)
            return RunResult('success', 'fixture done', True)
        self.runner.return_value.run.side_effect = run
        self.service.start_schedule()
        self.service.launch('scheduled')
        self.assertTrue(entered.wait(2))
        self.at = self.at.replace(day=4, hour=20)
        gate.set()
        self.finish()
        self.assertTrue(self.store.done('2026-11-03'))
        self.assertFalse(self.store.done('2026-11-04'))
        self.assertEqual(self.service.next_attempt.date(), self.at.date())

    def test_task_starting_after_midnight_uses_the_browsers_verified_day(self):
        self.at = self.at.replace(hour=23, minute=59)
        self.runner.return_value.day = '2026-11-04'
        self.run_result(RunResult('success', 'fixture done', True))
        self.assertFalse(self.store.done('2026-11-03'))
        self.assertTrue(self.store.done('2026-11-04'))

    def test_pause_is_persisted_and_prevents_new_checkin_tasks(self):
        self.service.start_schedule()
        self.service.stop_schedule()
        self.service.tick()
        self.assertFalse(self.store.settings().auto_start)
        self.runner.assert_not_called()

    def test_exit_preserves_schedule_for_next_manual_start(self):
        self.service.start_schedule()
        self.assertTrue(self.service.shutdown())
        self.assertTrue(self.store.settings().auto_start)
        self.service = CheckinService(self.store, runner_factory=self.runner,
            network_factory=lambda *_: self.network, clock=lambda: self.at,
            acquire_lock=False, awake=self.awake)
        self.assertTrue(self.service.enabled)

    def test_restored_service_reuses_encrypted_random_draw(self):
        with patch('storage.random.randint', return_value=42):
            self.store.schedule_delay('2026-11-03', create=True)
        self.service.start_schedule()
        self.service.tick()
        self.assertEqual(self.service.next_attempt, self.at.replace(minute=42))
        self.assertFalse(self.service.snapshot()['busy'])

    def test_settings_edit_blocks_automatic_tasks_until_closed(self):
        self.service.start_schedule()
        with self.service.editing_settings():
            self.service.tick()
            self.assertFalse(self.service.snapshot()['busy'])
        self.service.tick()
        self.assertTrue(self.service.snapshot()['busy'])
        self.finish()

    def test_network_is_independent_and_cannot_mark_checkin_done(self):
        self.service.save_network(True, 'network-user', 'network-secret', 'cmcc')
        self.network.ensure_online.return_value = RunResult('campus_ready', 'fixture network', True)
        self.service.stop_schedule()
        self.at = self.at.replace(hour=9)
        self.service.tick()
        self.finish()
        self.runner.assert_not_called()
        self.assertFalse(self.store.done('2026-11-03'))
        self.assertFalse((self.store.root/'schedule.dat').exists())
        self.assertEqual(self.service.next_network_check, 3600)
        self.mono = 3599
        self.service.tick()
        self.assertIsNone(self.service.worker)
        self.mono = 3600
        self.service.tick()
        self.finish()
        self.assertEqual(self.network.ensure_online.call_count, 2)

    def test_disabling_network_works_with_corrupt_network_credentials(self):
        (self.store.root/'network.dat').write_bytes(b'corrupt')
        self.service.save_network(False)
        self.assertFalse(self.store.settings().campus_network_enabled)

    def test_network_password_reuse_requires_same_network_account(self):
        self.service.save_network(True, 'network-user', 'network-secret', 'campus')
        self.service.save_network(True, 'network-user', '', 'cmcc')
        self.assertEqual(self.store.load_secret('network.dat')['password'], 'network-secret')
        with self.assertRaises(ValueError):
            self.service.save_network(True, 'another-network-user', '', 'campus')

    def test_manual_location_is_encrypted_and_no_coordinates_in_logs(self):
        self.service.save_location('manual', '30°15′30″', '120°30′30″')
        self.service.tick()
        self.assertEqual(self.store.settings().location_mode, 'manual')
        self.assertNotIn('120.', json.dumps(self.service.snapshot()))
        self.assertNotIn(b'longitude', (self.store.root/'location.dat').read_bytes())

    def test_corrupt_random_ledger_stops_without_redrawing(self):
        self.service.start_schedule()
        (self.store.root/'schedule.dat').write_bytes(b'corrupt')
        with patch('storage.random.randint') as draw:
            self.service.tick()
            draw.assert_not_called()
        self.assertFalse(self.service.enabled)
        self.assertIn('随机排期', self.service.status.get())

    def test_startup_registration_failure_rolls_back_settings(self):
        self.startup.set_enabled.side_effect = OSError('fixture failure')
        previous = self.store.settings()
        with self.assertRaises(OSError):
            self.service.set_login_start(True, consent=STARTUP_CONSENT)
        self.assertEqual(self.store.settings(), previous)
        self.assertFalse(self.service.settings.launch_at_login)

    def test_startup_requires_consent_and_uses_injected_entry(self):
        with self.assertRaises(ValueError):
            self.service.set_login_start(True)
        self.startup.set_enabled.assert_not_called()
        self.service.set_login_start(True, consent=STARTUP_CONSENT)
        self.startup.set_enabled.assert_called_once_with(True)
        self.assertTrue(self.store.settings().launch_at_login)


if __name__ == '__main__':
    unittest.main()
