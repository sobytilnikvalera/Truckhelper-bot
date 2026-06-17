import sqlite3
from datetime import datetime, timedelta
import calendar

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command

# Database connection helper
def get_db_connection():
    conn = sqlite3.connect('driver_shifts.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_journal_db():
    conn = get_db_connection()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS driver_shifts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            date TEXT UNIQUE,
            shift_duration REAL,
            rest_hours REAL,
            driving_hours REAL,
            used_10th_hour BOOLEAN DEFAULT 0,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

router = Router()

@router.message(F.text == "Журнал (2 недели)")
async def show_journal(message: Message):
    user_id = message.from_user.id
    conn = get_db_connection()
    
    # Get last 14 days
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=13)
    
    cursor = conn.execute('''
        SELECT date, shift_duration, rest_hours, driving_hours, used_10th_hour
        FROM driver_shifts 
        WHERE user_id = ? AND date >= ?
        ORDER BY date DESC
    ''', (user_id, start_date.isoformat()))
    
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        await message.answer("📋 Журнал пока пуст. Записи появятся после закрытия смен.")
        return
    
    # Build table
    text = "**Журнал смен за 2 недели**\n\n"
    text += "Дата | День | Смена | Отдых | Вождение | 10-й час\n"
    text += "---|---|---|---|---|---\n"
    
    total_driving_week = 0
    total_driving_2w = 0
    
    for row in rows:
        date_obj = datetime.fromisoformat(row['date'])
        day_name = calendar.day_name[date_obj.weekday()][:3]
        used_10 = "⚠️" if row['used_10th_hour'] else ""
        
        text += f"{row['date']} | {day_name} | {row['shift_duration']:.1f}ч | {row['rest_hours']:.1f}ч | {row['driving_hours']:.1f}ч | {used_10}\n"
        
        total_driving_2w += row['driving_hours']
        if date_obj >= end_date - timedelta(days=6):
            total_driving_week += row['driving_hours']
    
    text += "\n**Итоги:**\n"
    text += f"За последнюю неделю: **{total_driving_week:.1f}** часов вождения\n"
    text += f"За 2 недели: **{total_driving_2w:.1f}** часов вождения"
    
    await message.answer(text, parse_mode="Markdown")

# Function to add shift record (call from close shift handler)
def add_shift_record(user_id, shift_duration, rest_hours, driving_hours, used_10th_hour=False, notes=""):
    conn = get_db_connection()
    date = datetime.now().date().isoformat()
    conn.execute('''
        INSERT OR REPLACE INTO driver_shifts 
        (user_id, date, shift_duration, rest_hours, driving_hours, used_10th_hour, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, date, shift_duration, rest_hours, driving_hours, used_10th_hour, notes))
    conn.commit()
    conn.close()
