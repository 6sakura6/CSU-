"""Measure the full portable app, including this report, against a decimal 100 MB gate."""
from pathlib import Path
import json
import sys
from collections import defaultdict


def measure(root):
    root = Path(root).resolve()
    report_file = root / 'size-report.json'
    paths = [p for p in root.rglob('*') if p.is_file() and p != report_file]
    forbidden = [p.relative_to(root).as_posix() for p in paths if any(
        word in p.relative_to(root).as_posix().lower()
        for word in ['pyside6', 'qt6webengine', 'playwright', 'node.exe', '/numpy', '/_tcl', '/_tk_data'])]
    report = {'scope': 'entire_unpacked_application_including_report', 'unit': 'decimal MB',
        'budget_bytes': 100_000_000, 'unpacked_bytes': 0, 'unpacked_mb': 0,
        'files': len(paths) + 1, 'forbidden_heavy_dependencies': forbidden,
        'system_dependencies': ['Windows 10/11 x64', '.NET Framework 4.8+',
            'shared Microsoft Edge WebView2 Runtime', 'Microsoft Edge or Chrome for browser probe'],
        'runtime_bundled': False, 'runtime_auto_download': False,
        'user_data_and_shared_runtime_included': False,
        'note': 'User settings, WebView2 cache and shared system Runtime are measured separately, not part of this initial portable bundle.',
        'largest_files': [(p.relative_to(root).as_posix(), p.stat().st_size)
            for p in sorted(paths, key=lambda x: -x.stat().st_size)[:10]]}
    base = sum(p.stat().st_size for p in paths)
    for _ in range(10):
        data = json.dumps(report, ensure_ascii=False, indent=2).encode('utf-8')
        total = base + len(data)
        if report['unpacked_bytes'] == total:
            break
        report.update(unpacked_bytes=total, unpacked_mb=round(total / 1_000_000, 3))
    report_file.write_bytes(json.dumps(report, ensure_ascii=False, indent=2).encode('utf-8'))
    actual = sum(p.stat().st_size for p in root.rglob('*') if p.is_file())
    assert actual == report['unpacked_bytes']
    print(json.dumps({'root': str(root), 'bytes': actual, 'MB': report['unpacked_mb'],
        'forbidden': forbidden}, ensure_ascii=False))
    return 0 if actual < 100_000_000 and not forbidden else 1


if __name__ == '__main__':
    raise SystemExit(measure(sys.argv[1]))
