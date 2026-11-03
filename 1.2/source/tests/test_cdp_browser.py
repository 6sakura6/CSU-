"""Run the legacy workflow matrix through the real CDP adapter in local fixtures."""
from pathlib import Path
import json
import sys
import threading
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import test_app as legacy_cases
from cdp_transport import BrowserProcess, CDPClient
from cdp_browser import BrowserContext, normalize_state
from core import Cancelled
from lite_browser import LiteBrowserRunner
from cdp_fixture import FixtureBrowser, FixtureRoutes


class CDPIntegrationTests(legacy_cases.BrowserIntegrationTests):
    @classmethod
    def setUpClass(cls):
        cls.process = BrowserProcess(threading.Event(), channel='edge', headless=True).start()
        cls.playwright = types.SimpleNamespace(devices={'Pixel 7': {}})
        cls.browser = FixtureBrowser(cls.process)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        assert cls.process.cleanup_ok

    def setUp(self):
        super().setUp()
        self.context.client.stop = self.stop

    def tearDown(self):
        self.context.client.stop = threading.Event()
        super().tearDown()

    def test_full_runner_cleanup_and_compatible_saved_session(self):
        self.store.save_credentials('offline-account', 'fixture-password')
        runner = LiteBrowserRunner(self.store, self.runner.settings, self.runner.log, self.stop,
            channel='edge', headless=True, fixture=self.context.fixture)
        result = runner.run()
        self.assertTrue(result.completed, result)
        self.assertTrue(runner.last_cleanup_ok)
        saved = self.store.load_secret('session.dat')
        self.assertEqual(set(saved), {'cookies', 'origins'})
        self.assertNotIn(b'fixture-password', (self.store.root/'session.dat').read_bytes())

    def test_session_local_storage_restores_once_and_keeps_new_tokens(self):
        self.context.close()
        saved = {'cookies': [{'name': 'fixture-cookie', 'value': 'old', 'domain': 'zhxg.csu.edu.cn',
            'path': '/', 'expires': -1, 'secure': True, 'httpOnly': True, 'sameSite': 'Lax'}],
            'origins': [{'origin': 'https://zhxg.csu.edu.cn', 'localStorage': [{'name': 'session-token', 'value': 'old-token'}]}]}
        fixture = FixtureRoutes()
        fixture.routes.append(('**/*', lambda route: route.fulfill(body='<title>fixture</title>', content_type='text/html')))
        self.context = BrowserContext(self.process.client, saved, fixture=fixture)
        first = self.context.new_page()
        first.goto('https://zhxg.csu.edu.cn/fixture')
        self.assertEqual(first.evaluate('localStorage.getItem("session-token")'), 'old-token')
        first.evaluate('localStorage.setItem("session-token","new-token")')
        second = self.context.new_page()
        second.goto('https://zhxg.csu.edu.cn/fixture2')
        self.assertEqual(second.evaluate('localStorage.getItem("session-token")'), 'new-token')
        state = self.context.storage_state()
        self.assertTrue(any(cookie['name'] == 'fixture-cookie' for cookie in state['cookies']))
        values = next(origin['localStorage'] for origin in state['origins'] if origin['origin'] == 'https://zhxg.csu.edu.cn')
        self.assertIn({'name': 'session-token', 'value': 'new-token'}, values)

    def test_redirect_before_sensitive_fill_never_receives_password(self):
        from browser_flow import safe_login_url
        self.context.credentials_guard = safe_login_url
        page = self.login_fixture()
        page.goto('https://zhxg.csu.edu.cn/znzhxgpt_h5/')
        from core import AppError
        with self.assertRaises(AppError):
            page.locator('#password').fill('never-sent')

    def test_client_redirect_before_dom_ready_reaches_login_and_fills_credentials(self):
        page = self.login_fixture()
        self.context.route('https://zhxg.csu.edu.cn/redirect-before-dom',lambda route:
            route.fulfill(body='''<!doctype html><script>
                location.replace('https://ca.csu.edu.cn/authserver/login');
                </script>''',content_type='text/html'))
        page.goto('https://zhxg.csu.edu.cn/redirect-before-dom',timeout=2500)
        self.assertEqual(page.evaluate('location.hostname'),'ca.csu.edu.cn')
        self.runner.login(page)
        self.assertTrue(self.runner.logged_in)
        self.assertEqual(self.login_attempts,1)

    def test_cancel_interrupts_pending_javascript(self):
        page = self.context.new_page()
        timer = threading.Timer(0.2, self.stop.set)
        timer.start()
        try:
            with self.assertRaises(Cancelled):
                page.evaluate('new Promise(()=>{})')
        finally:
            timer.cancel()


class SessionValidationTests(unittest.TestCase):
    def test_external_domains_and_origins_are_filtered(self):
        result = normalize_state({'cookies': [{'name': 'x', 'value': 'y', 'domain': '.example.com', 'path': '/'}],
            'origins': [{'origin': 'https://example.com', 'localStorage': []}]})
        self.assertEqual(result, {'cookies': [], 'origins': []})

    def test_invalid_session_structures_are_rejected(self):
        for state in ([], {'cookies': 'not-list'}, {'cookies': [1]},
            {'origins': [{'origin': 'https://zhxg.csu.edu.cn', 'localStorage': [1]}]}):
            with self.subTest(state=state), self.assertRaises(ValueError):
                normalize_state(state)

    def test_saved_session_keeps_legacy_required_cookie_fields(self):
        state = normalize_state({'cookies': [{'name': 'x', 'value': 'y', 'domain': '.csu.edu.cn', 'path': '/'}]})
        cookie = state['cookies'][0]
        self.assertEqual(cookie['expires'], -1)
        self.assertEqual(set(cookie), {'name', 'value', 'domain', 'path', 'expires', 'secure', 'httpOnly', 'sameSite'})

    def test_already_cancelled_driver_never_launches_a_browser(self):
        stop = threading.Event()
        stop.set()
        with patch('cdp_transport.subprocess.Popen') as launch:
            with self.assertRaises(Cancelled):
                BrowserProcess(stop).start()
            launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
