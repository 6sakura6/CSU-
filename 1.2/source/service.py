"""Headless application service: persistence, scheduling, tasks and lifecycle.

UI hosts call tick() periodically and render snapshot(). No Tk/Qt/WebView imports.
Only the service's task thread invokes a browser or campus-network operation.
"""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import date, timedelta
from contextlib import contextmanager
import ctypes
import hashlib
import logging
from logging.handlers import RotatingFileHandler
import queue
import threading
import time
import uuid

from core import Settings, ManualLocation, RunResult, now_cn
from storage import SingleInstance
from campus_network import CampusNetwork, NetworkCredentials
from schedule_policy import SchedulePolicy

NETWORK_MODES = ('network', 'network_auto')
MODES = ('check', 'manual', 'scheduled', *NETWORK_MODES)
NETWORK_CHECK_SECONDS = 3600
DISCLAIMER_TEXT = '本程序仅供学习和测试使用。\n\n请在下载后 24 小时内删除。'
STARTUP_CONSENT = hashlib.sha256(DISCLAIMER_TEXT.encode('utf-8')).hexdigest()


class Value:
    def __init__(self, value=''):
        self.value = value

    def set(self, value):
        self.value = value

    def get(self):
        return self.value


class AwakeGuard:
    """SetThreadExecutionState must be owned by a persistent, dedicated thread."""
    def __init__(self):
        self.condition = threading.Condition()
        self.active = self.closed = False
        self.thread = None

    def set(self, active):
        with self.condition:
            self.active = bool(active)
            if active and self.thread is None:
                self.thread = threading.Thread(target=self._run, name='checkin-awake', daemon=True)
                self.thread.start()
            self.condition.notify_all()

    def _run(self):
        function = ctypes.windll.kernel32.SetThreadExecutionState
        try:
            with self.condition:
                while not self.closed:
                    function(0x80000001 if self.active else 0x80000000)
                    self.condition.wait()
        finally:
            function(0x80000000)

    def close(self):
        with self.condition:
            self.closed = True
            self.condition.notify_all()
        if self.thread:
            self.thread.join(timeout=2)


def default_runner(*args, **kwargs):
    from lite_browser import LiteBrowserRunner
    return LiteBrowserRunner(*args, **kwargs)


class CheckinService(SchedulePolicy):
    def __init__(self, store, *, runner_factory=default_runner, network_factory=CampusNetwork,
                 clock=now_cn, monotonic=time.monotonic, acquire_lock=True,
                 startup=None, awake=None, restore=True, automatic=True):
        self.store, self.runner_factory = store, runner_factory
        self.clock, self.monotonic, self.startup = clock, monotonic, startup
        self.automatic = automatic
        self._lock = threading.RLock()
        self.instance = SingleInstance(store.root) if acquire_lock else None
        self._awake = AwakeGuard() if awake is None else None
        self.awake_callback = self._awake.set if self._awake else awake
        self.messages = queue.Queue()
        self.worker = self.pending_result = None
        self.stop_event = threading.Event()
        self.closing = self.closed = self.enabled = False
        self.edit_depth = 0
        self.next_attempt = None
        self.completed_day = self.checked_day = ''
        self.daily_delays, self.prepared_days = {}, set()
        self.current_mode = self.current_day = ''
        self.status, self.next_text = Value('等待配置'), Value('保存账户和时间后，点击开始定时。')
        self.records = []
        self.last_result = None
        self.logger = logging.getLogger('peace-service.' + uuid.uuid4().hex)
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        handler = RotatingFileHandler(store.root/'activity.log', maxBytes=512000, backupCount=3, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(message)s'))
        self.logger.addHandler(handler)
        self.settings, self.username = Settings(), ''
        try:
            self.settings = store.settings()
            self.username = (store.credentials() or {}).get('username', '')
        except Exception:
            self.log('本地配置无法读取，请重新保存。加密文件只能由原 Windows 账户读取。', 'attention')
        self.campus_network = network_factory(store, self.network_log)
        self.next_network_check = monotonic() if self.settings.campus_network_enabled else None
        if restore and self.settings.auto_start and self.username:
            self.enabled = True
            self.status.set('定时已恢复')
            self.set_awake(True)
        self.log('业务服务已就绪，使用北京时间。')

    def log(self, message, kind='info'):
        self.messages.put(('log', (message, kind)))

    def network_log(self, message):
        quiet = self.current_mode == 'network_auto' and not self.campus_network.offline_detected
        self.log(message, 'background' if quiet else 'info')

    def set_awake(self, active):
        self.awake_callback(bool(active))

    def update_schedule_button(self):
        pass  # SchedulePolicy host hook; presentation derives from snapshot().

    def _ensure_idle(self):
        if self.closed or self.closing:
            raise ValueError('程序正在退出。')
        if self.worker is not None:
            raise ValueError('请先停止并等待当前任务结束，再修改设置。')

    def _save_settings(self, settings):
        checked = replace(settings, auto_start=self.enabled).validate()
        self.store.save_settings(checked)
        if (checked.start, checked.end) != (self.settings.start, self.settings.end):
            self.next_attempt = None
        self.settings = checked

    def save_account(self, username, password=''):
        with self._lock:
            self._ensure_idle()
            username = username.strip()
            try:
                old = self.store.credentials()
            except Exception:
                if not password:
                    raise ValueError('已保存的账户无法读取，请重新填写账户密码。') from None
                old = None
            if password:
                self.store.save_credentials(username, password)
            elif not old or old.get('username') != username:
                raise ValueError('首次保存或更换账户时，请填写密码。')
            if not old or old.get('username') != username:
                self.completed_day = self.checked_day = ''
                self.next_attempt = None
                self.daily_delays.clear()
                self.prepared_days.clear()
            self.username = username
            self.log('校园账户已加密保存。')

    def save_schedule(self, start, end, retry_minutes):
        with self._lock:
            self._ensure_idle()
            self._save_settings(replace(self.settings, start=start.strip(), end=end.strip(),
                retry_minutes=int(retry_minutes)))
            self.log('每日执行时间已保存。')

    def save_location(self, mode, latitude=None, longitude=None):
        with self._lock:
            self._ensure_idle()
            settings = replace(self.settings, location_mode=mode).validate()
            if mode == 'manual':
                self.store.save_manual_location(ManualLocation.from_inputs(latitude, longitude))
            self._save_settings(settings)
            self.log('定位设置已保存。')

    def save_network(self, enabled, username='', password='', carrier='campus'):
        with self._lock:
            self._ensure_idle()
            if type(enabled) is not bool:
                raise ValueError('校园网开关无效。')
            settings = replace(self.settings, campus_network_enabled=enabled).validate()
            if enabled or username or password:
                try:
                    saved = self.store.load_secret('network.dat') or {}
                except Exception:
                    saved = {}
                if not isinstance(saved, dict):
                    saved = {}
                username = username.strip()
                if not password and saved.get('username') == username:
                    password = saved.get('password', '')
                credentials = NetworkCredentials(username, password, carrier)
                credentials.account()
                self.store.save_secret('network.dat', asdict(credentials))
            self._save_settings(settings)
            self.campus_network.reset()
            self.next_network_check = self.monotonic() if enabled else None
            self.log('校园网设置已保存，独立于打卡定时。')

    def set_login_start(self, enabled, *, consent=''):
        with self._lock:
            self._ensure_idle()
            if type(enabled) is not bool or (enabled and consent != STARTUP_CONSENT):
                raise ValueError('开启自启动前需要确认免责声明。')
            if self.startup is None:
                raise ValueError('尚未配置轻量版启动入口。')
            previous = self.settings
            updated = replace(previous, launch_at_login=enabled,
                startup_consent=STARTUP_CONSENT if enabled else '')
            self.store.save_settings(updated)
            try:
                self.startup.set_enabled(enabled)
            except Exception:
                self.store.save_settings(previous)
                raise
            self.settings = updated
            self.log('开机自启动设置已保存。')

    @contextmanager
    def editing_settings(self):
        with self._lock:
            self._ensure_idle()
            self.edit_depth += 1
        try:
            yield
        finally:
            with self._lock:
                self.edit_depth -= 1

    def start_schedule(self):
        with self._lock:
            self._ensure_idle()
            if not self.store.credentials():
                raise ValueError('请先保存校园账户。')
            updated = replace(self.settings, auto_start=True).validate()
            self.store.save_settings(updated)
            self.settings, self.enabled = updated, True
            self.next_attempt = None
            self.set_awake(True)
            self.status.set('定时已启动')
            self.log('每日定时已启动，失败后按设定间隔重试。')

    def stop_schedule(self):
        with self._lock:
            self.enabled = False
            self.stop_event.set()
            self.settings = replace(self.settings, auto_start=False)
            try:
                self.store.save_settings(self.settings)
            except Exception:
                self.log('暂停状态未能保存，请检查文件写入权限。', 'attention')
            self.set_awake(self.worker is not None)
            self.status.set('正在停止…' if self.worker else '定时已停止')
            self.next_text.set('点击开始定时可恢复。')
            self.log('已暂停打卡定时；校园网每小时检测仍按其独立设置运行。')

    def stop_current(self):
        with self._lock:
            self.stop_event.set()
            if self.worker:
                self.status.set('正在停止当前操作…')

    def launch(self, mode):
        with self._lock:
            if mode not in MODES:
                raise ValueError('任务类型无效。')
            if self.closed or self.closing or self.worker is not None or self.edit_depth:
                return False
            if mode not in NETWORK_MODES and not self.store.credentials():
                raise ValueError('请先保存校园账户。')
            self.current_mode, self.current_day = mode, self.clock().date().isoformat()
            self.stop_event = threading.Event()
            stop, settings = self.stop_event, replace(self.settings)
            if mode in NETWORK_MODES and self.settings.campus_network_enabled:
                self.next_network_check = self.monotonic() + NETWORK_CHECK_SECONDS
            if mode != 'network_auto':
                self.status.set('正在测试校园网连接…' if mode == 'network' else
                    '正在检查登录与定位…' if mode == 'check' else '正在检查并执行打卡…')
            self.set_awake(True)
            self.log('开始校园网检测（不会提交打卡）。' if mode in NETWORK_MODES else
                '开始仅检查（不会提交）。' if mode == 'check' else '开始执行；先查询当天记录。',
                'background' if mode == 'network_auto' else 'info')
            def work():
                try:
                    if stop.is_set():
                        result = RunResult('cancelled', '本次操作已停止。')
                    elif mode in NETWORK_MODES:
                        result = self.campus_network.ensure_online(stop)
                    else:
                        runner = self.runner_factory(self.store, settings, self.log, stop,
                            scheduled=mode == 'scheduled', clock=self.clock)
                        # The browser owns the date it verifies. A task can be
                        # launched just before midnight and start on the next day.
                        day = getattr(runner, 'day', None)
                        with self._lock:
                            self.current_day = (day if isinstance(day, str) else self.clock().date().isoformat())
                        result = runner.run(dry_run=mode == 'check')
                    if not isinstance(result, RunResult):
                        raise TypeError('Invalid task result')
                except Exception:
                    result = RunResult('internal', '本次任务未完成，请检查运行环境后重试。', needs_user=True)
                self.messages.put(('result', result))
            self.worker = threading.Thread(target=work, name='checkin-service-task', daemon=False)
            self.worker.start()
            return True

    def _handle_result(self, result):
        self.worker = None
        self.last_result = result
        quiet = (self.current_mode == 'network_auto' and not self.campus_network.offline_detected
            and not result.needs_user and result.code in ('campus_ready', 'campus_service', 'cancelled'))
        self.log(result.message, 'background' if quiet else 'attention' if result.needs_user else 'info')
        if not quiet:
            self.status.set(result.message)
        if self.current_mode in NETWORK_MODES:
            self.set_awake(self.enabled)
            return
        if result.completed and self.current_mode != 'check':
            self.completed_day = self.current_day
            try:
                self.store.mark_done(self.current_day)
            except Exception:
                self.log('结果已核验，但本地记录未保存；下次仍会先查询学校记录。')
            try:
                self.day_delay(date.fromisoformat(self.current_day) + timedelta(days=1), create=True)
            except Exception:
                self.schedule_error()
                return
        if self.enabled and not self.closing:
            at = self.clock()
            self.load_schedule_day(at)
            self.next_attempt = (at + timedelta(minutes=self.settings.retry_minutes)
                if (self.current_mode == 'check' or not result.completed)
                and self.current_day == at.date().isoformat() else None)
            self.refresh_next_attempt(at)
        self.set_awake(self.enabled)

    def tick(self):
        with self._lock:
            if self.closed:
                return
            if self.pending_result is not None and self.worker and not self.worker.is_alive():
                result, self.pending_result = self.pending_result, None
                self._handle_result(result)
            for _ in range(1000):
                try:
                    kind, value = self.messages.get_nowait()
                except queue.Empty:
                    break
                if kind == 'result':
                    if self.worker and self.worker.is_alive():
                        self.pending_result = value
                    else:
                        self._handle_result(value)
                else:
                    message, level = value
                    stamp = self.clock().strftime('%m-%d %H:%M:%S')
                    self.logger.info(stamp + '  ' + message)
                    if level != 'background':
                        self.records.insert(0, {'time': stamp, 'message': message, 'level': level})
                        del self.records[600:]
            if self.closing:
                if self.worker is None:
                    self._finish_close()
                return
            if self.worker is not None or self.edit_depth or not self.automatic:
                return
            if self.settings.campus_network_enabled and (self.next_network_check is None or
                    self.monotonic() >= self.next_network_check):
                self.launch('network_auto')
            elif self.enabled:
                at = self.clock()
                self.load_schedule_day(at)
                if self.refresh_next_attempt(at) and at >= self.next_attempt:
                    self.launch('scheduled')

    def snapshot(self):
        with self._lock:
            return {'username': self.username, 'settings': asdict(self.settings),
                'enabled': self.enabled, 'busy': self.worker is not None,
                'current_mode': self.current_mode, 'status': self.status.get(),
                'next_text': self.next_text.get(),
                'next_attempt': self.next_attempt.isoformat() if self.next_attempt else None,
                'records': list(self.records), 'closing': self.closing, 'closed': self.closed,
                'result': asdict(self.last_result) if self.last_result else None}

    def request_exit(self):
        with self._lock:
            self.closing, self.enabled = True, False
            self.stop_event.set()
            if self.worker is None:
                self._finish_close()

    def _finish_close(self):
        if self.closed:
            return
        self.closed = True
        self.set_awake(False)
        if self._awake:
            self._awake.close()
        if self.instance:
            self.instance.close()
        for handler in tuple(self.logger.handlers):
            handler.close()
            self.logger.removeHandler(handler)

    def shutdown(self, timeout=25):
        self.request_exit()
        worker = self.worker
        if worker:
            worker.join(timeout=timeout)
        self.tick()
        return self.closed
