from pathlib import Path
import json
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch,Mock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lite_bridge import Coordinator,Bridge,PreviewStartup,startup_registration
from storage import Store,SingleInstance
from core import Settings,ManualLocation,now_cn
from profile_import import inspect_profile,import_profile,PROFILE_FILES


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.coordinator=Coordinator(Path(self.temp.name)/'data',testing=True)
        self.bridge=Bridge(self.coordinator)

    def tearDown(self):
        self.coordinator.quit()
        self.temp.cleanup()

    def accept(self):
        self.assertTrue(self.bridge.consent()['ok'])

    def test_disclaimer_prevents_service_creation_and_actions(self):
        self.assertIsNone(self.coordinator.service)
        self.assertFalse(self.bridge.state()['accepted'])
        self.assertFalse(self.bridge.action('manual')['ok'])
        self.assertIsNone(self.coordinator.service)

    def test_instance_lock_can_be_held_before_consent_then_transferred_to_service(self):
        root=Path(self.temp.name)/'early-lock'
        instance=SingleInstance(root)
        other=Coordinator(root,testing=True,instance=instance)
        try:
            with self.assertRaises(OSError):SingleInstance(root)
            other.consent()
            self.assertIs(other.service.instance,instance)
        finally:
            other.quit()
        replacement=SingleInstance(root)
        replacement.close()

    def test_bridge_exposes_no_store_or_arbitrary_execution(self):
        names={name for name in dir(self.bridge) if not name.startswith('_')}
        self.assertEqual(names,{'state','consent','begin_panel','close_panel','save_panel',
            'action','choose_import','confirm_import','window_action'})

    def test_passwords_are_not_returned_in_state_or_panels(self):
        self.accept()
        self.bridge.begin_panel('account')
        self.assertTrue(self.bridge.save_panel('account',{'username':'fixture','password':'never-echo-secret'})['ok'])
        panel=self.bridge.begin_panel('account')
        self.assertNotIn('password',panel)
        self.assertNotIn('never-echo-secret',json.dumps(self.bridge.state()))
        self.bridge.close_panel('account')
        self.bridge.begin_panel('network')
        self.bridge.save_panel('network',{'enabled':False,'username':'network','password':'never-echo-network','carrier':'campus'})
        panel=self.bridge.begin_panel('network')
        self.assertNotIn('password',panel)
        self.assertNotIn('never-echo-network',json.dumps(panel))

    def test_saving_requires_current_panel(self):
        self.accept()
        self.assertFalse(self.bridge.save_panel('account',{'username':'fixture','password':'x'})['ok'])
        self.assertIsNone(self.coordinator.store.credentials())

    def test_settings_edit_closes_and_stale_close_does_not_close_new_panel(self):
        self.accept()
        self.bridge.begin_panel('account')
        self.assertEqual(self.coordinator.service.edit_depth,1)
        self.bridge.close_panel('account')
        self.bridge.begin_panel('schedule')
        self.bridge.close_panel('account')
        self.assertEqual(self.coordinator.panel,'schedule')
        self.assertEqual(self.coordinator.service.edit_depth,1)
        self.bridge.close_panel('schedule')
        self.assertEqual(self.coordinator.service.edit_depth,0)

    def test_invalid_settings_keep_panel_open_without_writing(self):
        self.accept()
        self.bridge.begin_panel('schedule')
        self.assertFalse(self.bridge.save_panel('schedule',{'start':'23:00','end':'20:00','retry':5})['ok'])
        self.assertEqual(self.coordinator.panel,'schedule')
        self.assertEqual(self.coordinator.store.settings().start,'21:00')

    def test_location_formats_and_network_account_are_independent(self):
        self.accept()
        self.bridge.begin_panel('location')
        self.assertTrue(self.bridge.save_panel('location',{'mode':'manual','latitude':'30°15′30″','longitude':'120°30′30″'})['ok'])
        self.assertAlmostEqual(self.coordinator.store.manual_location().latitude,30+15/60+30/3600)
        self.bridge.begin_panel('network')
        self.bridge.save_panel('network',{'enabled':True,'username':'network','password':'synthetic','carrier':'cmcc'})
        self.assertIsNone(self.coordinator.store.credentials())
        self.assertTrue(self.coordinator.store.settings().campus_network_enabled)

    def test_startup_uses_current_lite_entry_and_explicit_directory(self):
        registration=startup_registration(self.coordinator.directory)
        self.assertIn('lite_app.py',registration.command)
        self.assertIn('--data-dir',registration.command)
        self.assertIn('--startup',registration.command)
        self.assertNotIn('password',registration.command)

    def test_startup_confirmation_and_cancel(self):
        self.accept()
        self.bridge.begin_panel('startup')
        self.bridge.close_panel('startup')
        self.assertFalse(self.coordinator.registration.is_enabled())
        self.bridge.begin_panel('startup')
        self.assertTrue(self.bridge.save_panel('startup',{})['ok'])
        self.assertTrue(self.coordinator.registration.is_enabled())
        self.bridge.action('disable_startup')
        self.assertFalse(self.coordinator.registration.is_enabled())

    def test_paused_state_shows_base_time_not_stale_next_attempt(self):
        self.accept()
        self.coordinator.service.next_attempt=now_cn().replace(hour=22,minute=42)
        self.assertEqual(self.bridge.state()['next_time'],'21:00')

    def test_new_attention_still_restores_window_after_log_limit(self):
        self.accept()
        self.coordinator.native=Mock()
        first={'time':'12:00','message':'needs attention','level':'attention'}
        self.coordinator.service.records=[first]+[{'time':'12:00','message':'ok','level':'info'} for _ in range(599)]
        self.coordinator.tick()
        self.coordinator.tick()
        self.assertEqual(self.coordinator.native.show.call_count,1)
        self.coordinator.service.records=[dict(first)]+self.coordinator.service.records[1:]
        self.coordinator.tick()
        self.assertEqual(self.coordinator.native.show.call_count,2)
        self.coordinator.native=None

    def test_saved_completion_is_loaded_for_the_main_screen(self):
        self.coordinator.store.save_credentials('fixture','synthetic')
        self.coordinator.store.mark_done(now_cn().date().isoformat())
        self.accept()
        self.assertTrue(self.bridge.state()['completed_today'])

    def test_import_picker_and_confirm_reload_account_and_pause_timer(self):
        self.accept()
        old=Store(Path(self.temp.name)/'old-data')
        old.save_credentials('legacy-fixture','legacy-synthetic-secret')
        old.save_settings(Settings(start='20:45',auto_start=True))
        self.coordinator.window=Mock()
        self.coordinator.window.create_file_dialog.return_value=[str(old.root)]
        selection=self.bridge.choose_import()
        self.assertTrue(selection['ok'])
        self.assertTrue(self.bridge.confirm_import()['ok'])
        self.assertEqual(self.bridge.state()['username'],'legacy-fixture')
        self.assertEqual(self.bridge.state()['settings']['start'],'20:45')
        self.assertFalse(self.bridge.state()['enabled'])
        self.assertEqual(self.coordinator.service.edit_depth,0)
        self.assertNotIn('legacy-synthetic-secret',json.dumps(selection))


class ProfileImportTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.source=Store(self.root/'old')
        self.target=Store(self.root/'new')
        self.source.save_credentials('old-user','synthetic-old-secret')
        self.source.save_settings(Settings(start='20:30',auto_start=True,location_mode='manual',launch_at_login=True))
        self.source.save_manual_location(ManualLocation(30,110))
        self.source.save_secret('session.dat',{'cookies':[],'origins':[]})
        self.source.schedule_delay('2026-11-04',create=True)
        self.target.save_credentials('new-user','synthetic-new-secret')

    def tearDown(self):
        self.temp.cleanup()

    def bytes(self,root):
        return {name:(root/name).read_bytes() for name in PROFILE_FILES if (root/name).is_file()}

    def test_import_keeps_encrypted_data_and_saved_draw_and_pauses_timer(self):
        before=self.bytes(self.source.root)
        previous_target=self.bytes(self.target.root)
        result=import_profile(self.source.root,self.target.root)
        self.assertEqual(self.target.credentials()['username'],'old-user')
        self.assertEqual(self.target.settings().start,'20:30')
        self.assertFalse(self.target.settings().auto_start)
        self.assertFalse(self.target.settings().launch_at_login)
        self.assertEqual(self.target.schedule_delay('2026-11-04'),self.source.schedule_delay('2026-11-04'))
        self.assertEqual(self.bytes(self.source.root),before)
        self.assertEqual((self.target.root/result['backup']/'credentials.dat').read_bytes(),
            previous_target['credentials.dat'])
        self.assertNotIn(b'synthetic-old-secret',(self.target.root/'credentials.dat').read_bytes())

    def test_corrupt_source_never_changes_target(self):
        old=self.bytes(self.target.root)
        (self.source.root/'location.dat').write_bytes(b'corrupt')
        with self.assertRaises(ValueError):import_profile(self.source.root,self.target.root)
        self.assertEqual(self.bytes(self.target.root),old)

    def test_import_preserves_target_startup_preference(self):
        self.target.save_settings(Settings(launch_at_login=True,startup_consent='target-consent'))
        import_profile(self.source.root,self.target.root)
        self.assertTrue(self.target.settings().launch_at_login)
        self.assertEqual(self.target.settings().startup_consent,'target-consent')

    def test_write_failure_rolls_back_previous_files(self):
        import profile_import
        before=self.bytes(self.target.root)
        actual=profile_import.atomic_write
        failed=False
        def write(path,data):
            nonlocal failed
            if path.parent==self.target.root and path.name=='location.dat' and not failed:
                failed=True
                raise OSError('synthetic failure')
            return actual(path,data)
        with patch('profile_import.atomic_write',side_effect=write):
            with self.assertRaises(OSError):import_profile(self.source.root,self.target.root)
        self.assertEqual(self.bytes(self.target.root),before)

    def test_same_directory_is_rejected(self):
        with self.assertRaises(ValueError):import_profile(self.source.root,self.source.root)


if __name__=='__main__':unittest.main()
