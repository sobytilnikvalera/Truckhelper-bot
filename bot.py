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

if __name__ == '__main__':
    asyncio.run(main())