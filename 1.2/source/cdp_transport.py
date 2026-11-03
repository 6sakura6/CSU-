"""Cancelable, single-owner CDP transport and isolated Edge/Chrome processes."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time

import websocket
from core import Cancelled


class DriverError(Exception):
    """Deliberately omit protocol payloads and URLs from public error messages."""


class CDPClient:
    def __init__(self, endpoint, stop):
        self.stop = stop
        self.socket = websocket.create_connection(endpoint, timeout=0.15,
            suppress_origin=True, http_no_proxy=['127.0.0.1', 'localhost'])
        self.serial = 0
        self.replies = {}
        self.waiting = set()
        self.handlers = []
        self.owner = threading.get_ident()
        self.closed = False

    def _check(self):
        if threading.get_ident() != self.owner:
            raise DriverError('浏览器操作必须在同一个任务线程执行。')
        if self.stop.is_set():
            raise Cancelled()
        if self.closed:
            raise DriverError('浏览器连接已关闭。')

    def send(self, method, params=None, session=None, *, wait=False, cleanup=False):
        if not cleanup:
            self._check()
        self.serial += 1
        message = {'id': self.serial, 'method': method, 'params': params or {}}
        if session is not None:
            message['sessionId'] = session
        if wait:
            self.waiting.add(self.serial)
        self.socket.send(json.dumps(message, ensure_ascii=False))
        return self.serial

    def read(self, deadline):
        while time.monotonic() < deadline:
            self._check()
            try:
                raw = self.socket.recv()
            except websocket.WebSocketTimeoutException:
                continue
            if not raw:
                raise DriverError('浏览器连接已关闭。')
            if len(raw) > 32 * 1024 * 1024:
                raise DriverError('浏览器响应超过处理上限。')
            message = json.loads(raw)
            if 'id' in message:
                if message['id'] in self.waiting:
                    self.replies[message['id']] = message
            else:
                for handler in tuple(self.handlers):
                    handler(message)
            return
        raise TimeoutError('浏览器操作超时。')

    def call(self, method, params=None, session=None, timeout=10):
        request = self.send(method, params, session, wait=True)
        try:
            deadline = time.monotonic() + timeout
            while request not in self.replies:
                self.read(deadline)
            reply = self.replies.pop(request)
            if 'error' in reply:
                raise DriverError('浏览器操作未完成：' + method)
            return reply.get('result', {})
        finally:
            self.waiting.discard(request)
            self.replies.pop(request, None)

    def pump(self, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                self.read(deadline)
            except TimeoutError:
                return

    def close(self):
        if not self.closed:
            self.closed = True
            self.socket.close()
            self.handlers.clear()
            self.replies.clear()


def browser_path(channel):
    suffix = {'edge': 'Microsoft/Edge/Application/msedge.exe',
              'chrome': 'Google/Chrome/Application/chrome.exe'}[channel]
    for variable in ('PROGRAMFILES(X86)', 'PROGRAMFILES', 'LOCALAPPDATA'):
        base = os.environ.get(variable)
        if base and (path := Path(base) / suffix).is_file():
            return path
    raise FileNotFoundError('未找到所选浏览器。')


class BrowserProcess:
    def __init__(self, stop, *, channel='auto', headless=False, startup_timeout=18):
        self.stop, self.channel, self.headless = stop, channel, headless
        self.startup_timeout = startup_timeout
        self.process = self.client = self.profile = None
        self.profile_path = None
        self.cleanup_ok = True

    def start(self):
        channels = ('edge', 'chrome') if self.channel == 'auto' else (self.channel,)
        for channel in channels:
            if self.stop.is_set():
                raise Cancelled()
            try:
                executable = browser_path(channel)
                self.profile = tempfile.TemporaryDirectory(prefix='CSU_Lite_Session_')
                self.cleanup_ok = False
                self.profile_path = Path(self.profile.name).resolve()
                args = [str(executable), '--remote-debugging-address=127.0.0.1',
                    '--remote-debugging-port=0', f'--user-data-dir={self.profile_path}',
                    '--no-first-run', '--no-default-browser-check', '--disable-background-networking',
                    '--disable-sync', '--disable-extensions', 'about:blank']
                if self.headless:
                    args.insert(1, '--headless=new')
                self.process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                deadline = time.monotonic() + self.startup_timeout
                active = self.profile_path / 'DevToolsActivePort'
                while time.monotonic() < deadline:
                    if self.stop.is_set():
                        raise Cancelled()
                    if self.process.poll() is not None:
                        raise DriverError('独立浏览器未能启动。')
                    if active.exists():
                        try:
                            lines = active.read_text().splitlines()
                            if len(lines) >= 2:
                                port = int(lines[0])
                                if not 1 <= port <= 65535 or not lines[1].startswith('/devtools/browser/'):
                                    raise DriverError('浏览器通知通道无效。')
                                self.client = CDPClient(f'ws://127.0.0.1:{port}{lines[1]}', self.stop)
                                self.channel = channel
                                return self
                        except (OSError, ValueError, websocket.WebSocketException):
                            # DevToolsActivePort can be visible before its socket accepts connections.
                            pass
                    self.stop.wait(0.08)
                raise TimeoutError('等待浏览器启动超时。')
            except Cancelled:
                self.close()
                raise
            except Exception:
                self.close()
                if not self.cleanup_ok:
                    raise DriverError('独立浏览器临时目录未能清理，已停止启动其他浏览器。') from None
        raise DriverError('无法启动 Edge 或 Chrome，请确认电脑已安装其中一个浏览器。')

    def close(self):
        if self.client is None and self.process is None and self.profile is None:
            return
        failures = []
        if self.client:
            try:
                self.client.send('Browser.close', cleanup=True)
            except Exception:
                pass
            finally:
                self.client.close()
                self.client = None
        if self.process:
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                # Never enumerate or kill user browser processes by executable name.
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    failures.append('process')
            self.process = None
        if self.profile:
            target = Path(self.profile.name).resolve()
            safe = target.parent == Path(tempfile.gettempdir()).resolve() and target.name.startswith('CSU_Lite_Session_')
            if not safe:
                raise DriverError('临时目录路径校验失败。')
            for attempt in range(8):
                try:
                    self.profile.cleanup()
                    break
                except OSError:
                    if attempt == 7:
                        failures.append('profile')
                    else:
                        time.sleep(0.2)
            self.profile = None
        self.cleanup_ok = not failures


@contextmanager
def isolated_browser(stop, **options):
    browser = BrowserProcess(stop, **options)
    try:
        yield browser.start()
    finally:
        browser.close()
