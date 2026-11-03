"""Headless entry point for the new backend, before full frontend integration."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import time


def main():
    parser = argparse.ArgumentParser(description='轻量业务后端；真实操作需明确指定数据目录。')
    parser.add_argument('--self-test', action='store_true', help='只运行本地合成网页的端到端验证')
    parser.add_argument('--channel', choices=['edge', 'chrome', 'auto'], default='auto')
    parser.add_argument('--report')
    parser.add_argument('--data-dir')
    parser.add_argument('--mode', choices=['check', 'manual', 'network'], default='check')
    parser.add_argument('--accept-disclaimer', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        from backend_selftest import run_backend_validation
        results = [run_backend_validation(channel) for channel in
            (('edge', 'chrome') if args.channel == 'auto' else (args.channel,))]
        result = {'passed': all(item['passed'] for item in results), 'browsers': results}
    else:
        from service import CheckinService, DISCLAIMER_TEXT
        from storage import Store
        from lite_browser import LiteBrowserRunner
        if not args.data_dir or not args.accept_disclaimer:
            parser.error('真实操作需提供 --data-dir 和 --accept-disclaimer。\n' + DISCLAIMER_TEXT)
        def factory(*items, **options):
            return LiteBrowserRunner(*items, **options, channel=args.channel)
        service = CheckinService(Store(Path(args.data_dir).resolve()), runner_factory=factory,
            restore=False, automatic=False)
        try:
            service.launch(args.mode)
            while service.snapshot()['busy']:
                service.tick()
                time.sleep(0.1)
            result = asdict(service.last_result)
            result['passed'] = result['code'] in ('checked', 'success', 'already_done', 'exempt', 'campus_ready')
        except KeyboardInterrupt:
            service.stop_current()
            result = {'passed': False, 'code': 'cancelled', 'message': '已请求停止。'}
        finally:
            service.shutdown()
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.report:
        path = Path(args.report).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
    print(text)
    return 0 if result.get('passed') else 1


if __name__ == '__main__':
    raise SystemExit(main())
