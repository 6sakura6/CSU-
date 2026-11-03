"""Opt-in, current-user Windows logon registration. No credentials in commands."""
from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import sys
import uuid
import winreg

from storage import app_dir

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def startup_command():
    if getattr(sys, "frozen", False):
        arguments = [str(Path(sys.executable).resolve()), "--startup"]
    else:
        interpreter = Path(sys.executable).resolve()
        windowed = interpreter.with_name("pythonw.exe")
        arguments = [str(windowed if windowed.exists() else interpreter),
                     str(Path(__file__).resolve().with_name("app.py")), "--startup"]
    return subprocess.list2cmdline(arguments)


class StartupRegistration:
    def __init__(self, directory=None, command=None, registry_path=RUN_KEY):
        self.directory = Path(directory) if directory is not None else app_dir()
        self.command = command if command is not None else startup_command()
        identity = hashlib.sha256(str(self.directory.resolve()).casefold().encode()).hexdigest()[:12]
        self.value_name = "CSUPeaceCheckin_" + identity
        self.registry_path = registry_path

    def is_enabled(self):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.registry_path, 0, winreg.KEY_QUERY_VALUE) as key:
                value, kind = winreg.QueryValueEx(key, self.value_name)
                return kind == winreg.REG_SZ and value == self.command
        except FileNotFoundError:
            return False

    def set_enabled(self, enabled):
        if enabled:
            if len(self.command) > 260 or any(character in self.command for character in ("\0", "\n", "\r")):
                raise ValueError("自启动命令无效或路径过长，请将程序放到较短的路径后重试。")
            with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, self.registry_path, 0, winreg.KEY_SET_VALUE) as key:
                winreg.SetValueEx(key, self.value_name, 0, winreg.REG_SZ, self.command)
        else:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.registry_path, 0, winreg.KEY_SET_VALUE) as key:
                    winreg.DeleteValue(key, self.value_name)
            except FileNotFoundError:
                pass


def startup_is_authorized(settings, registration, consent):
    return (settings.launch_at_login is True and settings.startup_consent == consent
            and registration.is_enabled())


def self_test_registration():
    # An isolated test key is not an autorun location and can never start a program.
    key = r"Software\CSUPeaceCheckinSelfTest_" + uuid.uuid4().hex
    registration = StartupRegistration(registry_path=key)
    try:
        registration.set_enabled(True)
        created = registration.is_enabled()
        registration.set_enabled(False)
        return created and not registration.is_enabled()
    finally:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
        except FileNotFoundError:
            pass
