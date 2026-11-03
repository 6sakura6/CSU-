"""Normal browser login and UI submission, with fresh server verification."""
from __future__ import annotations

from datetime import datetime
from urllib.parse import urlsplit, parse_qs, unquote
import threading
import time

from core import (SERVICE_URL, SCHOOL_ORIGIN, MENU_SUFFIX, QUERY_SUFFIX,
                  SUBMIT_SUFFIX, AppError, Cancelled, RunResult, PunchStatus,
                  now_cn, server_batch, find_punch_menu, resolve_menu_url, route_key)


# Read the page's own live location; never replace navigator or invent coordinates.
LOCATION_STATE_JS = """() => {
    const root = document.querySelector('#app');
    const stack = root && root.__vue__ ? [root.__vue__] : [];
    if (!stack.length) for (const el of document.querySelectorAll('uni-page, uni-page-body, .dk-index')) {
        if (el.__vue__) stack.push(el.__vue__);
    }
    const seen = new Set();
    while (stack.length && seen.size < 1500) {
        const vm = stack.pop();
        if (!vm || seen.has(vm)) continue;
        seen.add(vm);
        if (typeof vm.handlePunch === 'function' && typeof vm.submitPadk === 'function') {
            return {lat: vm.latitude, lng: vm.longitude, name: vm.locationName || '',
                    canPunch: vm.canPunch, locating: !!vm.locating, failed: !!vm.punchAbnormal};
        }
        if (vm.$children) stack.push(...vm.$children);
    }
    return null;
}"""

NATIVE_LOCATION_JS = """() => new Promise(resolve => {
    if (!navigator.geolocation) return resolve({ok:false});
    navigator.geolocation.getCurrentPosition(
        p => resolve({ok:true, lat:p.coords.latitude, lng:p.coords.longitude,
                      accuracy:p.coords.accuracy}),
        () => resolve({ok:false}),
        {enableHighAccuracy:true, maximumAge:0, timeout:15000});
})"""


def valid_location(value):
    if not isinstance(value, dict):
        return False
    try:
        lat, lng = float(value["lat"]), float(value["lng"])
        return -90 <= lat <= 90 and -180 <= lng <= 180 and (lat != 0 or lng != 0)
    except (ValueError, TypeError, KeyError):
        return False


def safe_login_url(url):
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != "ca.csu.edu.cn" or not parts.path.startswith("/authserver/"):
        return False
    service = parse_qs(parts.query).get("service", [""])[0]
    if service:
        service_parts = urlsplit(unquote(service))
        if (service_parts.scheme != "https" or service_parts.hostname != "zhxg.csu.edu.cn"
                or service_parts.path != "/fdcwonsun/caslogin_h5.jsp"):
            return False
    return True


class BrowserRunner:
    def __init__(self, store, settings, log, stop: threading.Event, scheduled=False):
        self.store, self.settings, self.log, self.stop = store, settings, log, stop
        self.scheduled = scheduled
        self.day = now_cn().date().isoformat()
        self.batch = None
        self.query_seen = False
        self.query_ok = False
        self.submitted_response = False
        self.logged_in = False
        self.context = None
        self.menu_seen = self.menu_ok = False
        self.menu_entry = None

    def check_cancel(self):
        if self.stop.is_set():
            raise Cancelled()

    def pause(self, page, seconds=0.3):
        self.check_cancel()
        page.wait_for_timeout(int(seconds * 1000))

    def observe(self, response):
        # Only inspect these two application responses; no headers, passwords or tokens are logged.
        parts = urlsplit(response.url)
        if parts.hostname != "zhxg.csu.edu.cn":
            return
        path = parts.path
        if path.endswith(MENU_SUFFIX):
            self.menu_seen, self.menu_ok = True, response.ok
            try:
                payload = response.json()
                self.menu_entry = find_punch_menu(payload)
                if isinstance(payload, dict) and (payload.get("success") is False or
                        ("code" in payload and str(payload["code"]) not in ("200", "0"))):
                    self.menu_ok = False
            except Exception:
                self.menu_ok = False
        elif path.endswith(QUERY_SUFFIX):
            self.query_seen, self.query_ok = True, response.ok
            try:
                payload = response.json()
                self.batch = server_batch(payload)
                if isinstance(payload, dict) and (payload.get("success") is False or
                        ("code" in payload and str(payload["code"]) not in ("200", "0"))):
                    self.query_ok = False
            except Exception:
                self.batch = None  # App may unwrap an encrypted response before building the URL.
        elif path.endswith(SUBMIT_SUFFIX):
            self.submitted_response = True

    @staticmethod
    def visible(locator):
        for item in locator.all()[:15]:
            if item.is_visible():
                return item
        return None

    def login(self, page):
        if not safe_login_url(page.url):
            raise AppError("unsafe_login", "登录地址与学校统一认证地址不一致，未填写账户密码。", True)
        credentials = self.store.credentials()
        if not credentials:
            raise AppError("credentials", "请先在程序中保存校园账户和密码。", True)
        self.log("需要统一认证，正在使用本机保存的账户登录。")
        # These selectors were verified against the current CSU CAS login page.
        username = page.locator("#pwdFromId #username")
        for _ in range(25):
            if self.visible(username):
                break
            tab = self.visible(page.locator("#userNameLogin_a"))
            if tab:
                tab.click()
                break
            self.pause(page)
        user_input = self.visible(username)
        password = self.visible(page.locator("#pwdFromId #password"))
        if not user_input or not password:
            raise AppError("login_changed", "未找到学校账号登录表单，请在浏览器完成登录后重试。", True)
        # Check again immediately before sending credentials, after any asynchronous navigation.
        if not safe_login_url(page.url):
            raise AppError("unsafe_login", "登录页面已变化，已停止填写。", True)
        user_input.fill(credentials["username"])
        password.click()  # The school's normal focus handler removes its readonly attribute.
        password.fill(credentials["password"])
        password.press("Tab")
        self.pause(page, 1.0)  # Allow the username captcha check triggered by blur to finish.
        captcha = self.visible(page.locator("#pwdFromId input[name='captcha']"))
        manual_notified = False
        if captcha:
            self.log("需要验证码：账户密码已填入，请在浏览器填写验证码并点击登录。", "attention")
            manual_notified = True
        else:
            button = self.visible(page.locator("#pwdFromId #login_submit"))
            if not button:
                raise AppError("login_changed", "未找到登录按钮，请在浏览器完成登录。", True)
            button.click()
        # Exactly one automatic login submission per run. Password rejection pauses scheduling.
        deadline = time.monotonic() + self.settings.manual_wait_seconds
        while time.monotonic() < deadline:
            self.check_cancel()
            host = urlsplit(page.url).hostname
            if host == "zhxg.csu.edu.cn":
                self.logged_in = True
                self.log("统一认证已返回学工系统。")
                return
            if host != "ca.csu.edu.cn":
                raise AppError("unexpected_page", "登录跳到了其他站点，请人工检查浏览器。", True)
            text = page.locator("body").inner_text(timeout=5000)
            if any(word in text for word in ("用户名或密码错误", "用户名或密码有误", "密码错误",
                                             "用户不存在", "账号已锁定", "账户已锁定", "账号被冻结")):
                raise AppError("credentials", "学校拒绝了登录信息；请检查账户密码，定时任务已暂停。", True)
            if not manual_notified and (self.visible(page.locator("#pwdFromId input[name='captcha']"))
                                        or "二次认证" in text or "短信验证码" in text):
                self.log("登录需要人工验证，请在浏览器完成验证码或二次认证。", "attention")
                manual_notified = True
            self.pause(page, 0.5)
        raise AppError("manual_login", "等待人工登录超时；浏览器将关闭，请检查配置后再运行。", True)

    def navigate_app(self, page, target_url, ready):
        page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
        deadline = time.monotonic() + 90
        tried_login = False
        redirected_to_target = False
        expected = route_key(target_url)
        while time.monotonic() < deadline:
            self.check_cancel()
            parts = urlsplit(page.url)
            if parts.hostname == "ca.csu.edu.cn":
                if tried_login:
                    raise AppError("login_loop", "登录后再次要求认证，请检查账户或在浏览器验证。", True)
                self.login(page)
                tried_login = True
                deadline = time.monotonic() + 90
                continue
            if parts.hostname != "zhxg.csu.edu.cn":
                raise AppError("unexpected_page", "打开了非预期站点，已停止自动操作。", True)
            if "/pages/login/" in parts.fragment or "/fdcwonsun/" in parts.path:
                # Do not interrupt the callback while it exchanges credentials and loads roles.
                self.pause(page, 0.5)
                continue
            if route_key(page.url) != expected:
                if not redirected_to_target and not parts.query:
                    page.goto(target_url, wait_until="domcontentloaded", timeout=45000)
                    redirected_to_target = True
                    continue
            elif ready():
                self.logged_in = True
                return
            self.pause(page)
        raise AppError("status_unavailable", "网页数据加载超时，请检查登录或网络后重新运行仅检查。", True)

    def open_punch(self, page):
        # Read the exact data shown in DevTools Network; no text/OCR or fixed punch route.
        self.log("打开服务大厅，读取最新功能菜单。")
        self.menu_seen = self.menu_ok = False
        self.menu_entry = None
        self.navigate_app(page, SERVICE_URL, lambda: self.menu_seen and self.menu_ok)
        if self.menu_entry is None:
            raise AppError("menu_missing", "当前服务大厅菜单没有平安打卡，请检查账户角色或学校菜单配置。", True)
        try:
            entry_url = resolve_menu_url(self.menu_entry["url"], page.url)
        except ValueError as error:
            raise AppError("menu_url", str(error), True) from None
        self.log("已从大厅菜单获取平安打卡的当前入口，正在进入。")
        self.batch = None
        self.query_seen = self.query_ok = False
        self.navigate_app(page, entry_url, lambda: self.query_seen and self.query_ok)
        status = (PunchStatus.from_batch(self.batch) if self.batch is not None
                  else PunchStatus.from_fresh_url(page.url, route_key(entry_url)))
        if not status or not status.shift:
            raise AppError("status_unavailable", "打卡页面未返回当天班次，请检查登录或网络后重新运行仅检查。", True)
        self.log(f"当前班次：{status.shift}；开放时间：{status.window or '未返回'}。")
        return status

    def location(self, page):
        self.log("等待网页获取电脑的真实定位。")
        deadline = time.monotonic() + 60
        refreshed = False
        while time.monotonic() < deadline:
            self.check_cancel()
            value = page.evaluate(LOCATION_STATE_JS)
            if valid_location(value) and not value.get("locating"):
                if value.get("canPunch") is False:
                    raise AppError("outside_range", "学校页面判断当前定位不符合考勤范围，未提交。", True)
                self.log("电脑定位已取得" + ("，地址已解析。" if value.get("name") else "，地址将由网页继续解析。"))
                return
            if not refreshed and time.monotonic() > deadline - 25:
                tab = self.visible(page.get_by_text("打卡", exact=True))
                if tab:
                    tab.click()  # Uses the page's own normal refresh-location handler.
                refreshed = True
            self.pause(page, 0.5)
        raise AppError("location", "无法取得电脑定位。请检查 Windows 和 Edge 定位权限，未提交打卡。", True)

    def verify(self, context, old_status):
        # A new page and bare route discard old component state and success query parameters.
        page = context.new_page()
        page.on("response", self.observe)
        status = self.open_punch(page)
        if (status.shift == old_status.shift and status.record_is_today(now_cn())
                and now_cn().date().isoformat() == self.day):
            stamp = status.record_time[-8:-3] if len(status.record_time) >= 16 else ""
            self.log("已重新查询服务端：当天打卡记录已确认。")
            return RunResult("success", "打卡成功，已重新查询确认。" + (f" 记录时间 {stamp}" if stamp else ""), True)
        raise AppError("unconfirmed", "提交后尚未查到当天成功记录；下一次重试会先查状态，避免重复提交。")

    def perform(self, context, dry_run):
        page = context.new_page()
        page.set_default_timeout(10000)
        page.on("response", self.observe)
        status = self.open_punch(page)
        if dry_run:
            if not status.punched and not status.exempt:
                self.location(page)
            else:
                self.log("今日已打卡或免打卡，正在单独检查浏览器定位。")
                location = page.evaluate(NATIVE_LOCATION_JS)
                if not location.get("ok") or not valid_location(location):
                    raise AppError("location", "登录与班次查询通过，但浏览器定位不可用；尚未验证自动打卡。", True)
                self.log("浏览器已取得电脑真实定位。")
            return RunResult("checked", "登录、班次与定位检查通过。本次未提交打卡。")
        if status.record_is_today(now_cn()):
            return RunResult("already_done", "服务端显示今天已经打卡，已跳过。", True)
        if status.punched:
            raise AppError("stale_status", "服务端记录日期与今天不一致，未提交，请人工检查。", True)
        if status.exempt:
            return RunResult("exempt", "学校显示今日无需打卡，已跳过。", True)
        if not status.allowed:
            raise AppError("unavailable", "学校当前不允许打卡，将在重试时重新检查。")
        if not status.within_school_window(now_cn()):
            raise AppError("outside_time", "当前不在学校返回的打卡时段内，等待下次检查。")
        self.location(page)
        self.check_cancel()
        at = now_cn()
        if at.date().isoformat() != self.day:
            raise AppError("day_changed", "等待过程中日期发生变化，本次未提交。")
        if not status.within_school_window(at) or (self.scheduled and not self.settings.in_window(at)):
            raise AppError("outside_time", "等待定位或登录后已超过允许时间，本次未提交。")
        button = self.visible(page.get_by_text("立即打卡", exact=True))
        if not button:
            raise AppError("button_changed", "未找到可用的立即打卡按钮，已停止自动提交。", True)
        self.log("定位和时段检查通过，正在点击立即打卡。")
        self.submitted_response = False
        self.check_cancel()
        button.click()
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline and not self.submitted_response:
            self.pause(page)
        if not self.submitted_response:
            self.log("未观察到提交响应，正在重新查询当天状态。")
        return self.verify(context, status)

    def run(self, dry_run=False):
        browser = context = None
        try:
            from playwright.sync_api import sync_playwright
            self.check_cancel()
            try:
                session = self.store.load_secret("session.dat")
            except Exception:
                session = None
                self.log("保存的登录状态不可用，将使用账户密码重新登录。")
            with sync_playwright() as playwright:
                try:
                    browser = playwright.chromium.launch(channel="msedge", headless=False,
                        args=["--no-first-run", "--no-default-browser-check"])
                except Exception:
                    try:
                        browser = playwright.chromium.launch(channel="chrome", headless=False,
                            args=["--no-first-run", "--no-default-browser-check"])
                    except Exception:
                        raise AppError("browser", "无法启动 Edge 或 Chrome，请确认电脑已安装其中一个浏览器。", True) from None
                context = browser.new_context(
                    **playwright.devices["Pixel 7"], locale="zh-CN", timezone_id="Asia/Shanghai",
                    storage_state=session if isinstance(session, dict) else None,
                )
                self.context = context
                context.set_default_timeout(10000)
                # Explicitly authorized by the user. No geolocation value is emulated or overridden.
                context.grant_permissions(["geolocation"], origin=SCHOOL_ORIGIN)
                try:
                    result = self.perform(context, dry_run)
                    return result
                finally:
                    if self.logged_in:
                        try:
                            self.store.save_secret("session.dat", context.storage_state())
                        except Exception:
                            self.log("登录状态未能保存，下次将使用账户密码重新登录。")
                    context.close()
                    browser.close()
        except Cancelled:
            return RunResult("cancelled", "本次操作已停止。若刚好已提交，下次运行会先查询记录。")
        except AppError as error:
            return RunResult(error.code, error.message, needs_user=error.needs_user)
        except Exception as error:
            # Never include third-party exception strings: they may contain sensitive URLs.
            if self.stop.is_set():
                return RunResult("cancelled", "本次操作已停止。")
            return RunResult("network", "浏览器操作未完成（" + type(error).__name__ + "）；请检查网络后重试。")
        finally:
            self.context = None
