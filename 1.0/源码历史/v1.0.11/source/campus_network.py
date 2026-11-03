"""CSU portal authentication, independent of the check-in account and browser."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
import ssl
from urllib.parse import urlencode, urlsplit
from urllib.request import build_opener, ProxyHandler, HTTPSHandler, HTTPRedirectHandler
from urllib.error import HTTPError, URLError

from core import RunResult, Cancelled


PORTAL = "https://portal.csu.edu.cn"
STATUS_URL = PORTAL + "/drcom/chkstatus?callback=csuStatus"
LOGIN_URL = PORTAL + ":802/eportal/portal/login"
SCHOOL_URL = "https://zhxg.csu.edu.cn/"
CARRIERS = {"无需运营商": "campus", "中国移动": "cmcc", "中国联通": "unicom", "中国电信": "telecom"}
SUFFIXES = {"campus": "", "cmcc": "@cmccn", "unicom": "@unicomn", "telecom": "@telecomn"}


@dataclass(frozen=True, repr=False)
class NetworkCredentials:
    username: str
    password: str
    carrier: str

    def account(self):
        if self.carrier not in SUFFIXES:
            raise ValueError("请选择校园网认证类型：无需运营商、移动、联通或电信。")
        user = self.username.strip()
        if not user or not self.password:
            raise ValueError("请填写独立的校园网账号和密码。")
        if any(ord(c) < 32 for c in user):
            raise ValueError("校园网账号包含无效字符。")
        # With no carrier selected, the original account is always preserved.
        suffix = SUFFIXES[self.carrier]
        if not suffix:
            return user
        known = next((s for s in SUFFIXES.values() if s and user.lower().endswith(s)), None)
        if known and known != suffix:
            raise ValueError("账号后缀与所选运营商不一致，请核对后保存。")
        if known:
            if not user[:-len(known)]:
                raise ValueError("请填写运营商后缀前的校园网账号。")
            return user
        if "@" in user:
            raise ValueError("运营商模式请填写不带后缀的账号，或填写对应运营商的完整账号。")
        return user + suffix


def parse_portal_response(text):
    """Decode bounded JSON / JSONP as data; never execute portal JavaScript."""
    value = text.strip()
    match = re.fullmatch(r"(?:[A-Za-z_$][\w$]*\s*)?\(\s*(\{.*\})\s*\)\s*;?", value, re.S)
    if match:
        value = match[1]
    data = json.loads(value)
    if not isinstance(data, dict) or type(data.get("result")) not in (int, str) or str(data["result"]) not in ("0", "1"):
        raise ValueError("Unrecognized portal response")
    return data


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class CampusNetwork:
    def __init__(self, store, log):
        self.store, self.log = store, log
        self.rejected = False

    def reset(self):
        self.rejected = False

    def _get(self, url):
        # Fixed HTTPS hosts, verified certificates, direct local network access.
        # Do not forward credentials through a proxy or follow portal redirects.
        opener = build_opener(ProxyHandler({}), HTTPSHandler(context=ssl.create_default_context()), NoRedirect())
        try:
            response = opener.open(url, timeout=5)
        except HTTPError as error:
            response = error
        with response:
            if url == SCHOOL_URL:
                return response.status, "", response.headers.get("Location", "")
            body = response.read(65537)
            if len(body) > 65536:
                raise ValueError("Response too large")
            return response.status, body.decode("utf-8-sig", errors="replace"), response.headers.get("Location", "")

    def school_reachable(self):
        try:
            status, _, location = self._get(SCHOOL_URL)
            if status == 200:
                return True
            if status in (301, 302, 303, 307, 308):
                target = urlsplit(location)
                return (target.scheme == "https" and target.hostname in ("zhxg.csu.edu.cn", "ca.csu.edu.cn")
                        and target.port in (None, 443) and not target.username and not target.password)
        except (OSError, ValueError, URLError):
            pass
        return False

    def ensure_online(self, stop):
        def check_cancel():
            if stop.is_set():
                raise Cancelled()

        try:
            check_cancel()
            self.log("正在检查学工系统连通性…")
            reachable = self.school_reachable()
            check_cancel()
            if reachable:
                return RunResult("campus_ready", "学工系统可访问，无需校园网认证（未验证校园网密码）。")
            self.log("学工系统暂不可达，正在检查中南大学校园网认证状态…")
            try:
                status, body, _ = self._get(STATUS_URL)
            except (OSError, URLError):
                return RunResult("campus_unavailable", "无法安全访问中南校园网门户，请检查 Wi-Fi／网线、门户证书和网络连接；未发送凭据。")
            check_cancel()
            if status != 200:
                return RunResult("campus_unavailable", "校园网门户返回异常，请检查网络；未发送凭据。")
            try:
                portal = parse_portal_response(body)
            except (ValueError, TypeError):
                return RunResult("campus_portal_changed", "校园网状态接口格式已变化，未发送凭据，请人工检查门户。", needs_user=True)
            if str(portal["result"]) == "1":
                return RunResult("campus_service", "校园网显示已认证，但学工系统暂不可达；稍后重试，不重复登录校园网。")
            if self.rejected:
                return RunResult("campus_rejected", "校园网认证曾被拒绝，请核对校园网账号、密码和认证类型并重新保存设置后再试。", needs_user=True)
            try:
                saved = self.store.load_secret("network.dat")
                credentials = NetworkCredentials(**saved) if isinstance(saved, dict) else None
                if credentials is None:
                    raise ValueError("Missing credentials")
                account = credentials.account()
            except Exception:
                return RunResult("campus_config", "校园网设置缺失或无法读取，请重新保存校园网账号、密码和认证类型。", needs_user=True)
            query = urlencode({"user_account": account, "user_password": credentials.password})
            check_cancel()
            self.log("已确认校园网尚未认证，正在按保存的认证类型登录…")
            status, body, _ = self._get(LOGIN_URL + "?" + query)
            check_cancel()
            if status != 200:
                return RunResult("campus_login", "校园网认证请求未完成，请稍后重试。")
            try:
                result = parse_portal_response(body)
            except (ValueError, TypeError):
                self.rejected = True
                return RunResult("campus_portal_changed", "校园网认证响应无法识别，请核对门户并重新保存校园网设置后再试。", needs_user=True)
            if str(result["result"]) != "1":
                self.rejected = True
                return RunResult("campus_rejected", "校园网拒绝认证，请核对账号、密码、认证类型或账户状态；重新保存校园网设置后再试。", needs_user=True)
            # A portal success response alone does not prove the check-in site works.
            for attempt in range(3):
                if attempt and stop.wait(1):
                    raise Cancelled()
                check_cancel()
                reachable = self.school_reachable()
                check_cancel()
                if reachable:
                    return RunResult("campus_ready", "校园网认证成功，学工系统连通性复核通过。")
            return RunResult("campus_service", "校园网返回认证成功，但学工系统仍不可达，将按打卡重试间隔再次检查。")
        except Cancelled:
            return RunResult("cancelled", "校园网检查已停止。")
        except Exception:
            # Network exception strings can contain the entire password-bearing URL.
            return RunResult("campus_network", "校园网请求未完成，请检查网络、门户证书或稍后重试。")
