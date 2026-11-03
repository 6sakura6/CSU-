"""Legacy Playwright backend for the shared browser workflow."""
from browser_flow import (BrowserFlow, safe_login_url, valid_location,
    LOCATION_STATE_JS, NATIVE_LOCATION_JS, MANUAL_LOCATION_INIT_JS)
from core import AppError, Cancelled, RunResult


class BrowserRunner(BrowserFlow):
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
