# ====================== ЖУРНАЛ СМЕН ======================

DAY_NAMES_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

def _build_journal_text(user_id):
    """Красивая таблица журнала за 2 недели"""
    conn = get_db_connection()
    cursor = conn.cursor()

    today = datetime.now(TZ).date()
    text = "📓 <b>Журнал смен за 2 недели</b>\n\n"
    text += "День       | Смена    | Отдых    | Вождение  | 10-й час\n"
    text += "────────────────────────────────────────────────────\n"

    week1_driving = 0.0
    week2_driving = 0.0
    current_week_start = today - timedelta(days=today.weekday())

    for i in range(14):
        day = today - timedelta(days=i)
        day_str = day.isoformat()
        day_name = DAY_NAMES_RU[day.weekday()]

        cursor.execute("""
            SELECT shift_duration, rest_hours, driving_hours, used_10th_hour 
            FROM driver_shifts 
            WHERE user_id = ? AND date = ?
        """, (user_id, day_str))
        row = cursor.fetchone()

        if row:
            s = row["shift_duration"] or 0
            r = row["rest_hours"] or 0
            d = row["driving_hours"] or 0
            ten = "⚡" if row["used_10th_hour"] else ""
            line = f"{day_name} {day.strftime('%d.%m')} | ⏰{s:.1f}ч | 🛏{r:.1f}ч | 🎯{d:.1f}ч | {ten}"
        else:
            line = f"{day_name} {day.strftime('%d.%m')} | —"

        text += line + "\n"

        if day >= current_week_start:
            week2_driving += (row["driving_hours"] or 0) if row else 0
        else:
            week1_driving += (row["driving_hours"] or 0) if row else 0

    total = week1_driving + week2_driving
    text += "\n━━━━━━━━━━━━━━━\n"
    text += f"<b>Неделя 1:</b> {week1_driving:.1f}ч вождения\n"
    text += f"<b>Неделя 2:</b> {week2_driving:.1f}ч вождения\n"
    text += f"<b>Всего за 2 недели:</b> {total:.1f}ч / 90ч\n"

    conn.close()
    return text


def add_shift_to_journal(user_id: int, shift_duration: float = 0, rest_hours: float = 0, driving_hours: float = 0, used_10th_hour: bool = False, notes: str = ""):
    """Автоматическая запись смены в журнал"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        date_str = datetime.now(TZ).date().isoformat()
        
        cursor.execute('''
            INSERT OR REPLACE INTO driver_shifts 
            (user_id, date, shift_duration, rest_hours, driving_hours, used_10th_hour, notes
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, date_str, round(shift_duration, 2), round(rest_hours, 2), 
              round(driving_hours, 2), 1 if used_10th_hour else 0, notes))
        conn.commit()
        logger.info(f"✅ Журнал обновлён для пользователя {user_id}")
    except Exception as e:
        logger.error(f"Ошибка записи в журнал: {e}")
    finally:
        if 'conn' in locals():
            conn.close()
