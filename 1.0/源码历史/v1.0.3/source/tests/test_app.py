"""Offline tests. All browser requests are intercepted; no CSU records are sent."""
from datetime import datetime, timedelta
from pathlib import Path
import json
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import (Settings, PunchStatus, SHANGHAI, server_batch, now_cn, AppError,
                  Cancelled, find_punch_menu, resolve_menu_url)
from storage import Store, crypt, SingleInstance
from browser import BrowserRunner, safe_login_url, valid_location


class CoreTests(unittest.TestCase):
    def test_schedule_final_minute_and_midnight(self):
        schedule = Settings(start="21:00", end="23:59").validate()
        self.assertTrue(schedule.in_window(datetime(2026, 10, 17, 23, 59, 59, tzinfo=SHANGHAI)))
        self.assertFalse(schedule.in_window(datetime(2026, 10, 17, 20, 59, tzinfo=SHANGHAI)))

    def test_invalid_settings(self):
        for value in (Settings(start="24:10"), Settings(end="20:00"), Settings(retry_minutes=0)):
            with self.assertRaises(ValueError):
                value.validate()

    def test_server_response_requires_success(self):
        batch = {"dkbc": "test", "sfydk": 1}
        self.assertEqual(server_batch({"code": 200, "data": batch}), batch)
        self.assertIsNone(server_batch({"code": 401, "data": batch}))
        self.assertIsNone(server_batch({"success": False, "data": batch}))

    def test_double_encoded_url(self):
        status = PunchStatus.from_fresh_url("https://zhxg.csu.edu.cn/znzhxgpt_h5/#/ywViews/qxjlfx/dk/index?dkbc=%25E6%25A0%25A1&dksjfw=19%253A00-23%253A30&sfydk=1&sfmdk=0&dksj=2026-10-16%252022%253A00%253A00")
        self.assertEqual(status.shift, "校")
        self.assertFalse(status.record_is_today(datetime(2026, 10, 17, tzinfo=SHANGHAI)))
        self.assertTrue(status.within_school_window(datetime(2026, 10, 17, 22, tzinfo=SHANGHAI)))

    def test_missing_window_fails_closed(self):
        self.assertFalse(PunchStatus("test", "", False, False, True).within_school_window(now_cn()))

    def test_cas_scope(self):
        self.assertTrue(safe_login_url("https://ca.csu.edu.cn/authserver/login"))
        self.assertFalse(safe_login_url("https://ca.csu.edu.cn.example.com/authserver/login"))
        self.assertFalse(safe_login_url("http://ca.csu.edu.cn/authserver/login"))
        self.assertFalse(safe_login_url("https://ca.csu.edu.cn/authserver/login?service=https%3A%2F%2Fother.example%2F"))

    def test_bad_location(self):
        for value in (None, {}, {"lat": 0, "lng": 0}, {"lat": "nan", "lng": 110}, {"lat": 91, "lng": 110}):
            self.assertFalse(valid_location(value))
        self.assertTrue(valid_location({"lat": 30, "lng": 110}))

    def test_menu_entry_is_resolved_by_name(self):
        data = {"code":200,"data":[{"name":"晚寝报平安","url":"/other"},
                 {"name":"平安打卡","url":"/new/check-in","icon":"https://example.com/image.png"}]}
        self.assertEqual(find_punch_menu(data)['url'], '/new/check-in')

    def test_menu_url_keeps_current_route_and_drops_old_status(self):
        self.assertEqual(resolve_menu_url('/new/check-in?sfydk=1&dksj=2026-10-16&channel=student'),
                         'https://zhxg.csu.edu.cn/znzhxgpt_h5/#/new/check-in?channel=student')
        for url in ('javascript:alert(1)', '//example.com/route', 'https://other.example/check'):
            with self.assertRaises(ValueError):
                resolve_menu_url(url)

    def test_fresh_status_on_new_menu_route(self):
        url = 'https://zhxg.csu.edu.cn/znzhxgpt_h5/#/new/check-in?dkbc=test&dksjfw=19:00-23:30&sfydk=0&sfmdk=0'
        self.assertIsNotNone(PunchStatus.from_fresh_url(url, '/new/check-in'))
        self.assertIsNone(PunchStatus.from_fresh_url(url, '/old/check-in'))


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name))

    def tearDown(self):
        self.directory.cleanup()

    def test_encrypted_credentials(self):
        self.store.save_credentials("offline-account", "test-password-仅用于测试")
        self.assertNotIn(b"offline-account", (self.store.root / "credentials.dat").read_bytes())
        self.assertNotIn(b"test-password", (self.store.root / "credentials.dat").read_bytes())
        self.assertEqual(self.store.credentials()["password"], "test-password-仅用于测试")

    def test_account_change_invalidates_session_and_ledger(self):
        self.store.save_credentials("a", "test")
        self.store.save_secret("session.dat", {"cookies": ["test"]})
        self.store.mark_done("2026-10-17")
        self.store.save_credentials("b", "test")
        self.assertIsNone(self.store.load_secret("session.dat"))
        self.assertFalse(self.store.done("2026-10-17"))

    def test_corrupt_credentials_can_be_replaced(self):
        (self.store.root / "credentials.dat").write_bytes(b"not a DPAPI blob")
        self.store.save_credentials("new", "test")
        self.assertEqual(self.store.credentials()["username"], "new")

    def test_single_instance(self):
        instance = SingleInstance(self.store.root)
        try:
            with self.assertRaises(OSError):
                SingleInstance(self.store.root)
        finally:
            instance.close()


FIXTURE = """<!doctype html><html lang='zh-CN'><meta charset='utf-8'><body><div id='app'></div><script>
const app = document.querySelector('#app');
const target = __TARGET_ROUTE__;
const openPunch = async () => {
  const r = await fetch('/gw/qxj/qxj-padkglxx/queryKqDkbc', {method:'POST'});
  const b = (await r.json()).data;
  const q = new URLSearchParams(b);
  location.hash = target + '?' + q.toString();
  app.innerHTML = b.sfydk ? '<p>今日已打卡</p>' : '<button id="punch">立即打卡</button>';
  app.__vue__ = {handlePunch(){}, submitPadk(){}, latitude:30, longitude:110,
                locationName:'离线测试地点', canPunch:!b.outside, locating:false, $children:[]};
  const button = document.querySelector('#punch');
  if(button) button.onclick = async () => {
    await fetch('/gw/qxj/qxj-padkglxx/xspadk', {method:'POST'});
    app.innerHTML = '<p>打卡成功</p>';
  };
};
window.addEventListener('hashchange', () => {
  if(location.hash === '#' + target) openPunch();
});
if(location.hash.split('?')[0] === '#' + target) {
  app.innerHTML = '';
  openPunch();
} else {
  app.innerHTML = '<h1>服务大厅</h1><div class="item" style="margin-top:1800px">图片卡片</div>';
  fetch('/znzhxgpt/yw/dekt-yyfwcd/getUniServiceMenu');
}
</script></body></html>"""


class BrowserIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(channel="msedge", headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.directory.name))
        self.stop = threading.Event()
        self.logs = []
        self.runner = BrowserRunner(self.store, Settings(), lambda text, *args: self.logs.append(text), self.stop)
        self.context = self.browser.new_context(**self.playwright.devices['Pixel 7'])
        self.state = {"submitted": 0, "queried": 0, "already": False, "reject": False, "outside": False,
                      "old": False, "unavailable": False, "menu_queried":0, "menu_missing":False,
                      "route":"/ywViews/qxjlfx/dk/index"}
        def route(request_route):
            url = request_route.request.url
            if url.endswith('/getUniServiceMenu'):
                self.state['menu_queried'] += 1
                menu = [] if self.state['menu_missing'] else [{'name':'平安打卡','url':self.state['route'],'icon':'test.png'}]
                request_route.fulfill(json={'code':200,'data':{'groups':[{'children':menu}]}})
            elif url.endswith('/queryKqDkbc'):
                self.state['queried'] += 1
                batch = {"dkbc": "校内住宿打卡", "dksjfw": "00:00-23:59", "sfydk": int(self.state['already']), "sfmdk": 0,
                         "lx": 0, "kdk": not self.state['unavailable'], "outside": self.state['outside']}
                if batch['sfydk']:
                    record = now_cn() - timedelta(days=1) if self.state['old'] else now_cn()
                    batch['dksj'] = record.strftime('%Y-%m-%d %H:%M:%S')
                request_route.fulfill(json={"code": 200, "data": batch})
            elif url.endswith('/xspadk'):
                self.state['submitted'] += 1
                if not self.state['reject']:
                    self.state['already'] = True
                request_route.fulfill(json={"code": 400 if self.state['reject'] else 200})
            elif url.startswith('https://zhxg.csu.edu.cn/znzhxgpt_h5/'):
                request_route.fulfill(body=FIXTURE.replace('__TARGET_ROUTE__',json.dumps(self.state['route'])), content_type='text/html; charset=utf-8')
            else:
                request_route.abort()  # No real network traffic in this test suite.
        self.context.route('**/*', route)

    def tearDown(self):
        self.context.close()
        self.directory.cleanup()

    def test_submit_and_fresh_verification(self):
        result = self.runner.perform(self.context, False)
        self.assertTrue(result.completed)
        self.assertEqual(self.state['submitted'], 1)
        self.assertGreaterEqual(self.state['queried'], 2)

    def test_dynamic_entry_without_hall_tile_text(self):
        paths = []
        self.context.on('page', lambda page: page.on('framenavigated', lambda frame: paths.append(frame.url)))
        self.state['already'] = True
        result = self.runner.perform(self.context, False)
        self.assertTrue(result.completed)
        self.assertTrue(any('/ywViews/qxjlfx/dk/index' in path for path in paths))
        self.assertTrue(any('/pages/service/index' in path for path in paths))
        self.assertGreaterEqual(self.state['menu_queried'],1)
        self.assertEqual(self.state['submitted'], 0)

    def test_menu_route_change_is_followed(self):
        self.state.update(already=True, route='/pages/new-service/daily-check-in')
        result = self.runner.perform(self.context, False)
        self.assertTrue(result.completed)
        self.assertGreaterEqual(self.state['menu_queried'],1)
        self.assertGreaterEqual(self.state['queried'],1)

    def test_menu_missing_is_reported_without_submission(self):
        self.state['menu_missing'] = True
        with self.assertRaises(AppError) as caught:
            self.runner.perform(self.context, False)
        self.assertEqual(caught.exception.code, 'menu_missing')
        self.assertEqual(self.state['submitted'], 0)

    def test_already_done_never_submits(self):
        self.state['already'] = True
        result = self.runner.perform(self.context, False)
        self.assertEqual(result.code, 'already_done')
        self.assertEqual(self.state['submitted'], 0)

    def test_dry_run_never_submits(self):
        result = self.runner.perform(self.context, True)
        self.assertEqual(result.code, 'checked')
        self.assertEqual(self.state['submitted'], 0)

    def test_fake_success_toast_is_not_enough(self):
        self.state['reject'] = True
        with self.assertRaises(AppError) as caught:
            self.runner.perform(self.context, False)
        self.assertEqual(caught.exception.code, 'unconfirmed')
        self.assertGreaterEqual(self.state['queried'], 2)

    def test_outside_scope_never_submits(self):
        self.state['outside'] = True
        with self.assertRaises(AppError) as caught:
            self.runner.perform(self.context, False)
        self.assertEqual(caught.exception.code, 'outside_range')
        self.assertEqual(self.state['submitted'], 0)

    def test_old_record_never_treated_as_today(self):
        self.state.update(already=True, old=True)
        with self.assertRaises(AppError) as caught:
            self.runner.perform(self.context, False)
        self.assertEqual(caught.exception.code, 'stale_status')
        self.assertEqual(self.state['submitted'], 0)

    def test_school_unavailable_never_submits(self):
        self.state['unavailable'] = True
        with self.assertRaises(AppError) as caught:
            self.runner.perform(self.context, False)
        self.assertEqual(caught.exception.code, 'unavailable')
        self.assertEqual(self.state['submitted'], 0)

    def test_stop_before_submission(self):
        def log(message, *args):
            if "正在点击立即打卡" in message:
                self.stop.set()
        self.runner.log = log
        with self.assertRaises(Cancelled):
            self.runner.perform(self.context, False)
        self.assertEqual(self.state['submitted'], 0)

    def login_fixture(self, reject=False, captcha=False):
        self.store.save_credentials('offline-account', 'fixture-password')
        self.login_attempts = 0
        html = '''<!doctype html><meta charset="utf-8"><form id="pwdFromId">
            <input id="username" name="username"><input id="password" type="password" readonly onfocus="this.removeAttribute('readonly')">
            <input name="captcha" style="display:CAPTCHA"><a id="login_submit">登录</a><p id="error"></p>
            </form><div style="display:none"><form id="pwdFromId"><input id="username"><input id="password"></form></div>
            <script>document.querySelector('#login_submit').onclick=async()=>{
                await fetch('/authserver/login-submit',{method:'POST'});
                ACTION
            };</script>'''.replace('CAPTCHA', 'block' if captcha else 'none')
        action = "document.querySelector('#error').innerText='用户名或密码错误';" if reject else "location.href='https://zhxg.csu.edu.cn/znzhxgpt_h5/';"
        html = html.replace('ACTION', action)
        def route(request_route):
            if request_route.request.url.endswith('login-submit'):
                self.login_attempts += 1
                request_route.fulfill(json={"ok": not reject})
            else:
                request_route.fulfill(body=html, content_type='text/html; charset=utf-8')
        self.context.route('https://ca.csu.edu.cn/**', route)
        page = self.context.new_page()
        page.goto('https://ca.csu.edu.cn/authserver/login')
        return page

    def test_auto_login_with_hidden_duplicate_forms(self):
        page = self.login_fixture()
        self.runner.login(page)
        self.assertTrue(self.runner.logged_in)
        self.assertEqual(self.login_attempts, 1)

    def test_bad_password_does_not_repeat_login(self):
        page = self.login_fixture(reject=True)
        with self.assertRaises(AppError) as caught:
            self.runner.login(page)
        self.assertEqual(caught.exception.code, 'credentials')
        self.assertTrue(caught.exception.needs_user)
        self.assertEqual(self.login_attempts, 1)

    def test_captcha_is_not_submitted_automatically(self):
        page = self.login_fixture(captcha=True)
        self.runner.settings.manual_wait_seconds = 1
        with self.assertRaises(AppError) as caught:
            self.runner.login(page)
        self.assertEqual(caught.exception.code, 'manual_login')
        self.assertEqual(self.login_attempts, 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
