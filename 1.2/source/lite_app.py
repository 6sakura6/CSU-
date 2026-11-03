"""Lightweight full Windows app: local WebView2 interface, existing business service."""
from __future__ import annotations

import argparse
import base64
import ctypes
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import winreg

from core import Settings, ManualLocation
from storage import Store, SingleInstance, app_dir
from lite_bridge import Coordinator, Bridge, VERSION
from lite_native import NativeWindow

HERE = Path(__file__).resolve().parent
ASSETS = HERE/'lite-assets' if getattr(sys, 'frozen', False) else HERE


def runtime_info():
    versions = set()
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(hive, r'SOFTWARE\Microsoft\EdgeUpdate\Clients', 0, winreg.KEY_READ | view) as key:
                    for index in range(winreg.QueryInfoKey(key)[0]):
                        with winreg.OpenKey(key, winreg.EnumKey(key, index)) as child:
                            try:
                                if 'WebView2' in winreg.QueryValueEx(child, 'name')[0]:
                                    versions.add(str(winreg.QueryValueEx(child, 'pv')[0]))
                            except OSError:
                                pass
            except OSError:
                pass
    return {'shared_webview2_versions': sorted(versions), 'runtime_bundled': False, 'runtime_auto_download': False}


def render_html():
    text = (HERE/'lite_ui/index.html').read_text('utf-8')
    text = text.replace('/* INLINE_CSS */', (HERE/'lite_ui/style.css').read_text('utf-8'))
    text = text.replace('/* INLINE_JS */', (HERE/'lite_ui/app.js').read_text('utf-8'))
    text = text.replace('__BACKGROUND__', 'data:image/png;base64,' + base64.b64encode(
        (ASSETS/'ui/assets/background.png').read_bytes()).decode())
    def icon(match):
        svg = (ASSETS/'ui/assets'/(match[1]+'.svg')).read_text('utf-8').replace('fill="#212121"', 'fill="currentColor"')
        return re.sub(r'<svg\b', '<svg aria-hidden="true"', svg, count=1)
    return re.sub(r'\{\{ICON:([a-z-]+)\}\}', icon, text)


def write_report(filename, value):
    file = Path(filename).resolve()
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def run(args):
    import webview
    temporary = tempfile.TemporaryDirectory(prefix='CSU_Lite_Offline_') if args.self_test or args.design_preview else None
    directory = Path(temporary.name)/'data' if temporary else Path(args.data_dir).resolve() if args.data_dir else app_dir()/'data'
    if SingleInstance.is_running(directory):
        SingleInstance.signal(directory, 'show')
        return 0
    try:
        instance = SingleInstance(directory)
    except OSError:
        if SingleInstance.signal(directory,'show'):
            return 0
        raise
    if temporary:
        store = Store(directory)
        store.save_credentials('fixture-account', 'fixture-secret')
        store.save_manual_location(ManualLocation(30, 110))
        store.save_settings(Settings(location_mode='manual'))
    coordinator = Coordinator(directory, preview=args.design_preview, testing=args.self_test, instance=instance)
    if args.startup:
        if not coordinator.startup_authorized():
            instance.close()
            return 0
        coordinator.consent()
    page_folder = tempfile.TemporaryDirectory(prefix='CSU_Lite_Page_')
    page = Path(page_folder.name)/'index.html'
    page.write_text(render_html(), encoding='utf-8')
    window = webview.create_window('平安打卡助手', url=str(page), js_api=Bridge(coordinator),
        width=1200, height=860, min_size=(860, 680), frameless=True, easy_drag=False,
        shadow=True, background_color='#eef5ff', focus=not args.self_test)
    native = NativeWindow(window, ASSETS/'assets/app.ico', coordinator.quit,
        lambda: Bridge(coordinator).action('toggle_schedule'))
    coordinator.window, coordinator.native = window, native
    ready = threading.Event()
    result = {'version': VERSION, 'runtime': runtime_info(), 'scope': 'offline_synthetic_pages',
              'external_school_requests_continued': 0}

    def loaded():
        try:
            native.attach()
            native.restrict_navigation(window.real_url)
            if args.startup:
                native.hide()
            ready.set()
        except Exception as error:
            result['native_error'] = type(error).__name__

    def closing():
        if not coordinator.quitting and not coordinator.accepted:
            threading.Thread(target=coordinator.quit, daemon=True).start()
            return False
        if not coordinator.quitting and native.hide():
            return False
    window.events.loaded += loaded
    window.events.closing += closing

    def loop():
        if not ready.wait(30):
            if args.self_test:
                write_report(args.report, {**result, 'passed': False, 'error_type': 'NativeInitialization'})
                coordinator.quit()
            return
        while not coordinator.quitting:
            try:
                coordinator.tick()
            except Exception as error:
                coordinator.ui_error = '运行状态未能更新（' + type(error).__name__ + '）。'
            time.sleep(0.2)

    def started():
        thread = threading.Thread(target=loop, name='checkin-ui-tick', daemon=True)
        thread.start()
        if args.self_test:
            from lite_ui_selftest import run_ui_selftest
            try:
                if not ready.wait(30):
                    raise TimeoutError('Window initialization')
                result.update(run_ui_selftest(window, coordinator, args.capture))
            except Exception as error:
                result.update(passed=False, error_type=type(error).__name__, error=str(error)[:240])
            finally:
                write_report(args.report, result)
                coordinator.quit()
        elif args.capture:
            time.sleep(2)
            native.capture(args.capture)

    webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = False
    try:
        webview.start(started, gui='edgechromium', debug=False, private_mode=True,
                      icon=str(ASSETS/'assets/app.ico'))
    finally:
        if coordinator.service and not coordinator.service.closed:
            coordinator.service.shutdown()
        instance.close()
        page_folder.cleanup()
        if temporary:
            temporary.cleanup()
    return 0 if not args.self_test or result.get('passed') else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', '--data-root', dest='data_dir')
    parser.add_argument('--startup', action='store_true')
    parser.add_argument('--design-preview', action='store_true')
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--backend-self-test', action='store_true')
    parser.add_argument('--channel', choices=['edge', 'chrome', 'auto'], default='auto')
    parser.add_argument('--report', default='lite-verification.json')
    parser.add_argument('--capture')
    args = parser.parse_args()
    if args.backend_self_test:
        try:
            from backend_selftest import run_backend_validation
            checks = [run_backend_validation(channel) for channel in
                (('edge', 'chrome') if args.channel == 'auto' else (args.channel,))]
            result = {'version': VERSION, 'passed': all(item['passed'] for item in checks), 'browsers': checks}
        except Exception as error:
            result = {'version': VERSION, 'passed': False, 'error_type': type(error).__name__}
        write_report(args.report, result)
        return 0 if result['passed'] else 1
    try:
        return run(args)
    except Exception as error:
        if args.self_test:
            write_report(args.report, {'version': VERSION, 'passed': False, 'error_type': type(error).__name__})
        else:
            ctypes.windll.user32.MessageBoxW(None,
                '程序未能启动。请确认安装了 Microsoft Edge WebView2 Runtime 和 .NET Framework 4.8。\n'
                '如数据目录正在被其他版本使用，请从系统托盘恢复该程序。\n'
                '本程序不会自动下载运行库。\n错误类型：'+type(error).__name__, '平安打卡助手', 0x10)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
