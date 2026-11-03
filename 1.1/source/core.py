"""Scheduling and interpretation of fresh server responses. No browser side effects."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone, time as dtime
from urllib.parse import parse_qs, unquote, urlsplit, urlunsplit, urlencode
import re
import math

SHANGHAI = timezone(timedelta(hours=8), "Asia/Shanghai")
SERVICE_URL = "https://zhxg.csu.edu.cn/znzhxgpt_h5/#/pages/service/index"
SCHOOL_ORIGIN = "https://zhxg.csu.edu.cn"
CAS_URL = ("https://ca.csu.edu.cn/authserver/login?service="
           "https%3A%2F%2Fzhxg.csu.edu.cn%2Ffdcwonsun%2Fcaslogin_h5.jsp")
MENU_SUFFIX = "/dekt-yyfwcd/getUniServiceMenu"
QUERY_SUFFIX = "/qxj-padkglxx/queryKqDkbc"
SUBMIT_SUFFIX = "/qxj-padkglxx/xspadk"


def now_cn() -> datetime:
    return datetime.now(SHANGHAI)


def parse_time(value: str) -> dtime:
    if not re.fullmatch(r"\d{2}:\d{2}", value.strip()):
        raise ValueError("时间请按 HH:MM 填写，例如 21:00。")
    try:
        return datetime.strptime(value.strip(), "%H:%M").time()
    except ValueError:
        raise ValueError("请输入有效时间（00:00 至 23:59）。") from None


def parse_coordinate(value, axis: str) -> float:
    label, limit = ("纬度", 90) if axis == "lat" else ("经度", 180)
    text = str(value).strip().upper()
    directions = {"北纬": "N", "南纬": "S", "东经": "E", "西经": "W"}
    for name, symbol in directions.items():
        text = text.replace(name, symbol)
    prefix = re.match(r"^[NSEW]", text)
    suffix = re.search(r"[NSEW]$", text)
    direction = prefix[0] if prefix else suffix[0] if suffix else ""
    if prefix and suffix:
        raise ValueError(f"{label}只需在一侧标注方向。")
    if direction and direction not in ("NS" if axis == "lat" else "EW"):
        raise ValueError(f"{label}方向不匹配，请检查是否把经纬度填反。")
    if prefix:
        text = text[1:].strip()
    elif suffix:
        text = text[:-1].strip()
    text = text.translate(str.maketrans({"度": "°", "′": "'", "’": "'", "分": "'",
                                        "″": '"', "”": '"', "秒": '"', "−": "-"}))
    if re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", text):
        result = float(text)
    else:
        match = re.fullmatch(r'''([+-]?\d+)\s*°\s*(\d{1,2})\s*'\s*(\d{1,2}(?:\.\d+)?)\s*"''', text)
        if not match:
            raise ValueError(f"{label}请输入小数或度分秒，例如 28.123456 或 28°7′24″。")
        degree, minute, second = match.groups()
        if int(minute) >= 60 or float(second) >= 60:
            raise ValueError(f"{label}的分、秒必须小于 60。")
        result = (abs(int(degree)) + int(minute) / 60 + float(second) / 3600) * (-1 if degree.startswith("-") else 1)
    if direction:
        if text.startswith("-"):
            raise ValueError(f"{label}请使用方向或负号其中一种表示方式。")
        result = abs(result) * (-1 if direction in "SW" else 1)
    if not math.isfinite(result) or not -limit <= result <= limit:
        raise ValueError(f"{label}须在 {-limit} 至 {limit} 度之间，请检查经纬度顺序。")
    return result


@dataclass(frozen=True)
class ManualLocation:
    latitude: float
    longitude: float

    @classmethod
    def from_inputs(cls, latitude, longitude):
        lat, lng = parse_coordinate(latitude, "lat"), parse_coordinate(longitude, "lng")
        if lat == 0 and lng == 0:
            raise ValueError("请填写实际位置，不能将经纬度同时设为 0。")
        return cls(lat, lng)

    def geolocation(self):
        return {"latitude": self.latitude, "longitude": self.longitude, "accuracy": 100}


@dataclass
class Settings:
    start: str = "21:00"
    end: str = "23:25"
    retry_minutes: int = 5
    manual_wait_seconds: int = 300
    auto_start: bool = False
    location_mode: str = "device"
    launch_at_login: bool = False
    startup_consent: str = ""
    campus_network_enabled: bool = False

    def validate(self):
        if type(self.campus_network_enabled) is not bool:
            raise ValueError("校园网自动认证开关无效。")
        if self.location_mode not in ("device", "manual"):
            raise ValueError("定位方式无效，请重新打开定位设置。")
        if parse_time(self.end) < parse_time(self.start):
            raise ValueError("重试截止时间不能早于每日打卡时间；当前版本按同一天运行。")
        if not 1 <= int(self.retry_minutes) <= 60:
            raise ValueError("重试间隔须为 1 至 60 分钟。")
        if not 30 <= int(self.manual_wait_seconds) <= 900:
            raise ValueError("人工处理等待时间须为 30 至 900 秒。")
        return self

    def in_window(self, at: datetime) -> bool:
        current = at.astimezone(SHANGHAI).time().replace(tzinfo=None)
        # The configured final minute is included, with no attempts after it.
        return parse_time(self.start) <= current <= parse_time(self.end).replace(second=59, microsecond=999999)

    def random_delay_limit(self) -> int:
        start, end = parse_time(self.start), parse_time(self.end)
        return min(60, max(0, end.hour * 60 + end.minute - start.hour * 60 - start.minute))

    def daily_start(self, day, delay_minutes=0) -> datetime:
        if type(delay_minutes) is not int or not 0 <= delay_minutes <= 60:
            raise ValueError("随机延迟须为 0 至 60 的整数分钟。")
        return datetime.combine(day, parse_time(self.start), tzinfo=SHANGHAI) + timedelta(
            minutes=min(delay_minutes, self.random_delay_limit()))

    def next_run(self, at: datetime, *, finished=False, retry_at=None,
                 delay_minutes=0, next_delay_minutes=0) -> datetime:
        at = at.astimezone(SHANGHAI)
        start = self.daily_start(at.date(), delay_minutes)
        tomorrow = self.daily_start(at.date() + timedelta(days=1), next_delay_minutes)
        cutoff = datetime.combine(at.date(), parse_time(self.end), tzinfo=SHANGHAI) + timedelta(minutes=1)
        if finished or at >= cutoff:
            return tomorrow
        candidate = max(at, start, retry_at) if retry_at is not None else max(at, start)
        return candidate if candidate < cutoff else tomorrow

    def to_dict(self):
        return asdict(self)


def truth(value) -> bool:
    return value is True or value == 1 or str(value).lower() in ("1", "true")


def decode(value: str) -> str:
    for _ in range(3):
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded
    return value


def server_batch(payload, depth=0):
    """Only call for an observed queryKqDkbc response, never arbitrary page data."""
    if depth > 7:
        return None
    if isinstance(payload, dict):
        if "code" in payload and str(payload["code"]) not in ("200", "0"):
            return None
        if payload.get("success") is False:
            return None
        if "dkbc" in payload and ("sfydk" in payload or "dksjfw" in payload):
            return payload
        for key in ("data", "result", "obj"):
            result = server_batch(payload.get(key), depth + 1)
            if result is not None:
                return result
    return None


def find_punch_menu(payload, depth=0):
    """Resolve by the menu's functional name, never by a cached route or image address."""
    if depth > 12:
        return None
    if isinstance(payload, dict):
        if (payload.get("success") is False or
                ("code" in payload and str(payload["code"]) not in ("200", "0"))):
            return None
        name = payload.get("name") or payload.get("cdmc") or payload.get("menuName") or ""
        url = payload.get("url") or payload.get("path") or payload.get("href") or ""
        if re.sub(r"\s+", "", str(name)) == "平安打卡" and isinstance(url, str) and url.strip():
            return {"name": "平安打卡", "url": url.strip()}
        for value in payload.values():
            if isinstance(value, (dict, list)):
                result = find_punch_menu(value, depth + 1)
                if result is not None:
                    return result
    elif isinstance(payload, list):
        for value in payload[:2000]:
            result = find_punch_menu(value, depth + 1)
            if result is not None:
                return result
    return None


def resolve_menu_url(value: str, hall_url=SERVICE_URL) -> str:
    """Accept a current same-origin menu route and discard historical punch state."""
    value = value.strip()
    if value.lower().startswith(("%2f", "%23")):
        value = decode(value)
    if "\\" in value or any(ord(character) < 32 for character in value):
        raise ValueError("菜单返回了无效入口。")
    parts, hall = urlsplit(value), urlsplit(hall_url)
    if parts.scheme or parts.netloc:
        if (parts.scheme != "https" or parts.hostname != hall.hostname or parts.port not in (None, 443)
                or parts.username or parts.password):
            raise ValueError("菜单入口不属于学校学工系统，已停止跳转。")
        target = parts
    elif parts.fragment:
        target = urlsplit(urlunsplit((hall.scheme, hall.netloc, parts.path or hall.path, parts.query, parts.fragment)))
    elif parts.path.startswith("/"):
        target = urlsplit(urlunsplit((hall.scheme, hall.netloc, hall.path, "", value)))
    else:
        raise ValueError("菜单返回的入口格式无法识别。")
    transient = {"dkbc", "dksjfw", "lx", "sfydk", "sfmdk", "dksj", "unavailable", "bxwz"}
    if target.fragment:
        route = urlsplit(target.fragment)
        query = {key: values for key, values in parse_qs(route.query, keep_blank_values=True).items() if key not in transient}
        fragment = route.path + ("?" + urlencode(query, doseq=True) if query else "")
        return urlunsplit((target.scheme, target.netloc, target.path, target.query, fragment))
    return urlunsplit(target)


def route_key(url: str) -> str:
    parts = urlsplit(url)
    return parts.fragment.split("?", 1)[0] if parts.fragment else parts.path


@dataclass
class PunchStatus:
    shift: str
    window: str
    punched: bool
    exempt: bool
    allowed: bool
    record_time: str = ""
    reason: str = ""
    source: str = "server"

    @classmethod
    def from_batch(cls, batch):
        return cls(
            str(batch.get("dkbc") or ""), str(batch.get("dksjfw") or ""),
            truth(batch.get("sfydk")), truth(batch.get("sfmdk")),
            batch.get("kdk") not in (False, 0, "0", "false"),
            str(batch.get("dksj") or ""), str(batch.get("bkyy") or ""), "server",
        )

    @classmethod
    def from_fresh_url(cls, url: str, expected_path=None):
        fragment = urlsplit(url).fragment
        if ((expected_path is not None and fragment.split("?", 1)[0] != expected_path)
                or not fragment.startswith("/") or "?" not in fragment):
            return None
        query = parse_qs(fragment.split("?", 1)[1], keep_blank_values=True)
        values = {key: decode(value[0]) for key, value in query.items()}
        if not {"dkbc", "dksjfw", "sfydk", "sfmdk"}.issubset(values):
            return None
        return cls(values["dkbc"], values["dksjfw"], truth(values["sfydk"]),
                   truth(values["sfmdk"]), not bool(values.get("unavailable")),
                   values.get("dksj", ""), values.get("unavailable", ""), "fresh-navigation")

    def record_is_today(self, at: datetime) -> bool:
        match = re.search(r"\d{4}-\d{2}-\d{2}", self.record_time)
        return bool(self.punched and (not match or match[0] == at.date().isoformat()))

    def within_school_window(self, at: datetime) -> bool:
        pairs = re.findall(r"(\d{1,2}:\d{2})\s*[-—–~～至]\s*(\d{1,2}:\d{2})", self.window)
        if not pairs:
            return False  # Missing rules must never silently allow submission.
        current = at.astimezone(SHANGHAI).time().replace(tzinfo=None)
        for start, end in pairs:
            try:
                begin, finish = (datetime.strptime(x, "%H:%M").time() for x in (start, end))
            except ValueError:
                continue
            if (begin <= current <= finish if begin <= finish else current >= begin or current <= finish):
                return True
        return False


@dataclass
class RunResult:
    code: str
    message: str
    completed: bool = False
    needs_user: bool = False


class AppError(Exception):
    def __init__(self, code: str, message: str, needs_user=False):
        super().__init__(message)
        self.code, self.message, self.needs_user = code, message, needs_user


class Cancelled(Exception):
    pass
