"""Synthetic school pages for offline driver validation, never a live-service proxy.

Every intercepted request is fulfilled or aborted; there is no continueRequest path.
No real credentials, actual position or school data are used.
"""
import base64
import json
from urllib.parse import urlsplit
from core import SERVICE_URL, now_cn


SCHOOL_PAGE = '''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>本地业务流程验证</title><div id="app"></div><script>
const app=document.querySelector('#app'),route=__ROUTE__;
async function punchPage(){
 const b=(await(await fetch('/gw/qxj/qxj-padkglxx/queryKqDkbc',{method:'POST'})).json()).data;
 location.hash=route+'?'+new URLSearchParams(b);
 app.innerHTML='<button id="punch">立即打卡</button>';
 app.__vue__={handlePunch(){},submitPadk(){},latitude:0,longitude:0,locating:true,
   locationName:'本地测试地点',canPunch:!b.outside,$children:[]};
 navigator.geolocation.getCurrentPosition(p=>{app.__vue__.latitude=p.coords.latitude;
   app.__vue__.longitude=p.coords.longitude;app.__vue__.locating=false;},()=>{app.__vue__.locating=false;});
 document.querySelector('#punch').onclick=async()=>{await fetch('/gw/qxj/qxj-padkglxx/xspadk',{
   method:'POST',body:JSON.stringify({wd:app.__vue__.latitude,jd:app.__vue__.longitude})});
   app.innerHTML='<p>打卡成功</p>';};
}
window.addEventListener('hashchange',()=>{if(location.hash==='#'+route)punchPage();});
if(location.hash.split('?')[0]==='#'+route)punchPage();
else{app.innerHTML='<h1>服务大厅</h1>';fetch('/znzhxgpt/yw/dekt-yyfwcd/getUniServiceMenu');}
</script>'''

LOGIN_PAGE = '''<!doctype html><meta charset="utf-8"><title>本地认证验证</title>
<form id="pwdFromId"><input id="username"><input id="password" type="password" readonly onfocus="this.removeAttribute('readonly')">
<input name="captcha" style="display:__CAPTCHA__"><button type="button" id="login_submit">登录</button><p id="error"></p></form>
<div style="display:none"><form id="pwdFromId"><input id="username"><input id="password"></form></div>
<script>document.querySelector('#login_submit').onclick=async()=>{
 const result=await(await fetch('/authserver/fixture-login',{method:'POST',body:JSON.stringify({
 username:document.querySelector('#username').value,password:document.querySelector('#password').value})})).json();
 if(result.ok){localStorage.setItem('fixture-auth','refreshed');location.href=__SERVICE__;}
 else document.querySelector('#error').textContent='用户名或密码错误';};</script>'''


class OfflineSchool:
    def __init__(self):
        self.authenticated = self.punched = self.reject = self.outside = self.exempt = False
        self.captcha = self.menu_missing = self.reject_submit = False
        self.route = '/pages/fixture/daily-check-in'
        self.login_attempts = self.submitted = self.queried = self.intercepted = 0
        self.submitted_position = None

    def handle(self, page, params):
        self.intercepted += 1
        request = params['request']
        parts = urlsplit(request['url'])
        def fulfill(body='', *, payload=None, status=200, headers=None, kind='text/html; charset=utf-8'):
            if payload is not None:
                body = json.dumps(payload, ensure_ascii=False)
                kind = 'application/json; charset=utf-8'
            page.context.client.send('Fetch.fulfillRequest', {'requestId': params['requestId'],
                'responseCode': status, 'responseHeaders': [{'name': 'Content-Type', 'value': kind},
                    *({'name': key, 'value': value} for key, value in (headers or {}).items())],
                'body': base64.b64encode(body.encode('utf-8')).decode()}, page.session)
        if parts.hostname not in ('ca.csu.edu.cn', 'zhxg.csu.edu.cn'):
            page.context.client.send('Fetch.failRequest', {'requestId': params['requestId'],
                'errorReason': 'Aborted'}, page.session)
            return
        cookie = next((str(value) for key, value in request.get('headers', {}).items() if key.lower() == 'cookie'), '')
        if 'csu_lite_fixture=authenticated' in cookie:
            self.authenticated = True
        if parts.hostname == 'ca.csu.edu.cn':
            if parts.path == '/authserver/fixture-login':
                self.login_attempts += 1
                value = json.loads(request.get('postData') or '{}')
                ok = not self.reject and value == {'username': 'fixture-account', 'password': 'fixture-secret'}
                self.authenticated = ok
                fulfill(payload={'ok': ok}, headers={'Set-Cookie':
                    'csu_lite_fixture=authenticated; Domain=csu.edu.cn; Path=/; Secure; HttpOnly; SameSite=Lax'} if ok else {})
            else:
                fulfill(LOGIN_PAGE.replace('__CAPTCHA__', 'block' if self.captcha else 'none')
                    .replace('__SERVICE__', json.dumps(SERVICE_URL)))
        elif not self.authenticated:
            # Match the real hall's client redirect before DOMContentLoaded.
            # A plain HTTP 302 does not expose the superseded-loader regression.
            fulfill('''<!doctype html><script>
                location.replace('https://ca.csu.edu.cn/authserver/login');
                </script>''')
        elif parts.path.endswith('/getUniServiceMenu'):
            fulfill(payload={'code': 200, 'data': [] if self.menu_missing else
                [{'name': '平安打卡', 'url': self.route}]})
        elif parts.path.endswith('/queryKqDkbc'):
            self.queried += 1
            batch = {'dkbc': '本地测试班次', 'dksjfw': '00:00-23:59', 'sfydk': int(self.punched),
                'sfmdk': int(self.exempt), 'kdk': True, 'outside': self.outside}
            if self.punched:
                batch['dksj'] = now_cn().strftime('%Y-%m-%d %H:%M:%S')
            fulfill(payload={'code': 200, 'data': batch})
        elif parts.path.endswith('/xspadk'):
            self.submitted += 1
            self.submitted_position = json.loads(request.get('postData') or '{}')
            if not self.reject_submit:
                self.punched = True
            fulfill(payload={'code': 400 if self.reject_submit else 200})
        else:
            fulfill(SCHOOL_PAGE.replace('__ROUTE__', json.dumps(self.route)))
