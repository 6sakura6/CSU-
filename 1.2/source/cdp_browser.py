"""Small browser adapter for BrowserFlow; implements only its required API."""
from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import math
import time
from urllib.parse import urlsplit

from core import AppError, Cancelled, MENU_SUFFIX, QUERY_SUFFIX, SUBMIT_SUFFIX
from cdp_transport import DriverError


ALLOWED_ORIGINS = {'https://ca.csu.edu.cn', 'https://zhxg.csu.edu.cn'}


def normalize_state(state):
    """Read the existing Playwright storage-state shape without opening a user profile."""
    if state is None:
        return {'cookies': [], 'origins': []}
    if not isinstance(state, dict) or not isinstance(state.get('cookies', []), list) or not isinstance(state.get('origins', []), list):
        raise ValueError('保存的浏览器会话格式无效。')
    cookies = []
    for item in state.get('cookies', []):
        if not isinstance(item, dict):
            raise ValueError('保存的浏览器 Cookie 格式无效。')
        domain = str(item.get('domain', '')).lstrip('.').lower()
        if domain != 'csu.edu.cn' and not domain.endswith('.csu.edu.cn'):
            continue
        if not all(isinstance(item.get(key), str) for key in ('name', 'value', 'domain', 'path')):
            raise ValueError('保存的浏览器 Cookie 字段无效。')
        cookie = {key: item[key] for key in ('name', 'value', 'domain', 'path', 'secure', 'httpOnly', 'sameSite') if key in item}
        expires = item.get('expires', -1)
        if type(expires) not in (int, float) or not math.isfinite(expires):
            raise ValueError('保存的浏览器 Cookie 有效期无效。')
        cookie['expires'] = expires if expires > 0 else -1
        cookie.setdefault('httpOnly', False)
        cookie.setdefault('secure', False)
        cookie.setdefault('sameSite', 'Lax')
        if cookie.get('sameSite') not in (None, 'Strict', 'Lax', 'None'):
            raise ValueError('保存的浏览器 Cookie 规则无效。')
        cookies.append(cookie)
    origins = []
    for item in state.get('origins', []):
        if not isinstance(item, dict):
            raise ValueError('保存的网页会话格式无效。')
        if item.get('origin') not in ALLOWED_ORIGINS:
            continue
        values = item.get('localStorage', [])
        if not isinstance(values, list) or not all(isinstance(value, dict) and
                isinstance(value.get('name'), str) and isinstance(value.get('value'), str) for value in values):
            raise ValueError('保存的网页会话字段无效。')
        origins.append({'origin': item['origin'], 'localStorage': values})
    return {'cookies': cookies, 'origins': origins}


@dataclass(repr=False)
class Response:
    url: str
    status: int
    body: str | None

    @property
    def ok(self):
        return 200 <= self.status < 300

    def json(self):
        if self.body is None:
            raise ValueError('响应正文无法读取。')
        return json.loads(self.body)


class Locator:
    def __init__(self, page, *, selector=None, text=None, exact=True, index=0):
        self.page, self.selector, self.text, self.exact, self.index = page, selector, text, exact, index

    def _items(self):
        if self.selector is not None:
            return f'Array.from(document.querySelectorAll({json.dumps(self.selector)}))'
        match = ('v===needle' if self.exact else 'v.includes(needle)')
        return ('Array.from(document.querySelectorAll("body *")).filter(e=>{'
            f'const needle={json.dumps(self.text, ensure_ascii=False)};'
            'const norm=v=>String(v||"").replace(/\\s+/g," ").trim();'
            f'const v=norm(e.textContent);return ({match}) && '
            '!Array.from(e.children).some(c=>norm(c.textContent)===v);})')

    def _eval(self, operation, *, guarded=False):
        guard = ''
        if guarded and self.page.context.credentials_guard:
            current = self.page.url
            if not self.page.context.credentials_guard(current):
                raise AppError('unsafe_login', '登录页面已变化，已停止填写。', True)
            guard = f'if(location.href!=={json.dumps(current)})throw new Error("origin_changed");'
        return self.page.evaluate('(()=>{' + guard + f'const el=({self._items()})[{self.index}];'
            'if(!el)return null;' + operation + '})()')

    def all(self):
        count = self.page.evaluate(f'({self._items()}).length') or 0
        return [Locator(self.page, selector=self.selector, text=self.text, exact=self.exact, index=i)
                for i in range(min(int(count), 500))]

    def is_visible(self):
        return bool(self._eval('const r=el.getBoundingClientRect(),s=getComputedStyle(el);'
            'return r.width>0&&r.height>0&&s.visibility!=="hidden"&&s.visibility!=="collapse";'))

    def _wait(self, operation, timeout=None):
        deadline = time.monotonic() + (timeout or self.page.default_timeout) / 1000
        while time.monotonic() < deadline:
            value = operation()
            if value is not None:
                return value
            self.page.wait_for_timeout(80)
        raise TimeoutError('等待网页元素超时。')

    def click(self):
        def point():
            return self._eval('el.scrollIntoView({block:"center",inline:"center"});'
                'const r=el.getBoundingClientRect();if(!r.width||!r.height||el.disabled)return null;'
                'const x=r.x+r.width/2,y=r.y+r.height/2,hit=document.elementFromPoint(x,y);'
                'return hit&&(hit===el||el.contains(hit))?{x,y}:null;')
        position = self._wait(point)
        for kind in ('mouseMoved', 'mousePressed', 'mouseReleased'):
            params = {'type': kind, **position}
            if kind != 'mouseMoved':
                params.update(button='left', clickCount=1)
            self.page.call('Input.dispatchMouseEvent', params)

    def fill(self, text):
        value = json.dumps(str(text), ensure_ascii=False)
        def fill():
            return self._eval('if(el.disabled||el.readOnly)return null;el.focus();'
                'const proto=el.tagName==="TEXTAREA"?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;'
                f'Object.getOwnPropertyDescriptor(proto,"value").set.call(el,{value});'
                'el.dispatchEvent(new Event("input",{bubbles:true}));'
                'el.dispatchEvent(new Event("change",{bubbles:true}));return true;', guarded=True)
        self._wait(fill)

    def press(self, key):
        if key != 'Tab':
            raise ValueError('不支持的网页按键。')
        self._eval('el.focus();return true;')
        for kind in ('keyDown', 'keyUp'):
            self.page.call('Input.dispatchKeyEvent', {'type': kind, 'key': 'Tab',
                'code': 'Tab', 'windowsVirtualKeyCode': 9, 'nativeVirtualKeyCode': 9})

    def inner_text(self, timeout=None):
        return self._wait(lambda: self._eval('return el.innerText;'), timeout)


class Page:
    def __init__(self, context, target, session):
        self.context, self.target, self.session = context, target, session
        self.default_timeout = context.default_timeout
        self.callbacks = {'response': [], 'framenavigated': []}
        self.responses = {}
        self.dom_loaded = False
        self.loaded_loaders = set()
        self.main_loader = None
        self.navigation_serial = 0
        self.last_url = 'about:blank'
        self.context.client.handlers.append(self._event)
        for domain in ('Page', 'Runtime', 'Network'):
            self.call(domain + '.enable')
        self.call('Page.setLifecycleEventsEnabled', {'enabled': True})
        self.call('Emulation.setDeviceMetricsOverride', {'width': 393, 'height': 727,
            'deviceScaleFactor': 2.75, 'mobile': True,
            'screenOrientation': {'type': 'portraitPrimary', 'angle': 0}})
        self.call('Emulation.setTouchEmulationEnabled', {'enabled': True, 'maxTouchPoints': 5})
        self.call('Emulation.setTimezoneOverride', {'timezoneId': 'Asia/Shanghai'})
        self.call('Emulation.setLocaleOverride', {'locale': 'zh-CN'})
        version = context.client.call('Browser.getVersion').get('product', 'Chrome/136.0.0.0').split('/')[-1]
        self.call('Emulation.setUserAgentOverride', {'userAgent':
            'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) '
            f'Chrome/{version} Mobile Safari/537.36', 'acceptLanguage': 'zh-CN,zh;q=0.9', 'platform': 'Linux armv8l'})
        if context.fixture:
            self.call('Fetch.enable', {'patterns': [{'urlPattern': '*', 'requestStage': 'Request'}]})
        for script in context.scripts:
            self.call('Page.addScriptToEvaluateOnNewDocument', {'source': script})
        if context.geolocation is not None:
            self.call('Emulation.setGeolocationOverride', context.geolocation)

    def call(self, method, params=None, timeout=10):
        return self.context.client.call(method, params, self.session, timeout)

    @property
    def url(self):
        return self.evaluate('location.href')

    def on(self, event, callback):
        self.callbacks.setdefault(event, []).append(callback)

    def remove_listener(self, event, callback):
        self.callbacks[event] = [item for item in self.callbacks.get(event, []) if item != callback]

    def set_default_timeout(self, milliseconds):
        self.default_timeout = milliseconds

    def _event(self, message):
        if message.get('sessionId') != self.session:
            return
        method, params = message.get('method'), message.get('params', {})
        if method == 'Fetch.requestPaused':
            self.context.fixture.handle(self, params)
        elif method == 'Page.javascriptDialogOpening':
            # Match the legacy browser's default dismissal of unhandled dialogs.
            self.context.client.send('Page.handleJavaScriptDialog', {'accept': False}, self.session)
        elif method in ('Page.frameNavigated', 'Page.navigatedWithinDocument'):
            frame = params.get('frame', params)
            if not frame.get('parentId'):
                if method == 'Page.frameNavigated':
                    self.main_loader = frame.get('loaderId')
                    self.navigation_serial += 1
                self.last_url = frame.get('url', self.last_url)
                for callback in self.callbacks['framenavigated']:
                    callback(self)
        elif method == 'Page.lifecycleEvent' and params.get('name') == 'DOMContentLoaded':
            self.dom_loaded = True
            self.loaded_loaders.add(params.get('loaderId'))
        elif method == 'Network.responseReceived':
            response = params.get('response', {})
            url = response.get('url', '')
            # Do not retain request payloads, credentials, arbitrary headers or unrelated bodies.
            if urlsplit(url).path.endswith((MENU_SUFFIX, QUERY_SUFFIX, SUBMIT_SUFFIX)):
                self.responses[params['requestId']] = (url, int(response.get('status', 0)))
        elif method in ('Network.loadingFinished', 'Network.loadingFailed'):
            saved = self.responses.pop(params.get('requestId'), None)
            if saved is not None:
                body = None
                if method == 'Network.loadingFinished':
                    try:
                        result = self.call('Network.getResponseBody', {'requestId': params['requestId']})
                        body = (base64.b64decode(result['body']).decode('utf-8') if result.get('base64Encoded')
                                else result.get('body', ''))
                    except Cancelled:
                        raise
                    except (DriverError, ValueError):
                        pass
                for callback in self.callbacks['response']:
                    callback(Response(*saved, body))

    def goto(self, url, wait_until='domcontentloaded', timeout=45000):
        self.dom_loaded = False
        before_navigation = self.navigation_serial
        result = self.call('Page.navigate', {'url': url}, timeout=timeout/1000)
        if result.get('errorText'):
            raise DriverError('网页导航失败。')
        deadline = time.monotonic() + timeout/1000
        while time.monotonic() < deadline:
            # A client-side redirect can replace the initial document before its
            # DOMContentLoaded event. Follow the currently committed main frame,
            # rather than waiting forever for the superseded loader ID.
            loader = (self.main_loader if self.navigation_serial > before_navigation
                      else result.get('loaderId'))
            if loader and loader in self.loaded_loaders:
                return
            if not result.get('loaderId') and self.evaluate('document.readyState') in ('interactive', 'complete'):
                return
            self.wait_for_timeout(80)
        raise TimeoutError('网页加载超时。')

    def evaluate(self, expression):
        # BrowserFlow passes both plain expressions and zero-argument JS functions.
        code = expression.strip()
        if code.startswith(('() =>', '()=>', 'function')):
            code = '(' + code + ')()'
        result = self.call('Runtime.evaluate', {'expression': code,
            'returnByValue': True, 'awaitPromise': True}, timeout=max(20, self.default_timeout/1000))
        if result.get('exceptionDetails'):
            raise DriverError('网页脚本执行未完成。')
        return result.get('result', {}).get('value')

    def locator(self, selector):
        return Locator(self, selector=selector)

    def get_by_text(self, text, exact=True):
        return Locator(self, text=text, exact=exact)

    def wait_for_timeout(self, milliseconds):
        self.context.client.pump(milliseconds / 1000)


class BrowserContext:
    def __init__(self, client, state=None, *, fixture=None, credentials_guard=None):
        self.client, self.fixture, self.credentials_guard = client, fixture, credentials_guard
        self.id = client.call('Target.createBrowserContext', {'disposeOnDetach': True})['browserContextId']
        self.pages, self.scripts, self.callbacks = [], [], []
        self.default_timeout = 10000
        self.geolocation = None
        self.state = normalize_state(state)
        self.closed = False
        if self.state['cookies']:
            # CDP uses omitted expiry for a session cookie. The persisted shape
            # retains expires=-1 so the legacy Playwright backend can read it.
            cookies = [{key: value for key, value in cookie.items() if key != 'expires' or value > 0}
                       for cookie in self.state['cookies']]
            client.call('Storage.setCookies', {'browserContextId': self.id, 'cookies': cookies})
        if self.state['origins']:
            self._restore_origins()

    def _restore_origins(self):
        # Seed each origin once, before the application loads. A script injected
        # on every navigation would overwrite freshly refreshed login tokens.
        target = self.client.call('Target.createTarget', {'url': 'about:blank', 'browserContextId': self.id})['targetId']
        session = self.client.call('Target.attachToTarget', {'targetId': target, 'flatten': True})['sessionId']
        def intercept(message):
            if message.get('sessionId') == session and message.get('method') == 'Fetch.requestPaused':
                self.client.send('Fetch.fulfillRequest', {'requestId': message['params']['requestId'],
                    'responseCode': 200, 'responseHeaders': [{'name': 'Content-Type', 'value': 'text/html'}],
                    'body': base64.b64encode(b'<!doctype html><title>Session restore</title>').decode()}, session)
        self.client.handlers.append(intercept)
        try:
            self.client.call('Page.enable', session=session)
            self.client.call('Fetch.enable', {'patterns': [{'urlPattern': '*', 'requestStage': 'Request'}]}, session)
            self.client.call('DOMStorage.enable', session=session)
            for item in self.state['origins']:
                self.client.call('Page.navigate', {'url': item['origin'] + '/__csu_lite_session_restore__'}, session)
                for pair in item['localStorage']:
                    self.client.call('DOMStorage.setDOMStorageItem', {'storageId': {
                        'securityOrigin': item['origin'], 'isLocalStorage': True},
                        'key': pair['name'], 'value': pair['value']}, session)
        finally:
            self.client.call('Target.closeTarget', {'targetId': target})
            self.client.handlers.remove(intercept)

    def set_default_timeout(self, milliseconds):
        self.default_timeout = milliseconds

    def on(self, event, callback):
        if event == 'page':
            self.callbacks.append(callback)

    def new_page(self):
        target = self.client.call('Target.createTarget', {'url': 'about:blank', 'browserContextId': self.id})['targetId']
        session = self.client.call('Target.attachToTarget', {'targetId': target, 'flatten': True})['sessionId']
        page = Page(self, target, session)
        self.pages.append(page)
        for callback in self.callbacks:
            callback(page)
        return page

    def grant_permissions(self, permissions, origin):
        if permissions != ['geolocation'] or origin not in ALLOWED_ORIGINS:
            raise ValueError('定位授权范围无效。')
        self.client.call('Browser.grantPermissions', {'browserContextId': self.id,
            'origin': origin, 'permissions': permissions})

    def set_geolocation(self, position):
        self.geolocation = dict(position)
        for page in self.pages:
            page.call('Emulation.setGeolocationOverride', self.geolocation)

    def add_init_script(self, script):
        self.scripts.append(script)
        for page in self.pages:
            page.call('Page.addScriptToEvaluateOnNewDocument', {'source': script})

    def storage_state(self):
        cookies = self.client.call('Storage.getCookies', {'browserContextId': self.id})['cookies']
        origins = {item['origin']: item for item in self.state['origins']}
        if self.pages:
            page = self.pages[-1]
            page.call('DOMStorage.enable')
            for origin in ALLOWED_ORIGINS:
                try:
                    entries = page.call('DOMStorage.getDOMStorageItems', {
                        'storageId': {'securityOrigin': origin, 'isLocalStorage': True}})['entries']
                    origins[origin] = {'origin': origin,
                        'localStorage': [{'name': pair[0], 'value': pair[1]} for pair in entries]}
                except DriverError:
                    pass
        # Filter CDP-only fields, preserving compatibility with existing DPAPI session.dat.
        return normalize_state({'cookies': cookies, 'origins': list(origins.values())})

    def close(self):
        if self.closed:
            return
        self.closed = True
        for page in self.pages:
            if page._event in self.client.handlers:
                self.client.handlers.remove(page._event)
        self.client.send('Target.disposeBrowserContext', {'browserContextId': self.id}, cleanup=True)
