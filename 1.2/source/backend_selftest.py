"""End-to-end service + CDP validation with synthetic, fully intercepted pages."""
from dataclasses import asdict
from pathlib import Path
import tempfile
import time

from core import Settings, now_cn
from storage import Store
from service import CheckinService
from lite_browser import LiteBrowserRunner
from offline_school import OfflineSchool


def run_backend_validation(channel='edge'):
    fixture = OfflineSchool()
    instances = []
    with tempfile.TemporaryDirectory(prefix='CSU_Lite_Backend_Check_') as directory:
        store = Store(Path(directory))
        store.save_credentials('fixture-account', 'fixture-secret')
        store.save_manual_location(__import__('core').ManualLocation(30, 110))
        store.save_settings(Settings(start='00:00', end='23:59', location_mode='manual'))
        def factory(*args, **kwargs):
            runner = LiteBrowserRunner(*args, **kwargs, channel=channel, headless=True, fixture=fixture)
            instances.append(runner)
            return runner
        service = CheckinService(store, runner_factory=factory, awake=lambda _: None,
            automatic=False, restore=False)
        checks = {}
        try:
            def run(mode):
                if not service.launch(mode):
                    raise RuntimeError('Validation task could not start')
                deadline = time.monotonic() + 70
                while service.snapshot()['busy'] and time.monotonic() < deadline:
                    service.tick()
                    time.sleep(0.03)
                if service.snapshot()['busy']:
                    service.stop_current()
                    raise TimeoutError('Backend validation timeout')
                return service.last_result
            check = run('check')
            checks['dry_run_no_submission'] = check.code == 'checked' and fixture.submitted == 0 and not store.done(now_cn().date().isoformat())
            saved = store.load_secret('session.dat')
            checks['encrypted_compatible_session'] = isinstance(saved, dict) and set(saved) == {'cookies', 'origins'}
            checks['session_secret_not_plaintext'] = b'authenticated' not in (store.root/'session.dat').read_bytes()
            attempts = fixture.login_attempts
            fixture.authenticated = False  # Second browser must restore its encrypted session cookie.
            service.start_schedule()
            result = run('manual')
            checks['login_session_restored'] = fixture.login_attempts == attempts
            checks['submitted_and_freshly_verified'] = result.completed and result.code == 'success' and fixture.submitted == 1 and fixture.queried >= 3
            checks['manual_location_reached_page'] = fixture.submitted_position == {'wd': 30, 'jd': 110}
            checks['completion_persisted'] = store.done(now_cn().date().isoformat())
            checks['schedule_continues_tomorrow'] = service.enabled and service.next_attempt.date() > now_cn().date()
            already = run('manual')
            checks['already_done_never_resubmits'] = already.code == 'already_done' and fixture.submitted == 1
            checks['all_browser_profiles_cleaned'] = all(item.last_cleanup_ok for item in instances)
            return {'channel': channel, 'passed': all(checks.values()), 'checks': checks,
                'results': [asdict(item) for item in (check, result, already)],
                'requests_fulfilled_or_aborted_locally': fixture.intercepted,
                'external_school_requests_continued': 0,
                'scope': 'synthetic_fixtures_only', 'native_ui_required': False}
        finally:
            if not service.shutdown(timeout=25):
                raise RuntimeError('Validation task did not stop')
