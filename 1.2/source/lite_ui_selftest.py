"""Drive the actual local interface and business service against synthetic pages."""
from pathlib import Path
import json
import time
import threading


def run_ui_selftest(window, coordinator, capture=None):
    def await_js(expression):
        window.evaluate_js('window.__testResult=null;Promise.resolve('+expression+
            ').then(value=>window.__testResult={ok:true,value},error=>window.__testResult={ok:false});null')
        deadline = time.monotonic()+15
        while time.monotonic()<deadline:
            value = window.evaluate_js('window.__testResult')
            if value is not None:
                if not value.get('ok'):
                    raise RuntimeError('Interface bridge operation failed')
                return value.get('value')
            time.sleep(0.06)
        raise TimeoutError('Interface bridge timeout')

    def click(selector):
        window.evaluate_js('document.querySelector('+json.dumps(selector)+').click()')

    def wait_for(expression, timeout=15):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if window.evaluate_js(expression):
                return
            time.sleep(0.08)
        raise TimeoutError('Interface condition: '+expression)

    def wait_task(timeout=75):
        deadline=time.monotonic()+timeout
        while coordinator.service.worker is not None and time.monotonic()<deadline:
            time.sleep(0.08)
        if coordinator.service.worker:
            raise TimeoutError('Business task')
        await_js('window.liteRefresh()')

    checks={}
    wait_for('window.liteReady===true')
    checks['disclaimer_required'] = window.evaluate_js('document.querySelector("#disclaimer").open') and not coordinator.accepted
    click('#accept')
    wait_for('!document.querySelector("#disclaimer").open')
    checks['consent_creates_service'] = coordinator.service is not None
    for panel in ['account','schedule','location','network']:
        await_js('window.liteOpenPanel('+json.dumps(panel)+')')
        checks['panel_'+panel] = window.evaluate_js('document.querySelector("#settings-dialog").open') and coordinator.panel==panel
        if capture:
            time.sleep(0.25)
            coordinator.native.capture(str(Path(capture).with_name(panel+'.png')))
        if panel=='account':
            window.evaluate_js('document.querySelector("#username").value="fixture-account";document.querySelector("#password").value="fixture-secret"')
        elif panel=='schedule':
            window.evaluate_js('document.querySelector("#start-time").value="21:00";document.querySelector("#end-time").value="23:25";document.querySelector("#retry").value="5"')
        elif panel=='location':
            window.evaluate_js('document.querySelector("[name=location-mode][value=manual]").checked=true;document.querySelector("#latitude").value="30";document.querySelector("#longitude").value="110"')
        else:
            window.evaluate_js('document.querySelector("#network-enabled").checked=false;document.querySelector("#network-username").value="fixture-network";document.querySelector("#network-password").value="fixture-secret"')
        click('#save-panel')
        wait_for('!document.querySelector("#settings-dialog").open')
        time.sleep(0.1)
    checks['password_not_in_state'] = 'fixture-secret' not in json.dumps(coordinator.state())
    checks['saved_credentials'] = coordinator.store.credentials()['username']=='fixture-account'
    checks['saved_schedule'] = coordinator.store.settings().start=='21:00'
    checks['saved_manual_location'] = coordinator.store.manual_location().latitude==30
    click('#check')
    time.sleep(0.25)
    wait_task()
    checks['check_only'] = coordinator.service.last_result.code=='checked' and coordinator.fixture.submitted==0
    click('#schedule-toggle')
    wait_for('document.querySelector("#timer-toggle").getAttribute("aria-checked")==="true"')
    checks['schedule_starts'] = coordinator.service.enabled and coordinator.store.settings().auto_start
    click('#manual')
    time.sleep(0.25)
    wait_task()
    checks['manual_and_fresh_verification'] = coordinator.service.last_result.completed and coordinator.fixture.submitted==1
    click('#manual')
    time.sleep(0.25)
    wait_task()
    checks['repeat_skips_submission'] = coordinator.service.last_result.code=='already_done' and coordinator.fixture.submitted==1
    click('#schedule-toggle')
    wait_for('document.querySelector("#timer-toggle").getAttribute("aria-checked")==="false"')
    checks['schedule_pauses'] = not coordinator.service.enabled and not coordinator.store.settings().auto_start
    await_js('window.liteOpenPanel("network")')
    window.evaluate_js('document.querySelector("#network-enabled").checked=false')
    click('#network-test')
    wait_for('!document.querySelector("#settings-dialog").open')
    time.sleep(0.2)
    wait_task()
    checks['network_test_independent'] = coordinator.service.last_result.code=='campus_ready' and coordinator.fixture.submitted==1
    click('#startup-toggle')
    wait_for('document.querySelector("#settings-dialog").open')
    click('#save-panel')
    wait_for('!document.querySelector("#settings-dialog").open')
    checks['startup_enabled_with_consent'] = coordinator.registration.is_enabled()
    time.sleep(0.2)
    await_js('window.liteRefresh()')
    click('#startup-toggle')
    wait_for('document.querySelector("#startup-toggle").getAttribute("aria-checked")==="false"')
    checks['startup_disabled'] = not coordinator.registration.is_enabled()
    click('#show-logs')
    wait_for('document.querySelector("#logs-dialog").open')
    checks['logs_do_not_pause_scheduler'] = coordinator.service.edit_depth==0
    window.evaluate_js('document.querySelector("#logs-dialog").close()')
    # A cancelable fake task verifies the real Stop button without waiting for a remote server.
    previous=coordinator.service.runner_factory
    def gated(*items,**options):
        class Runner:
            def run(self,**_):
                from core import RunResult
                items[3].wait(8)
                return RunResult('cancelled','本次操作已停止。')
        return Runner()
    coordinator.service.runner_factory=gated
    click('#check')
    wait_for('!document.querySelector("#stop").hidden')
    click('#stop')
    wait_task()
    checks['stop_button_cancels'] = coordinator.service.last_result.code=='cancelled'
    coordinator.service.runner_factory=previous
    # Re-query the already-complete fixture so the final screenshot has a real verified state.
    click('#manual')
    time.sleep(0.2)
    wait_task()
    native=coordinator.native
    checks['tray'] = native.info()['tray']
    checks['normal_rounded'] = native.info()['rounded']
    checks['no_legacy_window_shadow'] = not window.shadow
    checks['resize_grips_composited'] = native.info()['resize_grips_composited']
    checks['resize_corners_clipped'] = native.info()['resize_corners_clipped']
    for width,height in [(860,680),(1000,760),(1200,860),(1480,920)]:
        window.resize(width,height)
        time.sleep(0.2)
        checks['layout_'+str(width)] = (window.evaluate_js('document.documentElement.scrollWidth<=innerWidth')
            and native.info()['rounded'] and native.info()['resize_corners_clipped'])
    native.toggle_maximize()
    time.sleep(0.2)
    checks['maximized_square'] = not native.info()['rounded']
    native.toggle_maximize()
    window.resize(1200,860)
    time.sleep(0.3)
    checks['restored_round'] = native.info()['rounded'] and native.info()['resize_corners_clipped']
    native.hide()
    checks['tray_hide'] = not window.native.Visible
    native.show()
    checks['tray_restore'] = bool(window.native.Visible)
    checks['glass_without_live_blur'] = window.evaluate_js('getComputedStyle(document.querySelector(".glass")).backdropFilter==="none"')
    if capture:
        native.capture(capture)
    return {'passed': all(checks.values()), 'checks':checks, 'native':native.info(),
            'capture_scope':'web_content_with_window_region_mask',
            'fixture_submissions':coordinator.fixture.submitted, 'fixture_requests_intercepted':coordinator.fixture.intercepted}
