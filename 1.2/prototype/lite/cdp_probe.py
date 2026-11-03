"""Small CDP feasibility probe, exclusively against an isolated loopback fixture.

No school URLs, real credentials, default browser profile or Node/Playwright.
The driver is a prototype, not a replacement for the production BrowserRunner.
"""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading
import time
from urllib.parse import parse_qs

import websocket


class ProbeCancelled(Exception):
    pass


class CDPError(Exception):
    pass


def find_browser(channel='edge'):
    suffix = ('Microsoft/Edge/Application/msedge.exe' if channel == 'edge'
              else 'Google/Chrome/Application/chrome.exe')
    for variable in ('PROGRAMFILES(X86)', 'PROGRAMFILES', 'LOCALAPPDATA'):
        base = os.environ.get(variable)
        if base and (path := Path(base) / suffix).is_file():
            return path
    raise FileNotFoundError(f'本机未找到 {"Edge" if channel == "edge" else "Chrome"}。')


class CDP:
    def __init__(self, url, stop):
        self.stop = stop
        self.ws = websocket.create_connection(url, timeout=0.25,
            suppress_origin=True, http_no_proxy=['127.0.0.1', 'localhost'])
        self.serial = 0
        self.events = deque(maxlen=4000)
        self.replies = {}

    def _read(self, deadline):
        while time.monotonic() < deadline:
            if self.stop.is_set():
                raise ProbeCancelled('验证已停止。')
            try:
                raw = self.ws.recv()
                if not raw:
                    raise CDPError('浏览器连接已关闭。')
                msg = json.loads(raw)
                if 'id' in msg:
                    self.replies[msg['id']] = msg
                else:
                    self.events.append(msg)
                return
            except websocket.WebSocketTimeoutException:
                continue
        raise TimeoutError('本地浏览器操作超时。')

    def call(self, method, params=None, session=None, timeout=8):
        if self.stop.is_set():
            raise ProbeCancelled('验证已停止。')
        self.serial += 1
        request = {'id': self.serial, 'method': method, 'params': params or {}}
        if session:
            request['sessionId'] = session
        self.ws.send(json.dumps(request))
        deadline = time.monotonic() + timeout
        while request['id'] not in self.replies:
            self._read(deadline)
        reply = self.replies.pop(request['id'])
        if 'error' in reply:
            raise CDPError(f'浏览器不支持或拒绝了 {method}。')
        return reply.get('result', {})

    def event(self, method, predicate=lambda _: True, timeout=10, session=None):
        deadline = time.monotonic() + timeout
        while True:
            for msg in list(self.events):
                if (msg.get('method') == method and (session is None or msg.get('sessionId') == session)
                        and predicate(msg.get('params', {}))):
                    self.events.remove(msg)
                    return msg['params']
            self._read(deadline)

    def evaluate(self, expression, session, timeout=8):
        result = self.call('Runtime.evaluate', {'expression': expression,
            'returnByValue': True, 'awaitPromise': True}, session, timeout)
        if result.get('exceptionDetails'):
            raise CDPError('本地验证页面脚本执行失败。')
        return result.get('result', {}).get('value')

    def close(self):
        try:
            # Cleanup must still work after cancellation; do not clear caller's stop event.
            self.ws.send(json.dumps({'id': self.serial + 1, 'method': 'Browser.close'}))
        except Exception:
            pass
        finally:
            self.ws.close()


@contextmanager
def browser_connection(channel, stop, headless=True):
    browser = find_browser(channel)
    profile = tempfile.TemporaryDirectory(prefix='CSU_Lite_Probe_')
    process = client = None
    try:
        args = [str(browser), '--remote-debugging-address=127.0.0.1',
                '--remote-debugging-port=0', f'--user-data-dir={profile.name}',
                '--no-first-run', '--no-default-browser-check', '--disable-background-networking',
                '--disable-sync', '--disable-extensions', '--password-store=basic', 'about:blank']
        if headless:
            args.insert(1, '--headless=new')
        process = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        deadline = time.monotonic() + 18
        active = Path(profile.name) / 'DevToolsActivePort'
        while time.monotonic() < deadline:
            if stop.is_set():
                raise ProbeCancelled('验证已停止。')
            if process.poll() is not None:
                raise CDPError('独立浏览器实例未能启动。')
            if active.exists():
                lines = active.read_text().splitlines()
                if len(lines) >= 2:
                    client = CDP(f'ws://127.0.0.1:{int(lines[0])}{lines[1]}', stop)
                    break
            stop.wait(0.08)
        if client is None:
            raise TimeoutError('等待本地浏览器启动超时。')
        yield client
    finally:
        if client:
            client.close()
        if process:
            try:
                process.wait(timeout=6)
            except subprocess.TimeoutExpired:
                process.terminate()  # Only this probe's own Popen handle.
                process.wait(timeout=5)
        for attempt in range(5):
            try:
                profile.cleanup()
                break
            except OSError:
                if attempt == 4:
                    raise CDPError('验证结束，但独立浏览器临时目录仍被占用。') from None
                time.sleep(0.25)


@contextmanager
def local_fixture():
    token = secrets.token_urlsafe(18)
    prefix = f'/probe/{token}'

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def reply(self, status, content, kind='application/json', cookie=False):
            body = content.encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', kind + '; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'")
            if cookie:
                self.send_header('Set-Cookie', 'lite_probe=ok; HttpOnly; SameSite=Strict; Path=/')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == prefix + '/':
                self.reply(200, '''<!doctype html><meta charset="utf-8"><title>本地浏览器能力验证</title>
<style>body{font:18px system-ui;background:#edf5ff;color:#17305b;padding:60px}main{max-width:540px;background:white;padding:32px;border-radius:24px}input,button{display:block;padding:14px;margin:15px 0;border-radius:12px;border:1px solid #c8d9ef}button{background:#007aff;color:white}</style>
<main><h1>浏览器能力验证</h1><p>这是本机测试页面，使用预设验证数据。</p>
<input id="username" aria-label="验证用户名"><input id="password" type="password" aria-label="验证口令">
<button id="login">验证登录</button><p id="status">等待验证</p></main>
<script>window.probeState=null;document.querySelector('#login').onclick=async()=>{
const body=new URLSearchParams({username:document.querySelector('#username').value,password:document.querySelector('#password').value});
const r=await fetch('login',{method:'POST',body});if(r.ok){window.probeState=await (await fetch('state')).json();document.querySelector('#status').textContent='本地登录与响应读取完成';}};</script>''', 'text/html')
            elif self.path == prefix + '/state':
                ok = 'lite_probe=ok' in self.headers.get('Cookie', '')
                self.reply(200 if ok else 401, json.dumps({'code': 200 if ok else 401,
                    'data': {'dkbc': '本地测试班次', 'sfydk': '0', 'dksjfw': '00:00-23:59'}}, ensure_ascii=False))
            else:
                self.reply(404, '{}')

        def do_POST(self):
            if self.path != prefix + '/login':
                self.reply(404, '{}')
                return
            size = min(int(self.headers.get('Content-Length', '0')), 1024)
            fields = parse_qs(self.rfile.read(size).decode())
            ok = fields == {'username': ['lite-demo'], 'password': ['local-validation-only']}
            self.reply(200 if ok else 401, json.dumps({'ok': ok}), cookie=ok)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, name='lite-local-fixture', daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}{prefix}/'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def run_probe(channel='edge', stop=None, headless=True, progress=lambda _: None):
    stop = stop or threading.Event()
    checks = {}

    def passed(key, label):
        checks[key] = True
        progress(label)

    with local_fixture() as url, browser_connection(channel, stop, headless) as cdp:
        passed('isolated_browser', f'{channel.title()} 独立实例已启动')
        target = cdp.call('Target.createTarget', {'url': 'about:blank'})['targetId']
        session = cdp.call('Target.attachToTarget', {'targetId': target, 'flatten': True})['sessionId']
        for domain in ('Page', 'Runtime', 'Network'):
            cdp.call(domain + '.enable', session=session)
        cdp.call('Emulation.setTimezoneOverride', {'timezoneId': 'Asia/Shanghai'}, session)
        cdp.call('Page.addScriptToEvaluateOnNewDocument', {'source': 'window.liteInitScript=true;'}, session)
        cdp.call('Page.navigate', {'url': url}, session)
        cdp.event('Page.loadEventFired', session=session)
        cdp.evaluate('document.querySelector("#username").focus()', session)
        cdp.call('Input.insertText', {'text': 'lite-demo'}, session)
        cdp.evaluate('document.querySelector("#password").focus()', session)
        cdp.call('Input.insertText', {'text': 'local-validation-only'}, session)
        cdp.evaluate('document.querySelector("#login").click()', session)
        response = cdp.event('Network.responseReceived',
            lambda p: p.get('response', {}).get('url') == url + 'state', session=session)
        request_id = response['requestId']
        cdp.event('Network.loadingFinished', lambda p: p.get('requestId') == request_id, session=session)
        body = cdp.call('Network.getResponseBody', {'requestId': request_id}, session)
        raw = base64.b64decode(body['body']).decode() if body.get('base64Encoded') else body['body']
        payload = json.loads(raw)
        if payload.get('code') != 200 or not payload.get('data', {}).get('dkbc'):
            raise CDPError('本地登录或响应数据验证失败。')
        passed('login_and_response', '已填写本地表单，捕获并读取服务响应')
        cdp.call('Browser.setPermission', {'permission': {'name': 'geolocation'},
            'setting': 'granted', 'origin': url.split('/probe/')[0]})
        cdp.call('Emulation.setGeolocationOverride', {'latitude': 30.0, 'longitude': 110.0,
            'accuracy': 100}, session)
        position = cdp.evaluate('new Promise((resolve,reject)=>navigator.geolocation.getCurrentPosition(p=>resolve([p.coords.latitude,p.coords.longitude]),()=>reject("probe_location"),{timeout:5000}))', session)
        if position != [30, 110]:
            raise CDPError('本地测试坐标读取不一致。')
        passed('geolocation_protocol', '定位协议验证通过（预设测试坐标，不读取实际位置）')
        if cdp.evaluate('window.liteInitScript', session) is not True:
            raise CDPError('页面初始化脚本未生效。')
        passed('init_script', '页面加载前脚本注入正常')
        cookies = cdp.call('Storage.getCookies')['cookies']
        if not any(item.get('name') == 'lite_probe' and item.get('httpOnly') for item in cookies):
            raise CDPError('未能读取本地测试会话。')
        passed('session_cookie', '加密保存所需的会话 Cookie 可读取')
        # New page proves the browser's session is reusable across page instances.
        another = cdp.call('Target.createTarget', {'url': 'about:blank'})['targetId']
        second = cdp.call('Target.attachToTarget', {'targetId': another, 'flatten': True})['sessionId']
        cdp.call('Page.enable', session=second)
        cdp.call('Page.navigate', {'url': url}, second)
        cdp.event('Page.loadEventFired', session=second)
        code = cdp.evaluate("fetch('state').then(r=>r.status)", second)
        if code != 200:
            raise CDPError('新页面未能复用本地会话。')
        passed('fresh_page_verification', '新页面重新查询，确认会话复用正常')
    passed('cleanup', '浏览器、测试服务与临时目录已关闭')
    return {'channel': channel, 'passed': all(checks.values()), 'checks': checks,
            'scope': 'loopback_fixture_only', 'school_requests': 0}
