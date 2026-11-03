"""Import encrypted legacy data with validation and an encrypted rollback backup."""
from dataclasses import replace
from datetime import datetime
import json
from pathlib import Path
import tempfile
import uuid

from storage import Store, atomic_write

PROFILE_FILES = ('credentials.dat', 'settings.json', 'location.dat', 'network.dat',
                 'session.dat', 'completed.dat', 'schedule.dat')


def inspect_profile(directory):
    root = Path(directory).resolve()
    if not root.is_dir() or not (root/'credentials.dat').is_file():
        raise ValueError('请选择旧版包含 credentials.dat 的 data 文件夹。')
    payload = {name: (root/name).read_bytes() for name in PROFILE_FILES if (root/name).is_file()}
    # Validate a copied snapshot. Never modify the selected source folder.
    with tempfile.TemporaryDirectory(prefix='CSU_Profile_Validate_') as folder:
        copied = Path(folder)
        for name, data in payload.items():
            (copied/name).write_bytes(data)
        store = Store(copied)
        try:
            settings = store.settings()
            credentials = store.credentials()
            if (not isinstance(credentials, dict) or not isinstance(credentials.get('username'), str)
                    or not credentials['username'].strip() or not isinstance(credentials.get('password'), str)
                    or not credentials['password']):
                raise ValueError()
            for name in PROFILE_FILES:
                if name.endswith('.dat') and name in payload:
                    store.load_secret(name)
            if settings.location_mode == 'manual' and store.manual_location() is None:
                raise ValueError()
        except Exception:
            raise ValueError('旧版配置无法完整读取，请使用保存配置的 Windows 账户，或先在旧版重新保存。') from None
    for name, data in payload.items():
        if (root/name).read_bytes() != data:
            raise ValueError('旧版正在更新配置，请暂停旧程序后再导入。')
    return payload, settings


def import_profile(source, target):
    source, target = Path(source).resolve(), Path(target).resolve()
    if source == target:
        raise ValueError('选择的就是当前数据目录，无需导入。')
    payload, settings = inspect_profile(source)
    current = Store(target).settings()
    settings = replace(settings, auto_start=False, launch_at_login=current.launch_at_login,
                       startup_consent=current.startup_consent)
    payload['settings.json'] = json.dumps(settings.to_dict(), ensure_ascii=False, indent=2).encode('utf-8')
    backup = target / ('import-backup-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6])
    backup.mkdir()
    old = {name: (target/name).read_bytes() for name in PROFILE_FILES if (target/name).is_file()}
    for name, data in old.items():
        atomic_write(backup/name, data)
    written = []
    try:
        for name in PROFILE_FILES:
            path = target/name
            if name in payload:
                atomic_write(path, payload[name])
            elif path.exists():
                path.unlink()  # Exact known file within the explicitly selected target.
            written.append(name)
    except Exception:
        try:
            for name in written:
                if name in old:
                    atomic_write(target/name, old[name])
                else:
                    (target/name).unlink(missing_ok=True)
        except Exception:
            raise OSError('导入未完成，导入前的加密备份已保留在 data/import-backup 目录中。') from None
        raise OSError('导入未完成，原配置已恢复。') from None
    return {'files': len(payload), 'backup': backup.name, 'schedule_paused': True}
