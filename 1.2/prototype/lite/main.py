"""Independent WebView2 prototype. All validation uses synthetic local data."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
import winreg

HERE = Path(__file__).resolve().parent
FROZEN = bool(getattr(sys, 'frozen', False))
ASSETS = Path(sys._MEIPASS) / 'lite-assets' if FROZEN else HERE.parents[1] / 'source'
if not FROZEN:
    sys.path.insert(0, str(ASSETS))
from core import Settings, SHANGHAI, now_cn, server_batch
from storage import Store, crypt
from cdp_probe import ProbeCancelled, find_browser, run_probe
from native_window import NativeWindow

VERSION = '0.2.0-backend'


def runtime_info():
    versions = set()
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(hive, r'SOFTWARE\Microsoft\EdgeUpdate\Clients',
                        0, winreg.KEY_READ | view) as key:
                    for i in range(winreg.QueryInfoKey(key)[0]):
                        with winreg.OpenKey(key, winreg.EnumKey(key, i)) as child:
                            try:
                                if 'WebView2' in winreg.QueryValueEx(child, 'name')[0]:
                                    versions.add(str(winreg.QueryValueEx(child, 'pv')[0]))
                            except OSError:
                                pass
            except OSError:
                pass
    return {'webview2': sorted(versions), 'uses_shared_runtime': True,
            'runtime_downloaded_by_prototype': False}


def local_checks():
    # Use the original Store and Settings, only within this temporary test directory.
    with tempfile.TemporaryDirectory(prefix='CSU_Lite_Encryption_') as folder:
        store = Store(Path(folder))
        value = {'username': 'lite-test', 'password': 'synthetic-validation-only'}
        store.save_credentials(**value)
        ciphertext = (store.root / 'credentials.dat').read_bytes()
        encryption = (store.credentials() == value and value['password'].encode() not in ciphertext)
        store.save_secret('session.dat', {'cookies': [{'name': 'probe', 'value': 'local'}]})
        store.save_credentials('another-local-account', 'synthetic-only')
        isolation = store.load_secret('session.dat') is None
        settings = Settings(start='21:00', end='23:25').validate()
        store.save_settings(settings)
        at = datetime(2026, 11, 3, 20, 0, tzinfo=SHANGHAI)
        delay = store.schedule_delay('2026-11-04', create=True)
        scheduling = (store.settings() == settings and
            settings.next_run(at).strftime('%H:%M') == '21:00' and
            store.schedule_delay('2026-11-04', create=True) == delay and
            0 <= delay <= 60)
        parse_ok = server_batch({'code': 200, 'data': {'dkbc': 'test', 'sfydk': '0',
            'dksjfw': '00:00-23:59'}})['dkbc'] == 'test'
    return {'dpapi': encryption, 'account_isolation': isolation,
            'original_schedule': scheduling, 'original_response_parser': parse_ok}


def render_html():
    text = (HERE / 'ui/index.html').read_text('utf-8')
    text = text.replace('/* INLINE_CSS */', (HERE / 'ui/style.css').read_text('utf-8'))
    text = text.replace('/* INLINE_JS */', (HERE / 'ui/app.js').read_text('utf-8'))
    background = base64.b64encode((ASSETS / 'ui/assets/background.png').read_bytes()).decode()
    text = text.replace('__BACKGROUND__', 'data:image/png;base64,' + background)
    def icon(match):
        svg = (ASSETS / 'ui/assets' / (match[1] + '.svg')).read_text('utf-8')
        svg = svg.replace('fill="#212121"', 'fill="currentColor"')
        return re.sub(r'<svg\b', '<svg aria-hidden="true"', svg, count=1)
    return re.sub(r'\{\{ICON:([a-z-]+)\}\}', icon, text)


class Bridge:
    """Expose only the intended local UI operations, never the Store or native form."""
    def __init__(self, api):
        self._api = api

    def state(self):
        return self._api.state()

    def save_settings(self, name, note):
        return self._api.save_settings(name, note)

    def run_validation(self):
        return self._api.run_validation()

    def stop_validation(self):
        return self._api.stop_validation()

    def toggle_timer(self):
        return self._api.toggle_timer()

    def window_action(self, action):
        return self._api.window_action(action)


class API:
    def __init__(self, root):
        self.root = root
        self.store = Store(root / 'validation-data')
        self.window = self.native = None
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.worker = None
        self.quitting = False
        self.records = []
        self.results = {}
        self.status = '准备就绪'
        self.last_report = None
        self.timer_token = 0
        self.timer_active = False
        self.timer_text = '等待验证'
        self.native_ready = threading.Event()

    def record(self, message):
        with self.lock:
            self.records.insert(0, {'time': now_cn().strftime('%H:%M:%S'), 'message': message})
            del self.records[80:]

    def state(self):
        with self.lock:
            saved = self.store.load_secret('validation-settings.dat', {})
            size_file = (Path(sys.executable).parent / 'size-report.json' if FROZEN
                         else HERE.parents[1] / 'work/lite-validation/size-report.json')
            size = json.loads(size_file.read_text('utf-8')) if size_file.exists() else {}
            return {'version': VERSION, 'status': self.status, 'busy': self.worker is not None,
                'records': list(self.records), 'results': dict(self.results),
                'name': saved.get('name', '本地验证账户'), 'timer_active': self.timer_active,
                'timer_text': self.timer_text, 'size_mb': size.get('unpacked_mb'),
                'runtime': runtime_info(), 'time': now_cn().strftime('%H:%M')}

    def save_settings(self, name, note):
        name, note = str(name).strip(), str(note)
        if not name or len(name) > 32 or len(note) > 160:
            return {'ok': False, 'message': '名称请填写 1–32 个字符，验证文字最多 160 个字符。'}
        with self.lock:
            self.store.save_secret('validation-settings.dat', {'name': name, 'note': note})
            ok = self.store.load_secret('validation-settings.dat') == {'name': name, 'note': note}
        self.record('验证设置已加密保存，并重新读取确认。')
        return {'ok': ok, 'message': '已加密保存'}

    def run_validation(self):
        with self.lock:
            if self.worker is not None:
                return {'ok': False, 'message': '验证正在进行中。'}
            self.stop.clear()
            self.results = {}
            self.status = '正在验证…'
            self.worker = threading.Thread(target=self._validate, name='lite-validation', daemon=True)
            self.worker.start()
        return {'ok': True}

    def _validate(self):
        try:
            core = local_checks()
            if not all(core.values()):
                raise RuntimeError('Original core verification failed')
            self.results.update(core)
            self.record('原有加密存储、账户隔离和调度规则验证通过。')
            self.last_report = run_probe(stop=self.stop, progress=self.record)
            self.results.update(self.last_report['checks'])
            self.status = '基础能力验证通过'
            self.record('本轮完成：仅访问本机测试页面，没有提交学校打卡。')
        except ProbeCancelled:
            self.status = '验证已停止'
            self.record('验证已停止，独立浏览器已清理。')
        except Exception as error:
            self.status = '验证未完成'
            self.record(f'本地验证未完成（{type(error).__name__}）。请检查系统环境后重试。')
            self.last_report = {'passed': False, 'error_type': type(error).__name__}
        finally:
            with self.lock:
                self.worker = None

    def stop_validation(self):
        self.stop.set()
        return {'ok': True}

    def toggle_timer(self):
        with self.lock:
            self.timer_token += 1
            token = self.timer_token
            self.timer_active = not self.timer_active
            if not self.timer_active:
                self.timer_text = '已暂停'
                return {'ok': True}
        def tick():
            for remaining in range(5, 0, -1):
                with self.lock:
                    if token != self.timer_token or self.quitting:
                        return
                    self.timer_text = f'{remaining} 秒后触发本地验证'
                time.sleep(1)
            with self.lock:
                if token != self.timer_token or self.quitting:
                    return
                self.timer_active = False
                self.timer_text = '倒计时已触发'
            result = self.run_validation()
            self.record('本地倒计时已触发验证。' if result['ok'] else '倒计时触发时已有验证任务，本次跳过。')
        threading.Thread(target=tick, daemon=True).start()
        return {'ok': True}

    def window_action(self, action):
        if action == 'minimize':
            self.window.minimize()
        elif action == 'maximize':
            from System.Windows.Forms import FormWindowState
            if self.window.native.WindowState == FormWindowState.Maximized:
                self.window.restore()
            else:
                self.window.maximize()
        elif action == 'hide':
            if not self.native.hide():
                return {'ok': False, 'message': '托盘尚未就绪，请稍后重试。'}
        elif action == 'quit':
            threading.Thread(target=self.quit, daemon=True).start()
        else:
            return {'ok': False}
        return {'ok': True}

    def quit(self):
        self.quitting = True
        self.stop.set()
        if self.worker and self.worker is not threading.current_thread():
            self.worker.join(timeout=25)
        if self.worker:
            self.quitting = False
            self.status = '正在停止验证，请稍后退出'
            return
        self.native.dispose()
        self.window.destroy()


def run_ui(args):
    import webview
    data_root = Path(args.data_root).resolve() if args.data_root else Path(os.environ['LOCALAPPDATA']) / 'CSUCheckinLitePrototype'
    api = API(data_root)
    # NavigateToString has a size limit. Serve the local embedded artwork from a
    # temporary file through pywebview's loopback-only file server instead.
    page_folder = tempfile.TemporaryDirectory(prefix='CSU_Lite_UI_')
    page = Path(page_folder.name) / 'index.html'
    page.write_text(render_html(), encoding='utf-8')
    window = webview.create_window('平安打卡 · 轻量验证版', url=str(page), js_api=Bridge(api),
        width=1120, height=840, min_size=(860, 680), frameless=True,
        easy_drag=False, shadow=True, background_color='#eef5ff', focus=not args.self_test)
    api.window = window
    api.native = NativeWindow(window, ASSETS / 'assets/app.ico', api.quit)
    result = {'version': VERSION, 'runtime': runtime_info(), 'school_requests': 0}

    def loaded():
        try:
            api.native.attach()
            api.native_ready.set()
            api.record('轻量验证版已就绪。设置与测试均使用独立数据。')
        except Exception as error:
            result['native_error'] = type(error).__name__
            api.record('窗口或托盘初始化未完成。')

    window.events.loaded += loaded
    def closing():
        if not api.quitting and api.native.hide():
            return False
    window.events.closing += closing

    def automated_check():
        def await_js(expression):
            # WebView2 ExecuteScriptAsync serializes a JS Promise as {}, so
            # explicitly await it inside the page and poll its resolved value.
            window.evaluate_js('window.__liteAwait=null;Promise.resolve(' + expression +
                ').then(value=>window.__liteAwait={ok:true,value:value},'
                '()=>window.__liteAwait={ok:false});null')
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                resolved = window.evaluate_js('window.__liteAwait')
                if resolved is not None:
                    if not resolved.get('ok'):
                        raise RuntimeError('Javascript bridge rejected the operation')
                    return resolved.get('value')
                time.sleep(0.05)
            raise TimeoutError('Javascript bridge operation')
        try:
            if not api.native_ready.wait(25):
                raise TimeoutError('Native initialization')
            # Javascript bridge and rendered DOM are exercised, not just Python methods.
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                if window.evaluate_js('Boolean(window.liteReady)'):
                    break
                time.sleep(0.1)
            else:
                raise TimeoutError('UI bridge initialization')
            result['native'] = api.native.info()
            result['bridge'] = await_js('window.pywebview.api.save_settings("自动验证", "synthetic-test-only").then(r=>r.ok)')
            result['core'] = local_checks()
            result['browser'] = run_probe('edge', headless=True, progress=api.record)
            try:
                find_browser('chrome')
            except FileNotFoundError:
                result['chrome'] = {'available': False}
            else:
                result['chrome'] = run_probe('chrome', headless=True, progress=api.record)
            api.results = {**result['core'], **result['browser']['checks']}
            api.status = '基础能力验证通过'
            result['ui'] = await_js('window.liteRefresh().then(()=>({ready:window.liteReady,overflow:document.documentElement.scrollWidth>innerWidth,button:document.querySelector("#run").textContent.trim(),blur:getComputedStyle(document.querySelector(".glass")).backdropFilter}))')
            window.evaluate_js('document.querySelector("#settings-open").click()')
            time.sleep(0.3)
            result['settings_dialog'] = window.evaluate_js('document.querySelector("#settings").open')
            if args.capture:
                api.native.capture(str(Path(args.capture).with_name('settings.png')))
            window.evaluate_js('document.querySelector("#settings").close()')
            from System.Windows.Forms import FormWindowState
            window.resize(900, 720)
            time.sleep(0.3)
            result['compact'] = window.evaluate_js('({width:innerWidth,overflow:document.documentElement.scrollWidth>innerWidth})')
            result['compact']['rounded'] = api.native.info()['rounded']
            window.maximize()
            time.sleep(0.3)
            result['maximized_square'] = not api.native.info()['rounded']
            window.restore()
            window.resize(1120, 840)
            time.sleep(0.4)
            result['restored_round'] = api.native.info()['rounded']
            api.native.hide()
            result['tray_hide'] = not window.native.Visible
            api.native.show()
            time.sleep(0.2)
            result['tray_restore'] = bool(window.native.Visible)
            if args.capture:
                api.native.capture(args.capture)
            result['passed'] = (result['bridge'] and all(result['core'].values())
                and result['browser']['passed'] and result.get('chrome', {}).get('passed', True)
                and result['native']['tray'] and result['native']['rounded']
                and result['settings_dialog'] and not result['ui']['overflow']
                and not result['compact']['overflow'] and result['compact']['rounded']
                and result['maximized_square'] and result['restored_round']
                and result['tray_hide'] and result['tray_restore'])
        except Exception as error:
            result.update(passed=False, error_type=type(error).__name__, error=str(error)[:240])
        finally:
            report = Path(args.report).resolve()
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            api.quit()

    # Force WebView2: do not silently fall back to IE or bundle a separate browser engine.
    webview.settings['OPEN_EXTERNAL_LINKS_IN_BROWSER'] = False
    try:
        webview.start(automated_check if args.self_test else None, gui='edgechromium',
            debug=False, private_mode=True, icon=str(ASSETS / 'assets/app.ico'))
    finally:
        page_folder.cleanup()
    return 0 if not args.self_test or result.get('passed') else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--self-test', action='store_true')
    parser.add_argument('--report', default='lite-verification.json')
    parser.add_argument('--capture')
    parser.add_argument('--data-root')
    parser.add_argument('--probe-only', choices=['edge', 'chrome'])
    parser.add_argument('--backend-self-test', action='store_true')
    parser.add_argument('--channel', choices=['edge', 'chrome', 'auto'], default='auto')
    args = parser.parse_args()
    if args.backend_self_test:
        from backend_selftest import run_backend_validation
        try:
            checks = [run_backend_validation(channel) for channel in
                (('edge', 'chrome') if args.channel == 'auto' else (args.channel,))]
            result = {'version': VERSION, 'passed': all(item['passed'] for item in checks), 'browsers': checks}
        except Exception as error:
            result = {'version': VERSION, 'passed': False, 'error_type': type(error).__name__}
        report = Path(args.report).resolve()
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        return 0 if result['passed'] else 1
    if args.probe_only:
        result = {'core': local_checks(), 'browser': run_probe(args.probe_only)}
        Path(args.report).resolve().write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        return 0
    try:
        return run_ui(args)
    except Exception as error:
        if args.self_test:
            Path(args.report).resolve().write_text(json.dumps({'passed': False,
                'error_type': type(error).__name__, 'error': str(error)[:240]}), encoding='utf-8')
        else:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,
                '验证版未能启动。请确认电脑已安装 Microsoft Edge WebView2 Runtime 和 .NET Framework 4.8。\n'
                '本程序不会自动下载运行库。\n错误类型：' + type(error).__name__,
                '轻量验证版', 0x10)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
