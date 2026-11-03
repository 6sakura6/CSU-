"""Offline portal fixtures; never send a credential to a live campus service."""
from dataclasses import asdict
import json
from pathlib import Path
import ssl
import sys
import tempfile
import threading
import unittest
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from campus_network import (CampusNetwork, NetworkCredentials, parse_portal_response,
                            SCHOOL_URL, STATUS_URL, LOGIN_URL, NoRedirect)
from core import Settings
from storage import Store


class CredentialTests(unittest.TestCase):
    def test_each_carrier_and_no_carrier(self):
        for carrier, expected in (("campus", "student"), ("cmcc", "student@cmccn"),
                                  ("unicom", "student@unicomn"), ("telecom", "student@telecomn")):
            self.assertEqual(NetworkCredentials(" student ", "secret", carrier).account(), expected)
        # No-carrier mode never edits the original account, even if it contains @.
        self.assertEqual(NetworkCredentials("staff@example", "secret", "campus").account(), "staff@example")

    def test_existing_suffix_not_duplicated_or_silently_replaced(self):
        self.assertEqual(NetworkCredentials("student@cmccn", "secret", "cmcc").account(), "student@cmccn")
        for account, carrier in (("student@telecomn", "cmcc"), ("student@custom", "unicom"),
                                  ("@cmccn", "cmcc"), ("", "campus"), ("student\n", "campus")):
            # Trailing whitespace is deliberately stripped from user-entered accounts.
            if account == "student\n":
                account = "stu\ndent"
            with self.subTest(account=account), self.assertRaises(ValueError):
                NetworkCredentials(account, "secret", carrier).account()
        for carrier, password in (("", "secret"), ("unknown", "secret"), ("campus", "")):
            with self.assertRaises(ValueError):
                NetworkCredentials("student", password, carrier).account()

    def test_jsonp_is_data_only(self):
        for value in ('{"result":1}', 'csuStatus({"result":0});', '({"result":"1"})'):
            self.assertIn(str(parse_portal_response(value)["result"]), ("0", "1"))
        for value in ('<html>Login</html>', '[]', '{"result":true}', '{"result":2}',
                      'evil();csuStatus({"result":1});', '{"msg":"success"}'):
            with self.assertRaises(ValueError):
                parse_portal_response(value)

    def test_settings_are_opt_in_and_strict(self):
        self.assertFalse(Settings().campus_network_enabled)
        with self.assertRaises(ValueError):
            Settings(campus_network_enabled="false").validate()


class NetworkFlowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name))
        self.secret = "p &+=?#密码"
        self.store.save_secret("network.dat", asdict(NetworkCredentials("student", self.secret, "campus")))
        self.logs = []
        self.network = CampusNetwork(self.store, self.logs.append)
        self.stop = threading.Event()

    def tearDown(self):
        self.directory.cleanup()

    def run_flow(self, reachable, responses):
        with patch.object(self.network, 'school_reachable', side_effect=reachable), \
             patch.object(self.network, '_get', side_effect=responses) as request:
            result = self.network.ensure_online(self.stop)
        return result, request

    def test_already_reachable_does_not_read_password_or_authenticate(self):
        with patch.object(self.store, 'load_secret', side_effect=AssertionError("Must not read secret")):
            result, request = self.run_flow([True], [])
        self.assertEqual(result.code, 'campus_ready')
        self.assertFalse(result.completed)
        request.assert_not_called()
        self.assertIn('未验证校园网密码', result.message)

    def test_offline_authentication_encodes_all_four_account_types(self):
        for carrier, account in (("campus", "student"), ("cmcc", "student@cmccn"),
                                  ("unicom", "student@unicomn"), ("telecom", "student@telecomn")):
            self.store.save_secret('network.dat', asdict(NetworkCredentials('student', self.secret, carrier)))
            result, request = self.run_flow([False, True], [(200, 'csuStatus({"result":0});', ''),
                                                          (200, '{"result":1}', '')])
            self.assertEqual(result.code, 'campus_ready')
            self.assertEqual(request.call_args_list[0].args[0], STATUS_URL)
            url = request.call_args_list[1].args[0]
            self.assertTrue(url.startswith(LOGIN_URL + '?'))
            self.assertEqual(parse_qs(urlsplit(url).query), {'user_account': [account], 'user_password': [self.secret]})
            self.assertNotIn(self.secret, repr(self.logs) + result.message)

    def test_untrusted_redirect_html_and_tls_failure_never_send_credentials(self):
        for responses in ([(302, '', 'https://other.example/')], [(200, '<html>Login</html>', '')],
                          [URLError(ssl.SSLCertVerificationError('bad certificate'))]):
            result, request = self.run_flow([False], responses)
            self.assertNotEqual(result.code, 'campus_ready')
            self.assertEqual(request.call_count, 1)

    def test_portal_online_but_school_down_does_not_reauthenticate(self):
        result, request = self.run_flow([False], [(200, '({"result":1})', '')])
        self.assertEqual(result.code, 'campus_service')
        self.assertEqual(request.call_count, 1)

    def test_rejected_credentials_are_not_repeated_until_settings_reset(self):
        result, request = self.run_flow([False], [(200, '{"result":0}', ''),
                                                (200, json.dumps({'result': 0, 'msg': self.secret}), '')])
        self.assertEqual(result.code, 'campus_rejected')
        self.assertEqual(request.call_count, 2)
        result, request = self.run_flow([False], [(200, '{"result":0}', '')])
        self.assertEqual(result.code, 'campus_rejected')
        self.assertEqual(request.call_count, 1)
        self.assertNotIn(self.secret, repr(self.logs) + result.message)
        self.network.reset()
        result, _ = self.run_flow([False, True], [(200, '{"result":0}', ''), (200, '{"result":1}', '')])
        self.assertEqual(result.code, 'campus_ready')

    def test_login_success_requires_reachable_school(self):
        with patch.object(self.stop, 'wait', return_value=False):
            result, _ = self.run_flow([False, False, False, False], [(200, '{"result":0}', ''), (200, '{"result":1}', '')])
        self.assertEqual(result.code, 'campus_service')
        self.assertFalse(result.completed)

    def test_missing_corrupt_or_mismatched_secret_never_logs_in(self):
        for saved in (None, {'username': 'student', 'password': self.secret, 'carrier': 'unknown'},
                      asdict(NetworkCredentials('student@telecomn', self.secret, 'cmcc'))):
            self.store.save_secret('network.dat', saved)
            result, request = self.run_flow([False], [(200, '{"result":0}', '')])
            self.assertEqual(result.code, 'campus_config')
            self.assertEqual(request.call_count, 1)
        (self.store.root / 'network.dat').write_bytes(b'corrupt')
        result, request = self.run_flow([False], [(200, '{"result":0}', '')])
        self.assertEqual(result.code, 'campus_config')
        self.assertEqual(request.call_count, 1)

    def test_stop_before_and_between_requests(self):
        self.stop.set()
        result, request = self.run_flow([], [])
        self.assertEqual(result.code, 'cancelled')
        request.assert_not_called()
        self.stop.clear()
        def status_and_stop(url):
            self.stop.set()
            return 200, '{"result":0}', ''
        with patch.object(self.network, 'school_reachable', return_value=False), \
             patch.object(self.network, '_get', side_effect=status_and_stop) as request:
            result = self.network.ensure_online(self.stop)
        self.assertEqual(result.code, 'cancelled')
        self.assertEqual(request.call_count, 1)

    def test_exception_does_not_expose_password_url(self):
        result, _ = self.run_flow([False], [(200, '{"result":0}', ''), URLError('user_password=' + self.secret)])
        self.assertEqual(result.code, 'campus_network')
        self.assertNotIn(self.secret, repr(self.logs) + result.message)

    def test_network_secret_is_encrypted_and_separate(self):
        self.store.save_credentials('checkin-account', 'checkin-password')
        raw = (self.store.root / 'network.dat').read_bytes()
        self.assertNotIn(self.secret.encode(), raw)
        self.assertNotIn(b'student', raw)
        self.assertEqual(self.store.credentials()['username'], 'checkin-account')
        self.assertEqual(self.store.load_secret('network.dat')['password'], self.secret)

    def test_school_redirect_scope(self):
        for target, expected in (('https://ca.csu.edu.cn/authserver/login', True),
                                 ('https://ca.csu.edu.cn.evil.example/', False),
                                 ('http://ca.csu.edu.cn/', False), ('https://evil@ca.csu.edu.cn/', False),
                                 ('https://ca.csu.edu.cn:802/', False)):
            with patch.object(self.network, '_get', return_value=(302, '', target)):
                self.assertEqual(self.network.school_reachable(), expected)

    def test_transport_disables_proxies_redirects_and_uses_verified_tls(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = b'{"result":0}'
        response.headers = {}
        with patch('campus_network.build_opener') as build:
            build.return_value.open.return_value = response
            self.network._get(STATUS_URL)
        handlers = build.call_args.args
        self.assertEqual(handlers[0].proxies, {})
        self.assertEqual(handlers[1]._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(handlers[1]._context.check_hostname)
        self.assertIsInstance(handlers[2], NoRedirect)
        build.return_value.open.assert_called_once_with(STATUS_URL, timeout=5)
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.example/'))


if __name__ == '__main__':
    unittest.main()
