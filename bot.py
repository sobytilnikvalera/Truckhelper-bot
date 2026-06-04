import logging
import os
import sqlite3
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

from ec_rules import ECRules
from parking_search import ParkingSearch

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Bot token and API keys
TOKEN = os.environ.get("BOT_TOKEN", "8840935074:AAGtkB-HhnhnfXEvgTRy0Qjwva4J_GO50Po")
GOOGLE_MAPS_API_KEY = os.environ.get("GOOGLE_MAPS_API_KEY", "")

# Admin settings
ADMIN_ID = int(os.environ.get("ADMIN_ID", "683764730"))

# Database
DATABASE_NAME = "truckhelper.db"


def get_db_connection():
    conn = sqlite3.connect(DATABASE_NAME)
    conn.row_factory = sqlite3.Row
    return conn


async def get_or_create_user(user):
    """Register or update user in database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user.id,))
    existing = cursor.fetchone()
    now = datetime.now().isoformat()

    if not existing:
        cursor.execute(
            "INSERT INTO users (user_id, telegram_username, first_name, last_name, language_code, registration_date, last_active) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user.id, user.username, user.first_name, user.last_name, user.language_code, now, now))
    else:
        cursor.execute(
            "UPDATE users SET telegram_username = ?, first_name = ?, last_name = ?, last_active = ? WHERE user_id = ?",
            (user.username, user.first_name, user.last_name, now, user.id))

    conn.commit()
    conn.close()


# --- Main Menu ---

def get_main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("🚛 Начать смену", callback_data="start_shift")],
        [InlineKeyboardButton("🅿️ Поиск парковки", callback_data="find_parking")],
        [InlineKeyboardButton("📊 Статус РТиО", callback_data="rtio_status")],
        [InlineKeyboardButton("📈 Статистика", callback_data="statistics")],
    ]
    return InlineKeyboardMarkup(keyboard)


MAIN_MENU_TEXT = "🚛 TruckHelper — Главное меню\n\nВыберите действие:"


# --- Command Handlers ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await get_or_create_user(user)
    await update.message.reply_text(
        f"Привет, {user.first_name}! 👋\n\n"
        f"Я — TruckHelper, твой помощник на дороге.\n"
        f"Помогу с режимом труда и отдыха (EC 561/2006) и поиском парковок.\n\n"
        f"Выбери действие:",
        reply_markup=get_main_menu_keyboard()
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📋 Команды:\n"
        "/start — Главное меню\n"
        "/help — Помощь\n"
        "/status — Быстрый статус РТиО\n\n"
        "Используй кнопки для навигации."
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    try:
        ec_rules = ECRules(user_id)
        status = ec_rules.get_status()
        await update.message.reply_text(_format_status_text(status))
    except Exception as e:
        logger.error(f"Error in status_command: {e}")
        await update.message.reply_text("⚠️ Ошибка. Попробуйте /start")


async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin panel - only for ADMIN_ID."""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("⛔ Доступ запрещён.")
        return

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) as total FROM users")
    total_users = cursor.fetchone()["total"]

    cursor.execute("SELECT COUNT(*) as active FROM users WHERE last_active >= ?",
                   ((datetime.now() - timedelta(days=7)).isoformat(),))
    active_7d = cursor.fetchone()["active"]

    cursor.execute("SELECT COUNT(*) as active FROM users WHERE last_active >= ?",
                   ((datetime.now() - timedelta(days=1)).isoformat(),))
    active_24h = cursor.fetchone()["active"]

    cursor.execute("SELECT user_id, telegram_username, first_name, last_name, registration_date, last_active FROM users ORDER BY registration_date DESC LIMIT 10")
    recent_users = cursor.fetchall()

    conn.close()

    text = (
        f"👑 Админ-панель TruckHelper\n\n"
        f"📊 Статистика пользователей:\n"
        f"• Всего: {total_users}\n"
        f"• Активных за 24ч: {active_24h}\n"
        f"• Активных за 7 дней: {active_7d}\n\n"
        f"📋 Последние пользователи:\n"
    )

    for u in recent_users:
        username = f"@{u['telegram_username']}" if u['telegram_username'] else "без username"
        name = u['first_name'] or ""
        if u['last_name']:
            name += f" {u['last_name']}"
        reg_date = u['registration_date'][:10] if u['registration_date'] else "?"
        text += f"• {name} ({username}) — ID: {u['user_id']} — рег: {reg_date}\n"

    await update.message.reply_text(text)


# --- Callback Query Handlers ---

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass  # Ignore if callback query is too old

    user_id = query.from_user.id

    try:
        ec_rules = ECRules(user_id)

        if query.data == "start_shift":
            response = ec_rules.start_shift()
            keyboard = [
                [InlineKeyboardButton("🚗 Начать вождение", callback_data="start_driving")],
                [InlineKeyboardButton("🛑 Закончить смену", callback_data="end_shift")],
                [InlineKeyboardButton("◀️ Меню", callback_data="main_menu")],
            ]
            await query.edit_message_text(text=response, reply_markup=InlineKeyboardMarkup(keyboard))

        elif query.data == "end_shift":
            response = ec_rules.end_shift()
            keyboard = [[InlineKeyboardButton("◀️ Меню", callback_data="main_menu")]]
            await query.edit_message_text(text=response, reply_markup=InlineKeyboardMarkup(keyboard))

        elif query.data == "start_driving":
            response = ec_rules.start_driving()
            keyboard = [
                [InlineKeyboardButton("⏹ Закончить вождение", callback_data="end_driving")],
                [InlineKeyboardButton("◀️ Меню", callback_data="main_menu")],
            ]
            await query.edit_message_text(text=response, reply_markup=InlineKeyboardMarkup(keyboard))

        elif query.data == "end_driving":
            response = ec_rules.end_driving()
            keyboard = [
                [InlineKeyboardButton("🚗 Начать вождение", callback_data="start_driving")],
                [InlineKeyboardButton("🛑 Закончить смену", callback_data="end_shift")],
                [InlineKeyboardButton("◀️ Меню", callback_data="main_menu")],
            ]
            await query.edit_message_text(text=response, reply_markup=InlineKeyboardMarkup(keyboard))

        elif query.data == "rtio_status":
            status = ec_rules.get_status()
            text = _format_status_text(status)
            keyboard = [[InlineKeyboardButton("◀️ Меню", callback_data="main_menu")]]
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))

        elif query.data == "find_parking":
            await query.edit_message_text(
                text="📍 Отправьте мне свою геолокацию для поиска парковок.\n\n"
                     "Нажмите 📎 → Геопозиция (или кнопку геолокации на клавиатуре).")
            context.user_data["awaiting_location"] = True

        elif query.data == "statistics":
            status = ec_rules.get_status()
            daily_h = status["daily_driving"] / 60
            weekly_h = status["weekly_driving"] / 60
            bi_weekly_h = status["bi_weekly_driving"] / 60

            text = (
                f"📈 Статистика вождения:\n\n"
                f"Сегодня: {daily_h:.1f} ч\n"
                f"Эта неделя: {weekly_h:.1f} ч / 56ч макс\n"
                f"Две недели: {bi_weekly_h:.1f} ч / 90ч макс\n\n"
                f"📋 Недельные лимиты:\n"
                f"• {status['extended_driving_info']}\n"
                f"• {status['weekly_rest_info']}\n"
            )
            keyboard = [[InlineKeyboardButton("◀️ Меню", callback_data="main_menu")]]
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))

        elif query.data == "main_menu":
            await query.edit_message_text(text=MAIN_MENU_TEXT, reply_markup=get_main_menu_keyboard())

    except Exception as e:
        logger.error(f"Error in button handler: {e}")
        keyboard = [[InlineKeyboardButton("◀️ Меню", callback_data="main_menu")]]
        await query.edit_message_text(
            text="⚠️ Произошла ошибка. Попробуйте снова.",
            reply_markup=InlineKeyboardMarkup(keyboard))


def _format_status_text(status):
    daily_h = status["daily_driving"] / 60
    weekly_h = status["weekly_driving"] / 60
    bi_weekly_h = status["bi_weekly_driving"] / 60

    return (
        f"📊 Статус режима труда и отдыха:\n\n"
        f"🚗 Вождение сегодня: {daily_h:.1f} ч\n"
        f"📅 Вождение за неделю: {weekly_h:.1f} ч\n"
        f"📅 Вождение за 2 недели: {bi_weekly_h:.1f} ч\n\n"
        f"⏱ Непрерывное вождение: {status['remaining_continuous_driving']}\n"
        f"☕ Перерыв: {status['required_break']}\n\n"
        f"📊 Дневной лимит: {status['daily_driving_limit_status']}\n"
        f"📊 Недельный лимит: {status['weekly_driving_limit_status']}\n"
        f"📊 2-недельный лимит: {status['bi_weekly_driving_limit_status']}\n\n"
        f"😴 Отдых: {status['daily_rest_status']}\n\n"
        f"📋 Лимиты на неделю:\n"
        f"• {status['extended_driving_info']}\n"
        f"• {status['weekly_rest_info']}\n"
    )


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get("awaiting_location"):
        latitude = update.message.location.latitude
        longitude = update.message.location.longitude
        await update.message.reply_text("🔍 Ищу парковки для грузовиков рядом...")

        try:
            parking_finder = ParkingSearch(api_key=GOOGLE_MAPS_API_KEY)
            parkings = parking_finder.search_parking(latitude, longitude, radius=15000)

            if parkings:
                response_text = "🅿️ Парковки для грузовиков:\n\n"
                for i, parking in enumerate(parkings[:5], 1):
                    name = parking.get("name", "Без названия")
                    lat = parking.get("latitude")
                    lon = parking.get("longitude")
                    response_text += f"{i}. {name}\n"
                    response_text += f"   📍 https://www.google.com/maps/search/?api=1&query={lat},{lon}\n\n"
                await update.message.reply_text(response_text)
            else:
                await update.message.reply_text(
                    "❌ Парковки для грузовиков не найдены в радиусе 15 км.\n"
                    "Попробуйте отправить другую геолокацию.")
        except Exception as e:
            logger.error(f"Parking search error: {e}")
            await update.message.reply_text("⚠️ Ошибка при поиске парковок. Попробуйте позже.")

        context.user_data["awaiting_location"] = False
        await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=get_main_menu_keyboard())
    else:
        await update.message.reply_text(
            "Используйте кнопку '🅿️ Поиск парковки' в меню.",
            reply_markup=get_main_menu_keyboard())


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle any text message - show menu."""
    await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=get_main_menu_keyboard())


def main() -> None:
    """Start the bot."""
    application = Application.builder().token(TOKEN).build()

    # Commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("admin", admin_command))

    # Location
    application.add_handler(MessageHandler(filters.LOCATION, handle_location))

    # Buttons
    application.add_handler(CallbackQueryHandler(button))

    # Any other text
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Error handler
    async def error_handler(update, context):
        logger.error(f"Exception: {context.error}")
        if update and update.callback_query:
            try:
                keyboard = [[InlineKeyboardButton("◀️ Меню", callback_data="main_menu")]]
                await update.callback_query.edit_message_text(
                    text="⚠️ Произошла ошибка. Попробуйте снова.",
                    reply_markup=InlineKeyboardMarkup(keyboard))
            except Exception:
                pass
        elif update and update.message:
            try:
                await update.message.reply_text("⚠️ Ошибка. Попробуйте /start")
            except Exception:
                pass

    application.add_error_handler(error_handler)

    # Run
    logger.info("Bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    from database_setup import setup_database
    setup_database()
    main()
