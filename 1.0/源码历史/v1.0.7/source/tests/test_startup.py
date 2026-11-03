"""Logon registration checks use an isolated test key, never the Windows Run key."""
from dataclasses import replace
from datetime import datetime
from pathlib import Path
import sys
import tempfile
import tkinter as tk
import unittest
import uuid
import winreg
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
from app import App, STARTUP_CONSENT, DISCLAIMER_TEXT
from core import Settings, SHANGHAI
from startup import StartupRegistration, startup_command, startup_is_authorized
from storage import Store
from test_tray import FakeTray


class FakeStartup:
    def __init__(self):
        self.enabled = False
        self.fail = False
        self.writes = []

    def is_enabled(self):
        return self.enabled

    def set_enabled(self, enabled):
        self.writes.append(enabled)
        if self.fail:
            raise PermissionError('offline simulated registry denial')
        self.enabled = enabled


class StartupRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.key = r'Software\CSUPeaceCheckinTest_' + uuid.uuid4().hex
        self.registration = StartupRegistration(Path(tempfile.gettempdir()) / 'offline-startup-test',
                                               '"C:\\Example Program\\helper.exe" --startup', self.key)

    def tearDown(self):
        assert self.key.startswith('Software\\CSUPeaceCheckinTest_')
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, self.key)
        except FileNotFoundError:
            pass

    def test_register_and_remove_only_own_value(self):
        self.assertFalse(self.registration.is_enabled())
        self.registration.set_enabled(True)
        self.registration.set_enabled(True)
        self.assertTrue(self.registration.is_enabled())
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.key, 0, winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE) as key:
            winreg.SetValueEx(key, 'unrelated', 0, winreg.REG_SZ, 'keep this value')
            command, kind = winreg.QueryValueEx(key, self.registration.value_name)
            self.assertEqual(command, self.registration.command)
            self.assertEqual(kind, winreg.REG_SZ)
        self.registration.set_enabled(False)
        self.registration.set_enabled(False)
        self.assertFalse(self.registration.is_enabled())
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.key) as key:
            self.assertEqual(winreg.QueryValueEx(key, 'unrelated')[0], 'keep this value')

    def test_reject_command_over_windows_limit(self):
        self.registration.command = 'x' * 261
        with self.assertRaises(ValueError):
            self.registration.set_enabled(True)
        self.assertFalse(self.registration.is_enabled())

    def test_frozen_command_quotes_executable_path(self):
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', r'C:\Example Program\校园助手.exe'):
            command = startup_command()
        self.assertEqual(command, '"C:\\Example Program\\校园助手.exe" --startup')

    def test_authorization_needs_option_consent_and_registration(self):
        settings = Settings(launch_at_login=True, startup_consent=STARTUP_CONSENT)
        registration = FakeStartup()
        self.assertFalse(startup_is_authorized(settings, registration, STARTUP_CONSENT))
        registration.enabled = True
        self.assertTrue(startup_is_authorized(settings, registration, STARTUP_CONSENT))
        self.assertFalse(startup_is_authorized(replace(settings, startup_consent='old'), registration, STARTUP_CONSENT))
        self.assertFalse(startup_is_authorized(replace(settings, launch_at_login=False), registration, STARTUP_CONSENT))


class StartupUiFixture:
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.store = Store(Path(self.directory.name))
        self.registration = FakeStartup()
        self.application = App(self.root, self.store, tray_factory=FakeTray, startup_factory=lambda: self.registration)
        self.root.update_idletasks()

    def tearDown(self):
        for timer in self.root.tk.splitlist(self.root.tk.call('after', 'info')):
            self.root.after_cancel(timer)
        self.application.finish_close()
        for handler in list(self.application.logger.handlers):
            handler.close()
            self.application.logger.removeHandler(handler)
        self.directory.cleanup()


class StartupUiTests(StartupUiFixture, unittest.TestCase):
    def test_opt_in_survives_save_and_timer_stop(self):
        self.assertFalse(self.application.login_start.get())
        with patch('app.messagebox.askokcancel', return_value=True):
            self.application.login_start_button.invoke()
        self.assertTrue(self.registration.enabled)
        self.assertEqual(self.store.settings().startup_consent, STARTUP_CONSENT)
        self.application.username.set('offline-startup-account')
        self.application.password.set('offline-startup-password')
        self.assertTrue(self.application.save(quiet=True))
        self.application.start_schedule()
        self.application.stop_schedule()
        self.assertTrue(self.store.settings().launch_at_login)
        self.assertFalse(self.store.settings().auto_start)
        self.assertTrue(self.registration.enabled)

    def test_cancel_does_not_register_or_save_consent(self):
        with patch('app.messagebox.askokcancel', return_value=False):
            self.application.login_start_button.invoke()
        self.assertFalse(self.application.login_start.get())
        self.assertEqual(self.registration.writes, [])
        self.assertFalse(self.store.settings().launch_at_login)
        self.assertEqual(self.store.settings().startup_consent, '')

    def test_uncheck_removes_registration_preserving_timer_preference(self):
        self.registration.enabled = True
        self.application.settings = Settings(auto_start=True, launch_at_login=True, startup_consent=STARTUP_CONSENT)
        self.store.save_settings(self.application.settings)
        self.application.login_start.set(True)
        with patch('app.messagebox.askokcancel') as confirmation:
            self.application.login_start_button.invoke()
            confirmation.assert_not_called()
        self.assertFalse(self.registration.enabled)
        self.assertFalse(self.store.settings().launch_at_login)
        self.assertTrue(self.store.settings().auto_start)

    def test_failed_registration_rolls_back_local_preference(self):
        self.registration.fail = True
        with patch('app.messagebox.askokcancel', return_value=True), patch('app.messagebox.showerror') as error:
            self.application.login_start_button.invoke()
            error.assert_called_once()
        self.assertFalse(self.registration.enabled)
        self.assertFalse(self.store.settings().launch_at_login)
        self.assertFalse(self.application.login_start.get())

    def test_write_denial_does_not_claim_missing_entry_enabled(self):
        self.application.settings = Settings(launch_at_login=True, startup_consent=STARTUP_CONSENT)
        self.store.save_settings(self.application.settings)
        self.registration.fail = True
        with patch('app.messagebox.askokcancel', return_value=True), patch('app.messagebox.showerror'):
            self.application.login_start_button.invoke()
        self.assertFalse(self.application.login_start.get())
        self.assertFalse(self.registration.enabled)


class LoginRuntimeTests(StartupUiFixture, unittest.TestCase):
    # These focused checks create a new instance with saved offline preferences.
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = tk.Tk()
        self.store = Store(Path(self.directory.name))
        self.registration = FakeStartup()

    def rebuild(self, resume, account=True):
        if account:
            self.store.save_credentials('offline-login-account', 'offline-login-password')
        self.store.save_settings(Settings(auto_start=resume, launch_at_login=True, startup_consent=STARTUP_CONSENT))
        self.registration.enabled = True
        self.application = App(self.root, self.store, tray_factory=FakeTray, startup_factory=lambda: self.registration)

    def test_logon_hides_and_resumes_enabled_timer(self):
        self.rebuild(True)
        self.application.start_from_login()
        with patch.object(self.application, 'launch'), patch('app.now_cn', return_value=datetime(2026, 10, 18, 12, tzinfo=SHANGHAI)):
            self.root.after(850, self.root.quit)
            self.root.mainloop()
        self.assertTrue(self.application.hidden)
        self.assertTrue(self.application.enabled)
        self.assertTrue(self.application.tray.running)
        self.assertIn(DISCLAIMER_TEXT, self.application.tray.notifications[0])

    def test_logon_keeps_previously_stopped_timer_off(self):
        self.rebuild(False)
        self.application.start_from_login()
        self.root.after(150, self.root.quit)
        self.root.mainloop()
        self.assertTrue(self.application.hidden)
        self.assertFalse(self.application.enabled)

    def test_missing_account_restores_window(self):
        self.rebuild(True, account=False)
        self.application.start_from_login()
        self.assertFalse(self.application.hidden)
        self.assertFalse(self.application.enabled)
        self.assertIn('账户不可用', self.application.status.get())


class StartupMainTests(unittest.TestCase):
    def test_authorized_logon_uses_background_start_without_modal(self):
        with patch.object(sys, 'argv', ['app', '--startup']), patch('app.Store') as store, \
                patch('app.StartupRegistration') as registration, patch('app.tk.Tk') as root, \
                patch('app.SingleInstance.is_running', return_value=False), patch('app.App') as application, \
                patch('app.show_disclaimer') as disclaimer:
            store.return_value.settings.return_value = Settings(launch_at_login=True, startup_consent=STARTUP_CONSENT)
            registration.return_value.is_enabled.return_value = True
            self.assertEqual(app.main(), 0)
            disclaimer.assert_not_called()
            application.return_value.start_from_login.assert_called_once()
            root.return_value.mainloop.assert_called_once()

    def test_manual_launch_still_waits_for_disclaimer(self):
        with patch.object(sys, 'argv', ['app']), patch('app.SingleInstance.signal', return_value=False), \
                patch('app.tk.Tk') as root, patch('app.show_disclaimer', return_value=False) as disclaimer, \
                patch('app.App') as application:
            self.assertEqual(app.main(), 0)
            disclaimer.assert_called_once()
            application.assert_not_called()
            root.return_value.destroy.assert_called_once()

    def test_disabled_startup_exits_without_opening_ui(self):
        with patch.object(sys, 'argv', ['app', '--startup']), patch('app.Store') as store, \
                patch('app.StartupRegistration') as registration, patch('app.tk.Tk') as root:
            store.return_value.settings.return_value = Settings()
            self.assertEqual(app.main(), 0)
            root.assert_not_called()

    def test_duplicate_startup_leaves_existing_window_hidden(self):
        with patch.object(sys, 'argv', ['app', '--startup']), patch('app.Store') as store, \
                patch('app.StartupRegistration') as registration, patch('app.tk.Tk') as root, \
                patch('app.SingleInstance.is_running', return_value=True), patch('app.SingleInstance.signal') as signal:
            store.return_value.settings.return_value = Settings(launch_at_login=True, startup_consent=STARTUP_CONSENT)
            registration.return_value.is_enabled.return_value = True
            self.assertEqual(app.main(), 0)
            root.assert_not_called()
            signal.assert_not_called()
