"""Explicit, local-only presentation API for the full lightweight application."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import json
import subprocess
import sys
import threading
import time

from core import RunResult, now_cn, ManualLocation
from storage import Store,app_dir
from service import CheckinService, DISCLAIMER_TEXT, STARTUP_CONSENT
from startup import StartupRegistration, startup_is_authorized

VERSION = '1.2.4'
PANELS = ('account', 'schedule', 'location', 'network', 'startup', 'import')


class PreviewStartup:
    def __init__(self):
        self.enabled = False

    def is_enabled(self):
        return self.enabled

    def set_enabled(self, value):
        self.enabled = bool(value)


class PreviewNetwork:
    def __init__(self, *_):
        self.offline_detected = False

    def reset(self):
        self.offline_detected = False

    def ensure_online(self, stop):
        if stop.wait(0.4):
            return RunResult('cancelled', '本地连接测试已停止。')
        return RunResult('campus_ready', '离线自检：校园网任务连接正常。')


def startup_registration(data_dir):
    if getattr(sys, 'frozen', False):
        args = [str(Path(sys.executable).resolve()), '--startup', '--data-dir', str(data_dir)]
    else:
        python = Path(sys.executable).resolve()
        windowed = python.with_name('pythonw.exe')
        args = [str(windowed if windowed.exists() else python),
                str(Path(__file__).with_name('lite_app.py')), '--startup', '--data-dir', str(data_dir)]
    return StartupRegistration(directory=Path(data_dir).parent, command=subprocess.list2cmdline(args))


class Coordinator:
    def __init__(self, directory, *, preview=False, testing=False, service_factory=CheckinService,
                 registration=None, instance=None):
        self.directory = Path(directory).resolve()
        self.preview, self.testing = preview, testing
        self.lock = threading.RLock()
        self.store = Store(self.directory)
        self.registration = registration or (PreviewStartup() if preview or testing
                                              else startup_registration(self.directory))
        self.service_factory = service_factory
        self.service = None
        self.window = self.native = None
        self.panel = self._editing = None
        self.accepted = self.quitting = False
        self.import_source = None
        self.fixture = None
        self.instance = instance
        self.attention_record = None
        self.ui_error = ''
        self.tray_notice_shown = False
        self.size_mb = None
        try:
            self.size_mb = json.loads((app_dir()/'size-report.json').read_text('utf-8')).get('unpacked_mb')
        except (OSError,ValueError):
            pass

    def _make_service(self):
        options = {'startup': self.registration}
        if self.instance is not None:
            options['acquire_lock'] = False
        if self.preview or self.testing:
            from offline_school import OfflineSchool
            from lite_browser import LiteBrowserRunner
            self.fixture = OfflineSchool()
            options.update(network_factory=PreviewNetwork, awake=lambda _: None, automatic=False)
            def runner(*args, **kwargs):
                return LiteBrowserRunner(*args, **kwargs, fixture=self.fixture, headless=True)
            options['runner_factory'] = runner
        self.service = self.service_factory(self.store, **options)
        if self.instance is not None:
            self.service.instance = self.instance
        self.service.load_schedule_day(now_cn())

    def consent(self):
        with self.lock:
            if not self.accepted:
                self._make_service()
                self.accepted = True
            return {'ok': True}

    def startup_authorized(self):
        try:
            return startup_is_authorized(self.store.settings(), self.registration, STARTUP_CONSENT)
        except Exception:
            return False

    def _ready(self):
        if not self.accepted or self.service is None:
            raise ValueError('请先阅读并同意免责声明。')
        if self.quitting:
            raise ValueError('程序正在退出。')
        return self.service

    def state(self):
        with self.lock:
            base = {'version': VERSION, 'accepted': self.accepted, 'preview': self.preview,
                    'testing': self.testing, 'disclaimer': DISCLAIMER_TEXT, 'ui_error': self.ui_error,
                    'size_mb': self.size_mb}
            if not self.accepted:
                return base
            service = self.service
            state = service.snapshot()
            state['completed_today'] = service.completed_day == now_cn().date().isoformat()
            state['startup_enabled'] = self.startup_authorized()
            state['location_label'] = '手动 GPS 坐标' if service.settings.location_mode == 'manual' else '使用电脑定位'
            state['network_label'] = '每小时检测并按需认证' if service.settings.campus_network_enabled else '自动认证未开启'
            state['panel'] = self.panel
            next_at = service.next_attempt
            state['next_time'] = next_at.strftime('%H:%M') if service.enabled and next_at else service.settings.start
            state['next_date'] = ((next_at.strftime('%m月%d日') + ' · 北京时间') if service.enabled and next_at
                                  else '开启定时后按每日排期执行')
            state['has_account'] = bool(service.username)
            base.update(state)
            return base

    def begin_panel(self, name):
        with self.lock:
            service = self._ready()
            if name not in PANELS:
                raise ValueError('设置页面无效。')
            if self.panel:
                raise ValueError('请先关闭当前设置。')
            editing = service.editing_settings()
            editing.__enter__()
            self.panel, self._editing = name, editing
            try:
                value = {'panel': name, 'username': service.username, 'settings': service.settings.to_dict()}
                if name == 'location':
                    try:
                        position = self.store.manual_location()
                    except Exception:
                        position = None
                    value['latitude'] = str(position.latitude) if position else ''
                    value['longitude'] = str(position.longitude) if position else ''
                elif name == 'network':
                    try:
                        saved = self.store.load_secret('network.dat') or {}
                    except Exception:
                        saved = {}
                    if not isinstance(saved, dict):
                        saved = {}
                    value.update(network_username=saved.get('username', ''), carrier=saved.get('carrier', 'campus'))
                return value
            except Exception:
                self.close_panel()
                raise

    def close_panel(self, expected=None):
        with self.lock:
            if expected is not None and expected != self.panel:
                return {'ok': True}
            if self._editing:
                self._editing.__exit__(None, None, None)
            self._editing = self.panel = None
            self.import_source = None
            return {'ok': True}

    def save_panel(self, name, value, test=False):
        with self.lock:
            service = self._ready()
            if name != self.panel:
                raise ValueError('设置页面已关闭，请重新打开。')
            if not isinstance(value, dict):
                raise ValueError('设置内容无效。')
            if name == 'account':
                service.save_account(str(value.get('username', '')), str(value.get('password', '')))
            elif name == 'schedule':
                service.save_schedule(str(value.get('start', '')), str(value.get('end', '')), value.get('retry', 5))
            elif name == 'location':
                service.save_location(value.get('mode'), value.get('latitude'), value.get('longitude'))
            elif name == 'network':
                if test:
                    # A disabled automatic switch can still save credentials for a manual connection test.
                    from campus_network import NetworkCredentials
                    saved = self.store.load_secret('network.dat') or {}
                    if not isinstance(saved, dict):
                        saved = {}
                    user = str(value.get('username', '')).strip()
                    secret = str(value.get('password', '')) or (saved.get('password', '') if saved.get('username') == user else '')
                    NetworkCredentials(user, secret, value.get('carrier', 'campus')).account()
                service.save_network(value.get('enabled'), str(value.get('username', '')),
                    str(value.get('password', '')), value.get('carrier', 'campus'))
            elif name == 'startup':
                service.set_login_start(True, consent=STARTUP_CONSENT)
            else:
                raise ValueError('设置页面无效。')
            self.close_panel()
            if name == 'network' and test:
                service.launch('network')
            return {'ok': True, 'message': '设置已保存'}

    def action(self, name):
        with self.lock:
            service = self._ready()
            if self.panel:
                raise ValueError('请先完成或关闭当前设置。')
            if name == 'toggle_schedule':
                service.stop_schedule() if service.enabled else service.start_schedule()
            elif name == 'stop':
                service.stop_current()
            elif name in ('check', 'manual', 'network'):
                if not service.launch(name):
                    raise ValueError('当前正在执行任务，请稍后重试。')
            elif name == 'disable_startup':
                service.set_login_start(False)
            else:
                raise ValueError('操作无效。')
            return {'ok': True}

    def choose_import(self):
        service = self._ready()
        if service.enabled or service.worker:
            raise ValueError('请先暂停定时并等待任务结束，再导入旧版数据。')
        self.begin_panel('import')
        try:
            from webview import FileDialog
            paths = self.window.create_file_dialog(FileDialog.FOLDER)
            if not paths:
                self.close_panel()
                return {'ok': True, 'cancelled': True}
            from profile_import import inspect_profile
            payload, _ = inspect_profile(paths[0])
            self.import_source = Path(paths[0]).resolve()
            return {'ok': True, 'files': len(payload), 'folder': self.import_source.name}
        except Exception:
            self.close_panel()
            raise

    def confirm_import(self):
        with self.lock:
            service = self._ready()
            if self.panel != 'import' or self.import_source is None or service.enabled:
                raise ValueError('请先选择旧版 data 文件夹，并暂停定时。')
            from profile_import import import_profile
            result = import_profile(self.import_source, self.directory)
            service.settings = self.store.settings()
            service.username = self.store.credentials()['username']
            service.completed_day = service.checked_day = ''
            service.daily_delays.clear()
            service.prepared_days.clear()
            service.next_attempt = None
            service.next_network_check = service.monotonic() if service.settings.campus_network_enabled else None
            service.campus_network.reset()
            service.load_schedule_day(now_cn())
            service.status.set('旧版配置已导入，打卡定时已暂停')
            service.log('旧版配置已导入，原配置保留了加密备份；请检查设置后开启定时。')
            self.close_panel()
            return {'ok': True, 'message': '配置已导入，打卡定时已暂停', **result}

    def tick(self):
        with self.lock:
            if self.service:
                self.service.tick()
                if self.native:
                    self.native.set_running(self.service.enabled)
                    attention = next((record for record in self.service.records if record['level'] == 'attention'), None)
                    if attention is not None and attention is not self.attention_record and not self.quitting:
                        self.native.show()
                    self.attention_record = attention
            instance = self.service.instance if self.service else self.instance
            if instance:
                command = instance.command()
                if command == 'show' and self.native:
                    self.native.show()
                elif command == 'exit':
                    threading.Thread(target=self.quit, daemon=True).start()

    def quit(self):
        with self.lock:
            if self.quitting:
                return
            self.quitting = True
            self.close_panel()
            service = self.service
        if service and not service.shutdown(timeout=30):
            with self.lock:
                self.ui_error = '正在停止当前任务，请稍后退出。'
                self.quitting = False
            return
        if service is None and self.instance is not None:
            self.instance.close()
        if self.native:
            self.native.dispose()
        if self.window:
            self.window.destroy()


class Bridge:
    """Only these operations are exposed to the trusted local page."""
    def __init__(self, coordinator):
        self._coordinator = coordinator

    def _call(self, method, *args):
        try:
            return getattr(self._coordinator, method)(*args)
        except (ValueError, OSError) as error:
            # Business validation messages are controlled text; never expose arbitrary third-party exceptions.
            message = str(error) if isinstance(error, ValueError) else '本机文件或系统设置未能保存，请检查权限后重试。'
            return {'ok': False, 'message': message}
        except Exception:
            return {'ok': False, 'message': '操作未完成，请稍后重试。'}

    def state(self):
        return self._call('state')

    def consent(self):
        return self._call('consent')

    def begin_panel(self, name):
        return self._call('begin_panel', name)

    def close_panel(self, expected=None):
        return self._call('close_panel', expected)

    def save_panel(self, name, value, test=False):
        return self._call('save_panel', name, value, test)

    def action(self, name):
        return self._call('action', name)

    def choose_import(self):
        return self._call('choose_import')

    def confirm_import(self):
        return self._call('confirm_import')

    def window_action(self, name):
        coordinator = self._coordinator
        if name == 'quit':
            threading.Thread(target=coordinator.quit, daemon=True).start()
        elif coordinator.window is not None:
            if name == 'minimize':
                coordinator.window.minimize()
            elif name == 'maximize':
                coordinator.native.toggle_maximize()
            elif name == 'hide':
                if not coordinator.accepted:
                    threading.Thread(target=coordinator.quit, daemon=True).start()
                    return {'ok': True}
                if not coordinator.native.hide():
                    return {'ok': False, 'message': '托盘尚未就绪，窗口将保持显示。'}
            else:
                return {'ok': False}
        return {'ok': True}
