from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram import F
import asyncio
# ... existing imports ...

from database.journal import router as journal_router, init_journal_db

# In your main setup
async def main():
    bot = Bot(token=TOKEN)
    dp = Dispatcher()
    
    # Register journal router
    dp.include_router(journal_router)
    
    # Call init
    init_journal_db()
    
    # existing code...
    await dp.start_polling(bot)
def add_shift_to_journal(user_id: int, shift_duration: float = 0, rest_hours: float = 0, driving_hours: float = 0, used_10th_hour: bool = False, notes: str = ""):
    """Запись смены в журнал."""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        date_str = datetime.now(TZ).date().isoformat()
        
        cursor.execute('''
            INSERT OR REPLACE INTO driver_shifts 
            (user_id, date, shift_duration, rest_hours, driving_hours, used_10th_hour, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (user_id, date_str, round(shift_duration, 2), round(rest_hours, 2), 
              round(driving_hours, 2), 1 if used_10th_hour else 0, notes))
        conn.commit()
        logger.info(f"✅ Журнал обновлён для {user_id}: {driving_hours:.1f}ч вождения")
    except Exception as e:
        logger.error(f"Ошибка записи в журнал: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

if __name__ == '__main__':
    asyncio.run(main())
