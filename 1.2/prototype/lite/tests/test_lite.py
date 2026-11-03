import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from main import API, Bridge, local_checks
from cdp_probe import CDP, ProbeCancelled, browser_connection
import websocket


class LiteTests(unittest.TestCase):
    def test_original_encryption_account_isolation_and_scheduling(self):
        self.assertTrue(all(local_checks().values()))

    def test_settings_persist_without_revealing_secret_in_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = API(root)
            secret = 'synthetic-secret-for-test'
            self.assertTrue(app.save_settings('测试名称', secret)['ok'])
            self.assertEqual(API(root).state()['name'], '测试名称')
            self.assertNotIn(secret, json.dumps(app.state(), ensure_ascii=False))
            self.assertNotIn(secret.encode(), (root/'validation-data/validation-settings.dat').read_bytes())

    def test_invalid_settings_do_not_create_file(self):
        with tempfile.TemporaryDirectory() as directory:
            app = API(Path(directory))
            for name, note in [('', 'x'), ('a'*33, ''), ('ok', 'x'*161)]:
                self.assertFalse(app.save_settings(name, note)['ok'])
            self.assertFalse((app.store.root/'validation-settings.dat').exists())

    def test_bridge_does_not_expose_store_or_native_form(self):
        with tempfile.TemporaryDirectory() as directory:
            names = {n for n in dir(Bridge(API(Path(directory)))) if not n.startswith('_')}
            self.assertEqual(names, {'state', 'save_settings', 'run_validation',
                'stop_validation', 'toggle_timer', 'window_action'})

    def test_busy_lock_and_cancelled_worker_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            app = API(Path(directory))
            entered, release = threading.Event(), threading.Event()
            def fake_probe(**options):
                entered.set()
                release.wait(3)
                raise ProbeCancelled()
            with patch('main.run_probe', side_effect=fake_probe):
                self.assertTrue(app.run_validation()['ok'])
                self.assertTrue(entered.wait(2))
                worker = app.worker
                try:
                    self.assertFalse(app.run_validation()['ok'])
                    self.assertTrue(app.state()['busy'])
                    app.stop_validation()
                finally:
                    release.set()
                    worker.join(3)
                self.assertIsNone(app.worker)
                self.assertEqual(app.status, '验证已停止')

    def test_cdp_wait_is_cancelable(self):
        client = object.__new__(CDP)
        client.stop = threading.Event()
        client.stop.set()
        with self.assertRaises(ProbeCancelled):
            client.call('Runtime.enable')

    def test_cdp_event_timeout_is_bounded(self):
        from collections import deque
        from unittest.mock import Mock
        client = object.__new__(CDP)
        client.stop = threading.Event()
        client.events, client.replies = deque(), {}
        client.ws = Mock()
        client.ws.recv.side_effect = websocket.WebSocketTimeoutException()
        with self.assertRaises(TimeoutError):
            client.event('NeverArrives', timeout=0.02)


if __name__ == '__main__':
    unittest.main()
