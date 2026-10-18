"""Windows user-bound encryption. Secrets are never written as plaintext."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path
import hashlib
import json
import os
import sys
from core import Settings, ManualLocation


def app_dir() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent


class Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def crypt(data: bytes, decrypt=False) -> bytes:
    if os.name != "nt":
        raise OSError("本程序需要 Windows。")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    source_buf = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(source_buf, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    common = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.POINTER(Blob), ctypes.c_void_p,
              ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes, function.restype = common, wintypes.BOOL
    kernel32.LocalFree.argtypes, kernel32.LocalFree.restype = [ctypes.c_void_p], ctypes.c_void_p
    # CRYPTPROTECT_UI_FORBIDDEN; deliberately no LOCAL_MACHINE flag.
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise OSError("本地加密文件无法读取；请使用保存它的 Windows 账户重新配置。")
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(output.pbData)


def atomic_write(path: Path, content: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


class Store:
    def __init__(self, root: Path | None = None):
        self.root = root or app_dir() / "data"
        self.root.mkdir(parents=True, exist_ok=True)

    def save_secret(self, filename, value):
        raw = json.dumps(value, ensure_ascii=False).encode("utf-8")
        atomic_write(self.root / filename, crypt(raw))

    def load_secret(self, filename, default=None):
        file = self.root / filename
        if not file.exists():
            return default
        return json.loads(crypt(file.read_bytes(), decrypt=True).decode("utf-8"))

    def credentials(self):
        return self.load_secret("credentials.dat")

    def manual_location(self):
        saved = self.load_secret("location.dat")
        if saved is None:
            return None
        return ManualLocation.from_inputs(saved["latitude"], saved["longitude"])

    def save_manual_location(self, location: ManualLocation):
        checked = ManualLocation.from_inputs(location.latitude, location.longitude)
        self.save_secret("location.dat", {"latitude": checked.latitude, "longitude": checked.longitude})

    def save_credentials(self, username, password):
        if not username.strip() or not password:
            raise ValueError("请填写校园账户和密码。")
        try:
            previous = self.credentials()
        except Exception:
            previous = None
        self.save_secret("credentials.dat", {"username": username.strip(), "password": password})
        if previous is None or previous.get("username") != username.strip():
            # Different account must never inherit the prior account's browser session.
            self.save_secret("session.dat", None)

    def account_key(self):
        credentials = self.credentials()
        if not credentials:
            return ""
        return hashlib.sha256(credentials["username"].encode()).hexdigest()[:24]

    def settings(self):
        path = self.root / "settings.json"
        if not path.exists():
            return Settings()
        raw = json.loads(path.read_text(encoding="utf-8"))
        return Settings(**{key: value for key, value in raw.items() if key in Settings.__dataclass_fields__}).validate()

    def save_settings(self, settings):
        settings.validate()
        atomic_write(self.root / "settings.json", json.dumps(settings.to_dict(), ensure_ascii=False, indent=2).encode())

    def done(self, day):
        return self.load_secret("completed.dat", {}).get(self.account_key()) == day

    def mark_done(self, day):
        completed = self.load_secret("completed.dat", {})
        completed[self.account_key()] = day
        self.save_secret("completed.dat", completed)


class SingleInstance:
    @staticmethod
    def name(directory):
        return "Local\\CSU_Peace_Checkin_" + hashlib.sha256(str(directory.resolve()).lower().encode()).hexdigest()[:24]

    @staticmethod
    def kernel_api():
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [wintypes.HANDLE], wintypes.BOOL
        kernel.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR]
        kernel.OpenEventW.restype = wintypes.HANDLE
        kernel.SetEvent.argtypes, kernel.SetEvent.restype = [wintypes.HANDLE], wintypes.BOOL
        return kernel

    @classmethod
    def signal(cls, directory, command):
        if command not in ("show", "exit"):
            raise ValueError("Unsupported instance command.")
        kernel = cls.kernel_api()
        event = kernel.OpenEventW(0x0002, False, cls.name(directory) + "_" + command)
        if not event:
            return False
        try:
            return bool(kernel.SetEvent(event))
        finally:
            kernel.CloseHandle(event)

    def __init__(self, directory: Path):
        self.kernel = self.kernel_api()
        self.events = {}
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateMutexW.restype = wintypes.HANDLE
        name = self.name(directory)
        self.handle = self.kernel.CreateMutexW(None, False, name)
        error = ctypes.get_last_error()
        if not self.handle:
            raise OSError("无法创建程序运行锁。")
        if error == 183:
            self.close()
            raise OSError("这个目录中的打卡助手已经在运行，请查看任务栏。")
        self.kernel.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateEventW.restype = wintypes.HANDLE
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        for command in ("exit", "show"):
            event = self.kernel.CreateEventW(None, False, False, name + "_" + command)
            if not event:
                self.close()
                raise OSError("无法创建程序窗口通知通道。")
            self.events[command] = event

    def command(self):
        for command, event in self.events.items():
            if self.kernel.WaitForSingleObject(event, 0) == 0:
                return command
        return None

    def close(self):
        for event in self.events.values():
            self.kernel.CloseHandle(event)
        self.events.clear()
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None
