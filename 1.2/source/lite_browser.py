"""Production-capable CDP runner for the shared school workflow."""
from browser_flow import BrowserFlow, safe_login_url
from cdp_browser import BrowserContext, normalize_state
from cdp_transport import BrowserProcess, DriverError
from core import AppError, Cancelled, RunResult, now_cn


class LiteBrowserRunner(BrowserFlow):
    def __init__(self, store, settings, log, stop, scheduled=False, *,
                 channel='auto', headless=False, fixture=None, clock=now_cn):
        self._clock = clock
        super().__init__(store, settings, log, stop, scheduled)
        self.channel, self.headless, self.fixture = channel, headless, fixture
        self.last_cleanup_ok = None

    def now(self):
        return self._clock()

    def run(self, dry_run=False):
        browser = BrowserProcess(self.stop, channel=self.channel, headless=self.headless)
        context = None
        try:
            self.check_cancel()
            try:
                saved = normalize_state(self.store.load_secret('session.dat'))
            except Exception:
                saved = None
                self.log('保存的登录状态不可用，将使用账户密码重新登录。')
            browser.start()
            try:
                context = BrowserContext(browser.client, saved, fixture=self.fixture,
                    credentials_guard=safe_login_url)
            except DriverError:
                # An expired/unsupported cookie must not permanently block normal login.
                self.log('保存的浏览器会话未能恢复，将重新登录。')
                context = BrowserContext(browser.client, fixture=self.fixture,
                    credentials_guard=safe_login_url)
            self.context = context
            return self.perform(context, dry_run)
        except Cancelled:
            return RunResult('cancelled', '本次操作已停止。若刚好已提交，下次运行会先查询记录。')
        except AppError as error:
            return RunResult(error.code, error.message, needs_user=error.needs_user)
        except DriverError:
            return RunResult('browser', '浏览器操作未完成，请检查 Edge/Chrome、网络或人工验证提示。', needs_user=True)
        except Exception as error:
            if self.stop.is_set():
                return RunResult('cancelled', '本次操作已停止。')
            return RunResult('network', '浏览器操作未完成（' + type(error).__name__ + '）；请检查网络后重试。')
        finally:
            if context:
                if self.logged_in and not self.stop.is_set():
                    try:
                        self.store.save_secret('session.dat', context.storage_state())
                    except Exception:
                        self.log('登录状态未能保存，下次将使用账户密码重新登录。')
                try:
                    context.close()
                except Exception:
                    pass
            browser.close()
            self.last_cleanup_ok = browser.cleanup_ok
            self.context = None
            if not browser.cleanup_ok:
                self.log('独立浏览器临时目录仍被占用，请退出程序后检查临时目录。', 'attention')
