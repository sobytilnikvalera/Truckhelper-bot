import datetime
import os
import sqlite3
from zoneinfo import ZoneInfo

# Timezone
_TZ = ZoneInfo(os.environ.get("BOT_TIMEZONE", "Europe/Berlin"))


def _now():
    return datetime.datetime.now(_TZ)


class ECRules:
    # EC 561/2006 limits
    MAX_CONTINUOUS_DRIVING = datetime.timedelta(hours=4, minutes=30)
    MIN_BREAK_AFTER_CONTINUOUS = datetime.timedelta(minutes=45)
    MAX_DAILY_DRIVING_NORMAL = datetime.timedelta(hours=9)
    MAX_DAILY_DRIVING_EXTENDED = datetime.timedelta(hours=10)
    MAX_WEEKLY_DRIVING = datetime.timedelta(hours=56)
    MAX_BI_WEEKLY_DRIVING = datetime.timedelta(hours=90)
    MIN_DAILY_REST_NORMAL = datetime.timedelta(hours=11)
    MIN_DAILY_REST_REDUCED = datetime.timedelta(hours=9)
    MIN_WEEKLY_REST_NORMAL = datetime.timedelta(hours=45)
    MIN_WEEKLY_REST_REDUCED = datetime.timedelta(hours=24)

    # Weekly allowances
    MAX_EXTENDED_DRIVING_PER_WEEK = 2  # 2x 10h per week
    MAX_REDUCED_REST_PER_WEEK = 3  # 3x 9h rest per week

    def __init__(self, user_id, db_name='truckhelper.db'):
        self.user_id = user_id
        self.db_name = db_name
        self._load_user_state()

    def _get_db_connection(self):
        conn = sqlite3.connect(self.db_name)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _parse_dt(dt_str):
        """Parse datetime string and ensure it has timezone info."""
        dt = datetime.datetime.fromisoformat(dt_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_TZ)
        return dt

    def _get_week_start(self):
        today = datetime.date.today()
        return (today - datetime.timedelta(days=today.weekday())).isoformat()

    def _load_user_state(self):
        conn = self._get_db_connection()
        cursor = conn.cursor()

        # Load current shift/driving status
        cursor.execute(
            "SELECT * FROM driving_sessions WHERE user_id = ? AND end_time IS NULL ORDER BY start_time DESC LIMIT 1",
            (self.user_id,))
        active_session = cursor.fetchone()
        if active_session:
            if active_session["session_type"] == "driving":
                self.current_driving_start_time = self._parse_dt(active_session["start_time"])
                self.current_shift_start_time = self._get_last_shift_start_time(conn)
            elif active_session["session_type"] == "shift":
                self.current_shift_start_time = self._parse_dt(active_session["start_time"])
                self.current_driving_start_time = None
        else:
            self.current_shift_start_time = None
            self.current_driving_start_time = None

        # Load daily stats
        today = datetime.date.today().isoformat()
        cursor.execute("SELECT * FROM daily_stats WHERE user_id = ? AND date = ?", (self.user_id, today))
        daily_stat = cursor.fetchone()
        if daily_stat:
            self.daily_driving_minutes = daily_stat["daily_driving_minutes"]
            self.daily_rest_minutes = daily_stat["daily_rest_minutes"]
        else:
            self.daily_driving_minutes = 0
            self.daily_rest_minutes = 0
            cursor.execute("INSERT INTO daily_stats (user_id, date) VALUES (?, ?)", (self.user_id, today))
            conn.commit()

        # Load weekly stats
        week_start = self._get_week_start()
        cursor.execute("SELECT * FROM weekly_stats WHERE user_id = ? AND week_start_date = ?",
                       (self.user_id, week_start))
        weekly_stat = cursor.fetchone()
        if weekly_stat:
            self.weekly_driving_minutes = weekly_stat["weekly_driving_minutes"]
            self.extended_driving_count = weekly_stat["extended_driving_count"]
            self.reduced_rest_count = weekly_stat["reduced_rest_count"]
        else:
            self.weekly_driving_minutes = 0
            self.extended_driving_count = 0
            self.reduced_rest_count = 0
            cursor.execute("INSERT INTO weekly_stats (user_id, week_start_date) VALUES (?, ?)",
                           (self.user_id, week_start))
            conn.commit()

        # Calculate bi-weekly driving
        two_weeks_ago = (datetime.date.today() - datetime.timedelta(days=14)).isoformat()
        cursor.execute(
            "SELECT COALESCE(SUM(weekly_driving_minutes), 0) as total FROM weekly_stats WHERE user_id = ? AND week_start_date >= ?",
            (self.user_id, two_weeks_ago))
        result = cursor.fetchone()
        self.bi_weekly_driving_minutes = result["total"] if result else 0

        conn.close()

    def _get_last_shift_start_time(self, conn):
        cursor = conn.cursor()
        cursor.execute(
            "SELECT start_time FROM driving_sessions WHERE user_id = ? AND session_type = 'shift' AND end_time IS NULL ORDER BY start_time DESC LIMIT 1",
            (self.user_id,))
        result = cursor.fetchone()
        if result:
            return self._parse_dt(result["start_time"])
        return None

    def _update_driving_stats(self, duration_minutes):
        conn = self._get_db_connection()
        cursor = conn.cursor()
        today = datetime.date.today().isoformat()
        week_start = self._get_week_start()

        cursor.execute(
            "UPDATE daily_stats SET daily_driving_minutes = daily_driving_minutes + ? WHERE user_id = ? AND date = ?",
            (duration_minutes, self.user_id, today))
        cursor.execute(
            "UPDATE weekly_stats SET weekly_driving_minutes = weekly_driving_minutes + ? WHERE user_id = ? AND week_start_date = ?",
            (duration_minutes, self.user_id, week_start))

        conn.commit()
        conn.close()
        self._load_user_state()

    def start_shift(self):
        if self.current_shift_start_time:
            return "⚠️ Смена уже активна."
        self.current_shift_start_time = _now()
        conn = self._get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO driving_sessions (user_id, start_time, session_type) VALUES (?, ?, ?)",
                       (self.user_id, self.current_shift_start_time.isoformat(), "shift"))
        conn.commit()
        conn.close()

        # Record rest period if there was a previous shift
        self._record_rest_period()

        return "✅ Смена начата. Удачи на дороге!"

    def _record_rest_period(self):
        """Record the rest period between shifts."""
        conn = self._get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT end_time FROM driving_sessions WHERE user_id = ? AND session_type = 'shift' AND end_time IS NOT NULL ORDER BY end_time DESC LIMIT 1",
            (self.user_id,))
        last_shift_end = cursor.fetchone()

        if last_shift_end:
            rest_start = self._parse_dt(last_shift_end["end_time"])
            rest_end = self.current_shift_start_time
            rest_duration = rest_end - rest_start
            rest_minutes = int(rest_duration.total_seconds() / 60)

            # Determine rest type
            if rest_duration >= self.MIN_DAILY_REST_NORMAL:
                rest_type = "full"  # 11h+
            elif rest_duration >= self.MIN_DAILY_REST_REDUCED:
                rest_type = "reduced"  # 9-11h
                # Update reduced rest count for the week
                week_start = self._get_week_start()
                cursor.execute(
                    "UPDATE weekly_stats SET reduced_rest_count = reduced_rest_count + 1 WHERE user_id = ? AND week_start_date = ?",
                    (self.user_id, week_start))
            else:
                rest_type = "insufficient"  # <9h

            week_start = self._get_week_start()
            cursor.execute(
                "INSERT INTO rest_sessions (user_id, start_time, end_time, duration_minutes, rest_type, week_start_date) VALUES (?, ?, ?, ?, ?, ?)",
                (self.user_id, rest_start.isoformat(), rest_end.isoformat(), rest_minutes, rest_type, week_start))
            conn.commit()

        conn.close()

    def end_shift(self):
        if not self.current_shift_start_time:
            return "⚠️ Нет активной смены для завершения."
        if self.current_driving_start_time:
            return "⚠️ Нельзя завершить смену во время вождения. Сначала завершите вождение."

        shift_end_time = _now()
        shift_duration = shift_end_time - self.current_shift_start_time

        conn = self._get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE driving_sessions SET end_time = ?, duration = ? WHERE user_id = ? AND session_type = 'shift' AND end_time IS NULL",
            (shift_end_time.isoformat(), int(shift_duration.total_seconds() / 60), self.user_id))
        conn.commit()
        conn.close()

        self.current_shift_start_time = None
        return f"✅ Смена завершена.\n⏱ Продолжительность: {self._format_timedelta(shift_duration)}.\n\n😴 Хорошего отдыха!"

    def start_driving(self):
        if not self.current_shift_start_time:
            return "⚠️ Нельзя начать вождение без активной смены. Сначала начните смену."
        if self.current_driving_start_time:
            return "⚠️ Вождение уже активно."

        # Check for required break after continuous driving
        last_driving_session = self._get_last_completed_driving_session()
        if last_driving_session:
            last_driving_end = self._parse_dt(last_driving_session["end_time"])
            time_since_last_driving = _now() - last_driving_end
            if (last_driving_session["duration"] and
                    last_driving_session["duration"] >= self.MAX_CONTINUOUS_DRIVING.total_seconds() / 60 and
                    time_since_last_driving < self.MIN_BREAK_AFTER_CONTINUOUS):
                remaining_break = self.MIN_BREAK_AFTER_CONTINUOUS - time_since_last_driving
                return f"⚠️ Необходимо отдохнуть ещё {self._format_timedelta(remaining_break)} перед началом вождения."

        # Check daily driving limit (considering weekly 2x10h allowance)
        daily_limit_minutes = self.MAX_DAILY_DRIVING_NORMAL.total_seconds() / 60
        if self.extended_driving_count < self.MAX_EXTENDED_DRIVING_PER_WEEK:
            max_today = self.MAX_DAILY_DRIVING_EXTENDED.total_seconds() / 60
        else:
            max_today = daily_limit_minutes

        if self.daily_driving_minutes >= max_today:
            return f"🛑 Достигнут дневной лимит вождения ({int(max_today / 60)}ч). Необходим отдых."

        # Check weekly driving limit
        if self.weekly_driving_minutes >= self.MAX_WEEKLY_DRIVING.total_seconds() / 60:
            return "🛑 Достигнут недельный лимит вождения (56 часов)."

        # Check bi-weekly driving limit
        if self.bi_weekly_driving_minutes >= self.MAX_BI_WEEKLY_DRIVING.total_seconds() / 60:
            return "🛑 Достигнут лимит вождения за две недели (90 часов)."

        self.current_driving_start_time = _now()
        conn = self._get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO driving_sessions (user_id, start_time, session_type) VALUES (?, ?, ?)",
                       (self.user_id, self.current_driving_start_time.isoformat(), "driving"))
        conn.commit()
        conn.close()
        return "✅ Вождение начато. Будьте внимательны!"

    def end_driving(self):
        if not self.current_driving_start_time:
            return "⚠️ Нет активного вождения для завершения."

        driving_end_time = _now()
        driving_duration = driving_end_time - self.current_driving_start_time
        duration_minutes = int(driving_duration.total_seconds() / 60)

        conn = self._get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE driving_sessions SET end_time = ?, duration = ? WHERE user_id = ? AND session_type = 'driving' AND end_time IS NULL",
            (driving_end_time.isoformat(), duration_minutes, self.user_id))
        conn.commit()
        conn.close()

        self._update_driving_stats(duration_minutes)

        # Check if this session used extended (10h) driving
        if self.daily_driving_minutes > self.MAX_DAILY_DRIVING_NORMAL.total_seconds() / 60:
            conn = self._get_db_connection()
            cursor = conn.cursor()
            week_start = self._get_week_start()
            cursor.execute(
                "UPDATE weekly_stats SET extended_driving_count = extended_driving_count + 1 WHERE user_id = ? AND week_start_date = ? AND extended_driving_count < ?",
                (self.user_id, week_start, self.MAX_EXTENDED_DRIVING_PER_WEEK))
            conn.commit()
            conn.close()

        self.current_driving_start_time = None

        # Build response with warnings
        response = f"✅ Вождение завершено.\n⏱ Продолжительность: {self._format_timedelta(driving_duration)}."

        # Check remaining time
        remaining = self._get_remaining_daily_driving()
        if remaining <= 60:
            response += f"\n\n⚠️ Осталось вождения сегодня: {int(remaining)} мин!"

        return response

    def _get_remaining_daily_driving(self):
        """Returns remaining daily driving in minutes."""
        if self.extended_driving_count < self.MAX_EXTENDED_DRIVING_PER_WEEK:
            max_today = self.MAX_DAILY_DRIVING_EXTENDED.total_seconds() / 60
        else:
            max_today = self.MAX_DAILY_DRIVING_NORMAL.total_seconds() / 60
        return max(0, max_today - self.daily_driving_minutes)

    def _get_last_completed_driving_session(self):
        conn = self._get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM driving_sessions WHERE user_id = ? AND session_type = 'driving' AND end_time IS NOT NULL ORDER BY end_time DESC LIMIT 1",
            (self.user_id,))
        session = cursor.fetchone()
        conn.close()
        return session

    def get_status(self):
        status = {
            "daily_driving": self.daily_driving_minutes,
            "weekly_driving": self.weekly_driving_minutes,
            "bi_weekly_driving": self.bi_weekly_driving_minutes,
            "remaining_continuous_driving": self._get_remaining_continuous_driving(),
            "required_break": self._get_required_break_time(),
            "daily_driving_limit_status": self._get_daily_driving_limit_status(),
            "weekly_driving_limit_status": self._get_weekly_driving_limit_status(),
            "bi_weekly_driving_limit_status": self._get_bi_weekly_driving_limit_status(),
            "daily_rest_status": self._get_daily_rest_status(),
            "weekly_rest_info": self._get_weekly_rest_info(),
            "extended_driving_info": self._get_extended_driving_info(),
        }
        return status

    def _get_remaining_continuous_driving(self):
        if self.current_driving_start_time:
            continuous_driving = _now() - self.current_driving_start_time
            remaining = self.MAX_CONTINUOUS_DRIVING - continuous_driving
            if remaining.total_seconds() < 0:
                return "🛑 Превышено! Требуется пауза 45 минут!"
            return self._format_timedelta(remaining)
        return "Не в режиме вождения."

    def _get_required_break_time(self):
        last_driving_session = self._get_last_completed_driving_session()
        if last_driving_session:
            last_driving_end = self._parse_dt(last_driving_session["end_time"])
            time_since_last_driving = _now() - last_driving_end
            if (last_driving_session["duration"] and
                    last_driving_session["duration"] >= self.MAX_CONTINUOUS_DRIVING.total_seconds() / 60 and
                    time_since_last_driving < self.MIN_BREAK_AFTER_CONTINUOUS):
                remaining_break = self.MIN_BREAK_AFTER_CONTINUOUS - time_since_last_driving
                return f"Необходимо ещё {self._format_timedelta(remaining_break)}."
        return "Перерыв не требуется."

    def _get_daily_driving_limit_status(self):
        remaining = self._get_remaining_daily_driving()
        if remaining > 0:
            can_extend = self.extended_driving_count < self.MAX_EXTENDED_DRIVING_PER_WEEK
            if can_extend and self.daily_driving_minutes < self.MAX_DAILY_DRIVING_NORMAL.total_seconds() / 60:
                return f"Осталось: {self._format_timedelta(datetime.timedelta(minutes=remaining))} (можно продлить до 10ч)"
            else:
                return f"Осталось: {self._format_timedelta(datetime.timedelta(minutes=remaining))}"
        return "Дневной лимит исчерпан."

    def _get_weekly_driving_limit_status(self):
        remaining = self.MAX_WEEKLY_DRIVING.total_seconds() / 60 - self.weekly_driving_minutes
        if remaining > 0:
            return f"Осталось: {self._format_timedelta(datetime.timedelta(minutes=remaining))}"
        return "Недельный лимит исчерпан."

    def _get_bi_weekly_driving_limit_status(self):
        remaining = self.MAX_BI_WEEKLY_DRIVING.total_seconds() / 60 - self.bi_weekly_driving_minutes
        if remaining > 0:
            return f"Осталось: {self._format_timedelta(datetime.timedelta(minutes=remaining))}"
        return "Двухнедельный лимит исчерпан."

    def _get_daily_rest_status(self):
        conn = self._get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT end_time FROM driving_sessions WHERE user_id = ? AND session_type = 'shift' AND end_time IS NOT NULL ORDER BY end_time DESC LIMIT 1",
            (self.user_id,))
        last_shift_end = cursor.fetchone()
        conn.close()

        if last_shift_end and not self.current_shift_start_time:
            last_shift_end_time = self._parse_dt(last_shift_end["end_time"])
            time_since_last_shift = _now() - last_shift_end_time

            if time_since_last_shift >= self.MIN_DAILY_REST_NORMAL:
                return f"✅ Полный отдых выполнен ({self._format_timedelta(time_since_last_shift)})."
            elif time_since_last_shift >= self.MIN_DAILY_REST_REDUCED:
                if self.reduced_rest_count < self.MAX_REDUCED_REST_PER_WEEK:
                    return f"✅ Сокращённый отдых ({self._format_timedelta(time_since_last_shift)}). Можно начинать."
                else:
                    remaining = self.MIN_DAILY_REST_NORMAL - time_since_last_shift
                    return f"⚠️ Сокращённый отдых недоступен (использованы все 3 за неделю). Ждите ещё {self._format_timedelta(remaining)}."
            else:
                remaining_full = self.MIN_DAILY_REST_NORMAL - time_since_last_shift
                remaining_reduced = self.MIN_DAILY_REST_REDUCED - time_since_last_shift
                if remaining_reduced.total_seconds() > 0 and self.reduced_rest_count < self.MAX_REDUCED_REST_PER_WEEK:
                    return f"⏳ До сокращённого отдыха (9ч): {self._format_timedelta(remaining_reduced)}\n⏳ До полного отдыха (11ч): {self._format_timedelta(remaining_full)}"
                else:
                    return f"⏳ До полного отдыха (11ч): {self._format_timedelta(remaining_full)}"
        elif self.current_shift_start_time:
            return "Смена активна."
        return "Нет данных (начните первую смену)."

    def get_current_driving_session_start_time(self):
        """Returns the start time of the current driving session if active, otherwise None."""
        return self.current_driving_start_time

    def _get_weekly_rest_info(self):
        """Info about weekly rest allowances."""
        reduced_remaining = self.MAX_REDUCED_REST_PER_WEEK - self.reduced_rest_count
        return f"Сокращённый отдых (9ч): использовано {self.reduced_rest_count}/{self.MAX_REDUCED_REST_PER_WEEK}, осталось {reduced_remaining}"

    def _get_extended_driving_info(self):
        """Info about extended driving allowances per week."""
        extended_remaining = self.MAX_EXTENDED_DRIVING_PER_WEEK - self.extended_driving_count
        return f"Продлённое вождение (10ч): использовано {self.extended_driving_count}/{self.MAX_EXTENDED_DRIVING_PER_WEEK}, осталось {extended_remaining}"

    def _format_timedelta(self, td):
        total_seconds = int(td.total_seconds())
        if total_seconds < 0:
            total_seconds = abs(total_seconds)
            sign = "-"
        else:
            sign = ""

        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            return f"{sign}{hours}ч {minutes}м"
        elif minutes > 0:
            return f"{sign}{minutes}м"
        else:
            return f"{sign}{seconds}с"
