"""Scheduling policy shared by the legacy GUI and the headless controller.

The host supplies Store, observable values, logging and platform callbacks.
"""
from datetime import timedelta
from core import SHANGHAI


class SchedulePolicy:
    def load_schedule_day(self, at):
        day = at.date().isoformat()
        if self.checked_day != day:
            self.checked_day = day
            # A previous day's retry must never delay the new day's first task.
            self.next_attempt = None
            try:
                if self.store.done(day):
                    self.completed_day = day
            except Exception:
                self.log("本地完成记录不可用，将以学校服务端状态为准。")

    def day_delay(self, day, *, create=False):
        key = day.isoformat()
        if key not in self.daily_delays or (create and key not in self.prepared_days):
            self.daily_delays[key] = self.store.schedule_delay(
                key, create=create, limit=self.settings.random_delay_limit())
            if create:
                self.prepared_days.add(key)
        return min(self.daily_delays[key], self.settings.random_delay_limit())

    def schedule_error(self):
        # Never replace an unreadable saved draw with a new random time.
        self.enabled = False
        self.next_attempt = None
        self.set_awake(False)
        self.update_schedule_button()
        self.status.set("随机排期无法读取或保存，已停止打卡定时。")
        self.next_text.set("请检查 data 目录权限及原 Windows 账户后重新开始定时。")
        self.log("随机排期无法读取或保存，已停止打卡定时，避免重启或重试时变更排期。", "attention")

    def refresh_next_attempt(self, at):
        at = at.astimezone(SHANGHAI)
        day = at.date()
        try:
            if self.completed_day == day.isoformat():
                self.day_delay(day + timedelta(days=1), create=True)
            candidate = self.settings.next_run(at, finished=self.completed_day == day.isoformat(),
                retry_at=self.next_attempt, delay_minutes=self.day_delay(day))
            delay = self.day_delay(candidate.date())
            self.next_attempt = self.settings.next_run(at, finished=self.completed_day == day.isoformat(),
                retry_at=self.next_attempt, delay_minutes=self.day_delay(day), next_delay_minutes=delay)
        except Exception:
            self.schedule_error()
            return False
        self.next_text.set("下一次检查：" + self.next_attempt.strftime("%m月%d日 %H:%M") +
                           f"（北京时间；当日随机延迟 {delay} 分钟）")
        return True
