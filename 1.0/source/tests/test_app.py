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
                  Cancelled, find_punch_menu, resolve_menu_url, ManualLocation, parse_coordinate)
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

    def test_coordinate_formats_and_hemispheres(self):
        for value in ('30.2584722222', '30°15′30.5″', '北纬30度15分30.5秒', 'N30°15\'30.5"', '30°15\'30.5"N'):
            self.assertAlmostEqual(parse_coordinate(value, 'lat'), 30 + 15/60 + 30.5/3600)
        self.assertAlmostEqual(parse_coordinate('南纬30°15′30.5″', 'lat'), -(30 + 15/60 + 30.5/3600))
        self.assertAlmostEqual(parse_coordinate('120°30′30″W', 'lng'), -(120 + 30/60 + 30/3600))
        self.assertAlmostEqual(parse_coordinate('-0°30′0″', 'lat'), -0.5)
        self.assertEqual(parse_coordinate('180°0′0″E', 'lng'), 180)

    def test_coordinate_invalid_values_are_rejected(self):
        for value, axis in [('', 'lat'), ('NaN', 'lat'), ('inf', 'lat'), ('1e2', 'lat'), ('91', 'lat'),
                            ('181', 'lng'), ('30°60′0″', 'lat'), ('30°0′60″', 'lat'),
                            ('180°0′1″', 'lng'), ('E30', 'lat'), ('N120', 'lng'), ('-30N', 'lat'),
                            ('N30N', 'lat'), ('30,120', 'lat')]:
            with self.subTest(value=value, axis=axis), self.assertRaises(ValueError):
                parse_coordinate(value, axis)
        with self.assertRaises(ValueError):
            ManualLocation.from_inputs('0', '0')
        with self.assertRaises(ValueError):
            Settings(location_mode='unknown').validate()


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

    def test_manual_location_is_encrypted_and_round_trips(self):
        location = ManualLocation.from_inputs('30°15′30″', '120°30′30″')
        self.store.save_manual_location(location)
        self.assertEqual(self.store.manual_location(), location)
        raw = (self.store.root / 'location.dat').read_bytes()
        self.assertNotIn(b'latitude', raw)
        self.assertNotIn(b'30.258', raw)
        self.store.save_settings(Settings(location_mode='manual'))
        self.assertEqual(self.store.settings().location_mode, 'manual')
        self.assertNotIn(b'latitude', (self.store.root / 'settings.json').read_bytes())

    def test_legacy_settings_keep_device_location(self):
        self.assertIsNone(self.store.manual_location())
        (self.store.root / 'settings.json').write_text('{"start":"21:00","auto_start":false}', encoding='utf-8')
        self.assertEqual(self.store.settings().location_mode, 'device')


FIXTURE = """<!doctype html><html lang='zh-CN'><meta charset='utf-8'><body><div id='app'></div><script>
const app = document.querySelector('#app');
const target = __TARGET_ROUTE__;
const useGeo = __USE_GEO__;
if (useGeo) {
  window.BMapGL = {};
  window.BMapGL.Geolocation = class {
    getCurrentPosition(callback) { callback({point:{lat:40,lng:116}}); }
    getStatus() { return 0; }
  };
  window.BMapGL.Geocoder = class { marker() { return 'geocoder-unchanged'; } };
}
const readGps = () => new Promise(resolve => {
  const locator = new BMapGL.Geolocation();
  locator.getCurrentPosition(result => {
    if (locator.getStatus() === 0 && result) {
      window.geoSource = 'bmap';
      resolve({latitude:result.point.lat,longitude:result.point.lng});
    } else {
      navigator.geolocation.getCurrentPosition(position => {
        window.geoSource = 'gps';
        resolve(position.coords);
      }, () => resolve(null), {timeout:3000});
    }
  });
});
const openPunch = async () => {
  const r = await fetch('/gw/qxj/qxj-padkglxx/queryKqDkbc', {method:'POST'});
  const b = (await r.json()).data;
  const q = new URLSearchParams(b);
  location.hash = target + '?' + q.toString();
  app.innerHTML = b.sfydk ? '<p>今日已打卡</p>' : '<button id="punch">立即打卡</button>';
  app.__vue__ = {handlePunch(){}, submitPadk(){}, latitude:30, longitude:110,
                locationName:'离线测试地点', canPunch:!b.outside, locating:false, $children:[]};
  if (useGeo && !b.sfydk) {
    app.__vue__.latitude = app.__vue__.longitude = 0;
    app.__vue__.locating = true;
    const position = await readGps();
    // A deterministic fixture transform checks that the app's transform is preserved.
    if (position) {
      app.__vue__.latitude = position.latitude + 0.002;
      app.__vue__.longitude = position.longitude + 0.004;
    }
    app.__vue__.locating = false;
  }
  const button = document.querySelector('#punch');
  if(button) button.onclick = async () => {
    await fetch('/gw/qxj/qxj-padkglxx/xspadk', {method:'POST',
      body:JSON.stringify({jd:app.__vue__.longitude,wd:app.__vue__.latitude})});
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
                      "route":"/ywViews/qxjlfx/dk/index", "use_geo":False, "submitted_location":None}
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
                self.state['submitted_location'] = request_route.request.post_data_json
                if not self.state['reject']:
                    self.state['already'] = True
                request_route.fulfill(json={"code": 400 if self.state['reject'] else 200})
            elif url.startswith('https://zhxg.csu.edu.cn/znzhxgpt_h5/'):
                html = FIXTURE.replace('__TARGET_ROUTE__',json.dumps(self.state['route'])).replace('__USE_GEO__',json.dumps(self.state['use_geo']))
                request_route.fulfill(body=html, content_type='text/html; charset=utf-8')
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

    def enable_manual_location(self):
        location = ManualLocation.from_inputs('30°15′30″', '120°30′30″')
        self.store.save_manual_location(location)
        self.runner.settings.location_mode = 'manual'
        self.state['use_geo'] = True
        return location

    def test_manual_location_reaches_submission_via_gps_fallback(self):
        location = self.enable_manual_location()
        result = self.runner.perform(self.context, False)
        self.assertTrue(result.completed)
        self.assertEqual(self.state['submitted'], 1)
        self.assertAlmostEqual(self.state['submitted_location']['wd'], location.latitude + 0.002)
        self.assertAlmostEqual(self.state['submitted_location']['jd'], location.longitude + 0.004)
        self.assertEqual(self.context.pages[0].evaluate('window.geoSource'), 'gps')
        self.assertEqual(self.context.pages[0].evaluate('new BMapGL.Geocoder().marker()'), 'geocoder-unchanged')
        self.assertFalse(any(str(location.latitude) in line for line in self.logs))

    def test_manual_dry_run_and_already_done_never_submit(self):
        location = self.enable_manual_location()
        self.state['already'] = True
        result = self.runner.perform(self.context, True)
        self.assertEqual(result.code, 'checked')
        self.assertEqual(self.state['submitted'], 0)
        from browser import NATIVE_LOCATION_JS
        position = self.context.pages[0].evaluate(NATIVE_LOCATION_JS)
        self.assertAlmostEqual(position['lat'], location.latitude)
        self.assertAlmostEqual(position['lng'], location.longitude)

    def test_manual_location_preserves_school_range_rejection(self):
        self.enable_manual_location()
        self.state['outside'] = True
        with self.assertRaises(AppError) as caught:
            self.runner.perform(self.context, False)
        self.assertEqual(caught.exception.code, 'outside_range')
        self.assertEqual(self.state['submitted'], 0)

    def test_missing_manual_location_never_opens_school_page(self):
        self.runner.settings.location_mode = 'manual'
        with self.assertRaises(AppError) as caught:
            self.runner.perform(self.context, False)
        self.assertEqual(caught.exception.code, 'location_config')
        self.assertEqual(self.state['menu_queried'], 0)
        self.assertEqual(self.state['submitted'], 0)

    def test_device_location_preserves_map_provider(self):
        self.state['use_geo'] = True
        result = self.runner.perform(self.context, True)
        self.assertEqual(result.code, 'checked')
        self.assertEqual(self.context.pages[0].evaluate('window.geoSource'), 'bmap')

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
