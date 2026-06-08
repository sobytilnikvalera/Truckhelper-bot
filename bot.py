import logging
import os
import sqlite3
import re
import random
from datetime import datetime, timedelta

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters

from ec_rules import ECRules
from parking_search import ParkingSearch
from database_setup import setup_database

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Bot token and API keys (set in Railway environment variables)
TOKEN = os.environ.get("BOT_TOKEN")
OPENROUTE_SERVICE_API_KEY = os.environ.get("OPENROUTE_SERVICE_API_KEY", "")

# Admin settings
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

if not TOKEN:
    raise ValueError("BOT_TOKEN environment variable is not set! Add it in Railway settings.")

# Database
DATABASE_NAME = "truckhelper.db"

# Initialize database tables on startup
setup_database()


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


# --- Helper: format minutes to human-readable ---

def format_minutes(total_minutes):
    """Format minutes to 'Xч Yм' format."""
    total_minutes = int(total_minutes)
    if total_minutes <= 0:
        return "0м"
    hours = total_minutes // 60
    minutes = total_minutes % 60
    if hours > 0 and minutes > 0:
        return f"{hours}ч {minutes}м"
    elif hours > 0:
        return f"{hours}ч"
    else:
        return f"{minutes}м"


def format_seconds(total_seconds):
    """Format seconds to 'Xч Yм' or 'Xм Yс' format."""
    total_seconds = int(total_seconds)
    if total_seconds <= 0:
        return "0м"
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    if hours > 0 and minutes > 0:
        return f"{hours}ч {minutes}м"
    elif hours > 0:
        return f"{hours}ч"
    elif minutes > 0:
        return f"{minutes}м"
    else:
        return f"{total_seconds}с"


# --- Funny reminder messages ---

REMINDER_30MIN = [
    "⚠️ Братан, 30 минут до перерыва! Начинай присматривать парковку 🅿️",
    "⏰ Эй, дальнобой! Через 30 мин тебе нужна пауза. Ищи место!",
    "🚛 30 минут осталось. Скоро кофе-брейк, готовься!",
]

REMINDER_15MIN = [
    "🔴 15 минут! Серьёзно, ищи где встать!",
    "⚡ Осталось 15 мин! Если не видишь парковку — паникуй (шутка, но ищи)!",
    "🅿️ 15 минут до перерыва! Давай, найди местечко!",
]

REMINDER_10MIN = [
    "🚨 10 МИНУТ! Ты что, решил рекорд поставить?! Тормози уже!",
    "😤 10 минут осталось! Хватит гнать, пора на паузу!",
    "⛔ 10 мин! Тахограф не обманешь, братан! Вставай!",
]

REMINDER_5MIN = [
    "🤬 5 МИНУТ, БЛЯТЬ! ТОРМОЗИ НЕМЕДЛЕННО! Штраф хочешь?!",
    "💀 5 минут! Ё-моё, ты что творишь?! СТОЙ СЕЙЧАС ЖЕ!",
    "🔥 ПЯТЬ МИНУТ! Ты ебанулся?! Вставай на паузу СРОЧНО!",
    "☠️ 5 МИН! Если не остановишься — тахограф тебя сдаст! ТОРМОЗИ!",
]


# --- Main Menu ---

def get_main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("🚛 Режим труда и отдыха", callback_data="rtio_menu")],
        [InlineKeyboardButton("🅿️ Поиск парковки", callback_data="find_parking")],
        [InlineKeyboardButton("🗺️ Маршрут", callback_data="route_planner")],
        [InlineKeyboardButton("💬 Помощник общения", callback_data="communication_helper")],
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
        f"Помогу с режимом труда и отдыха (EC 561/2006), поиском парковок и маршрутами.\n\n"
        f"Выбери действие:",
        reply_markup=get_main_menu_keyboard()
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📋 Команды:\n"
        "/start — Главное меню\n"
        "/help — Помощь\n"
        "/status — Быстрый статус\n\n"
        "Используй кнопки для навигации."
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    try:
        ec_rules = ECRules(user_id)
        text = _build_status_text(ec_rules)
        await update.message.reply_text(text)
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
        f"📊 Статистика:\n"
        f"• Всего: {total_users}\n"
        f"• Активных за 24ч: {active_24h}\n"
        f"• Активных за 7 дней: {active_7d}\n\n"
        f"📋 Последние пользователи:\n"
    )

    for u in recent_users:
        username = f"@{u['telegram_username']}" if u["telegram_username"] else "без username"
        name = u["first_name"] or ""
        if u["last_name"]:
            name += f" {u['last_name']}"
        reg_date = u["registration_date"][:10] if u["registration_date"] else "?"
        text += f"• {name} ({username}) — рег: {reg_date}\n"

    await update.message.reply_text(text)


# --- Notification Job Callbacks ---

async def driving_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    msg = job.data
    await context.bot.send_message(job.chat_id, text=msg)


# --- Geocoding with Nominatim (free, no API key) ---

async def geocode_city(city_name: str) -> tuple:
    """Geocode a city name using Nominatim (OpenStreetMap). Returns (lat, lon) or None."""
    geocode_url = "https://nominatim.openstreetmap.org/search"
    params = {
        "q": city_name,
        "format": "json",
        "limit": 1,
        "addressdetails": 0,
    }
    headers = {
        "User-Agent": "TruckHelperBot/1.0"
    }
    try:
        response = requests.get(geocode_url, params=params, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()
        if data:
            lat = float(data[0]["lat"])
            lon = float(data[0]["lon"])
            return (lat, lon)
        return None
    except Exception as e:
        logger.error(f"Nominatim geocoding error for '{city_name}': {e}")
        return None


# --- Route distance calculation (OSRM, free, no key) ---

async def get_route_details(start_coords: tuple, end_coords: tuple) -> dict:
    """Get route details using OSRM (free routing). Returns dict with distance_km and duration_hours."""
    # OSRM expects lon,lat
    url = f"http://router.project-osrm.org/route/v1/driving/{start_coords[1]},{start_coords[0]};{end_coords[1]},{end_coords[0]}?overview=false"
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        data = response.json()
        if data.get("code") == "Ok" and data.get("routes"):
            route = data["routes"][0]
            distance_km = route["distance"] / 1000
            duration_hours = route["duration"] / 3600
            return {
                "distance_km": distance_km,
                "duration_hours": duration_hours,
            }
        return None
    except Exception as e:
        logger.error(f"OSRM routing error: {e}")
        return None


# --- Build status text ---

def _build_status_text(ec_rules):
    """Build a clean status text from ECRules instance."""
    status = ec_rules.get_status()

    daily_min = status["daily_driving"]
    weekly_min = status["weekly_driving"]
    bi_weekly_min = status["bi_weekly_driving"]

    text = "📊 Статус режима труда и отдыха:\n\n"
    text += f"🚗 Сегодня за рулём: {format_minutes(daily_min)}\n"
    text += f"📅 За неделю: {format_minutes(weekly_min)} / 56ч\n"
    text += f"📅 За 2 недели: {format_minutes(bi_weekly_min)} / 90ч\n\n"
    text += f"⏱ Непрерывное вождение: {status['remaining_continuous_driving']}\n"
    text += f"☕ Перерыв: {status['required_break']}\n\n"
    text += f"📊 Дневной лимит: {status['daily_driving_limit_status']}\n"
    text += f"📊 Недельный лимит: {status['weekly_driving_limit_status']}\n\n"
    text += f"😴 Отдых: {status['daily_rest_status']}\n"
    return text


# --- Callback Query Handlers ---

async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    user_id = query.from_user.id

    try:
        ec_rules = ECRules(user_id)

        # === MAIN MENU ===
        if query.data == "main_menu":
            await query.edit_message_text(text=MAIN_MENU_TEXT, reply_markup=get_main_menu_keyboard())

        # === РТиО MENU ===
        elif query.data == "rtio_menu":
            # Show different menu depending on current state
            if ec_rules.current_shift_start_time:
                # Shift is active
                await _show_active_shift_menu(query, ec_rules)
            else:
                # No active shift
                keyboard = [
                    [InlineKeyboardButton("🟢 Открыть смену", callback_data="start_shift")],
                    [InlineKeyboardButton("📊 Статус", callback_data="show_status")],
                    [InlineKeyboardButton("◀️ Меню", callback_data="main_menu")],
                ]
                await query.edit_message_text(
                    text="🚛 Режим труда и отдыха\n\nСмена не активна. Нажми «Открыть смену» чтобы начать.",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )

        # === START SHIFT ===
        elif query.data == "start_shift":
            response = ec_rules.start_shift()
            if "✅" in response:
                await _show_active_shift_menu(query, ec_rules)
            else:
                keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="rtio_menu")]]
                await query.edit_message_text(text=response, reply_markup=InlineKeyboardMarkup(keyboard))

        # === END SHIFT ===
        elif query.data == "end_shift":
            if ec_rules.current_driving_start_time:
                keyboard = [
                    [InlineKeyboardButton("⏹ Сначала закончи вождение", callback_data="end_driving")],
                    [InlineKeyboardButton("◀️ Назад", callback_data="rtio_menu")],
                ]
                await query.edit_message_text(
                    text="⚠️ Нельзя закрыть смену во время вождения.\nСначала заверши вождение.",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
            else:
                response = ec_rules.end_shift()
                # Remove all scheduled jobs
                current_jobs = context.job_queue.get_jobs_by_name(str(user_id))
                for job in current_jobs:
                    job.schedule_removal()

                keyboard = [
                    [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")],
                ]
                await query.edit_message_text(text=response, reply_markup=InlineKeyboardMarkup(keyboard))

        # === START DRIVING ===
        elif query.data == "start_driving":
            response = ec_rules.start_driving()
            if "✅" in response:
                # Schedule reminders
                _schedule_driving_reminders(context, user_id, ec_rules)
                # Show driving active screen
                await _show_driving_active(query, ec_rules)
            else:
                keyboard = [
                    [InlineKeyboardButton("◀️ Назад", callback_data="rtio_menu")],
                ]
                await query.edit_message_text(text=response, reply_markup=InlineKeyboardMarkup(keyboard))

        # === END DRIVING ===
        elif query.data == "end_driving":
            response = ec_rules.end_driving()
            # Remove driving reminders
            current_jobs = context.job_queue.get_jobs_by_name(str(user_id))
            for job in current_jobs:
                job.schedule_removal()

            if "✅" in response:
                # Show shift menu after ending driving
                # Re-load state
                ec_rules_fresh = ECRules(user_id)
                shift_start = ec_rules_fresh.current_shift_start_time
                shift_duration = datetime.now() - shift_start if shift_start else timedelta(0)

                text = f"✅ Вождение завершено!\n\n"
                text += f"{response}\n\n"
                text += f"━━━━━━━━━━━━━━━\n"
                text += f"🚛 Смена активна с {shift_start.strftime('%H:%M') if shift_start else '?'}\n"
                text += f"⏱ Длительность смены: {format_seconds(int(shift_duration.total_seconds()))}\n"

                keyboard = [
                    [InlineKeyboardButton("🚗 Снова за руль", callback_data="start_driving")],
                    [InlineKeyboardButton("🔴 Закрыть смену", callback_data="end_shift")],
                    [InlineKeyboardButton("📊 Статус", callback_data="show_status")],
                    [InlineKeyboardButton("◀️ Меню", callback_data="main_menu")],
                ]
                await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="rtio_menu")]]
                await query.edit_message_text(text=response, reply_markup=InlineKeyboardMarkup(keyboard))

        # === REFRESH DRIVING STATUS ===
        elif query.data == "refresh_driving":
            if ec_rules.current_driving_start_time:
                await _show_driving_active(query, ec_rules)
            else:
                await _show_active_shift_menu(query, ec_rules)

        # === SHOW STATUS ===
        elif query.data == "show_status":
            text = _build_status_text(ec_rules)
            keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="rtio_menu")]]
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))

        # === FIND PARKING ===
        elif query.data == "find_parking":
            await query.edit_message_text(
                text="📍 Отправь мне свою геолокацию для поиска парковок.\n\n"
                     "Нажми 📎 → Геопозиция в Telegram.")
            context.user_data["awaiting_location"] = True

        # === ROUTE PLANNER ===
        elif query.data == "route_planner":
            await query.edit_message_text(
                text="🗺️ Напиши откуда и куда едешь.\n\n"
                     "Примеры:\n"
                     "• Берлин Париж\n"
                     "• Варшава - Мадрид\n"
                     "• из Мюнхена в Рим\n\n"
                     "Просто напиши два города:")
            context.user_data["awaiting_route"] = True

        # === COMMUNICATION HELPER ===
        elif query.data == "communication_helper":
            keyboard = [
                [InlineKeyboardButton("⏰ Задержка", callback_data="comm_delay")],
                [InlineKeyboardButton("🔧 Поломка", callback_data="comm_breakdown")],
                [InlineKeyboardButton("🔄 Изменение маршрута", callback_data="comm_route_change")],
                [InlineKeyboardButton("✏️ Свободный текст", callback_data="comm_free_text")],
                [InlineKeyboardButton("◀️ Меню", callback_data="main_menu")],
            ]
            await query.edit_message_text(text="💬 Выбери тип сообщения:", reply_markup=InlineKeyboardMarkup(keyboard))

        elif query.data.startswith("comm_"):
            context.user_data["communication_type"] = query.data
            prompts = {
                "comm_delay": "Напиши причину задержки и когда приедешь\n(например: пробка на А2, буду через 2 часа)",
                "comm_breakdown": "Опиши поломку и где ты сейчас\n(например: прокол колеса, 150 км трассы Е40)",
                "comm_route_change": "Опиши причину и новый маршрут\n(например: объезд из-за ремонта, еду через Дрезден)",
                "comm_free_text": "Напиши текст, я оформлю его профессионально:",
            }
            await query.edit_message_text(text=prompts.get(query.data, "Напиши текст:"))
            context.user_data["awaiting_comm_input"] = True

    except Exception as e:
        logger.error(f"Error in button handler: {e}")
        keyboard = [[InlineKeyboardButton("◀️ Меню", callback_data="main_menu")]]
        try:
            await query.edit_message_text(
                text="⚠️ Произошла ошибка. Попробуй снова.",
                reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception:
            pass


# --- Helper functions for shift/driving display ---

async def _show_active_shift_menu(query, ec_rules):
    """Show the active shift screen with current state."""
    shift_start = ec_rules.current_shift_start_time
    shift_duration = datetime.now() - shift_start if shift_start else timedelta(0)
    start_time_str = shift_start.strftime('%H:%M') if shift_start else "?"

    if ec_rules.current_driving_start_time:
        # Currently driving - show driving screen
        await _show_driving_active(query, ec_rules)
    else:
        # Shift active but not driving
        daily_driving = ec_rules.daily_driving_minutes

        text = f"🟢 Смена открыта\n"
        text += f"━━━━━━━━━━━━━━━\n"
        text += f"🕐 Начало: {start_time_str}\n"
        text += f"⏱ Длительность: {format_seconds(int(shift_duration.total_seconds()))}\n"
        text += f"🚗 Вождение сегодня: {format_minutes(daily_driving)}\n"
        text += f"━━━━━━━━━━━━━━━\n"

        keyboard = [
            [InlineKeyboardButton("🚗 Начать вождение", callback_data="start_driving")],
            [InlineKeyboardButton("🔴 Закрыть смену", callback_data="end_shift")],
            [InlineKeyboardButton("📊 Полный статус", callback_data="show_status")],
            [InlineKeyboardButton("◀️ Меню", callback_data="main_menu")],
        ]
        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))


async def _show_driving_active(query, ec_rules):
    """Show the active driving screen."""
    driving_start = ec_rules.current_driving_start_time
    driving_duration = datetime.now() - driving_start if driving_start else timedelta(0)
    driving_start_str = driving_start.strftime('%H:%M') if driving_start else "?"

    shift_start = ec_rules.current_shift_start_time
    shift_duration = datetime.now() - shift_start if shift_start else timedelta(0)

    # Calculate remaining continuous driving
    max_continuous = 4.5 * 3600  # 4h30m in seconds
    remaining_seconds = max_continuous - driving_duration.total_seconds()
    remaining_str = format_seconds(max(0, int(remaining_seconds)))

    text = f"🚗 ЗА РУЛЁМ\n"
    text += f"━━━━━━━━━━━━━━━\n"
    text += f"🕐 Начало вождения: {driving_start_str}\n"
    text += f"⏱ Едешь уже: {format_seconds(int(driving_duration.total_seconds()))}\n"
    text += f"⏳ До перерыва: {remaining_str}\n"
    text += f"━━━━━━━━━━━━━━━\n"
    text += f"🚛 Смена идёт: {format_seconds(int(shift_duration.total_seconds()))}\n"
    text += f"━━━━━━━━━━━━━━━\n"

    if remaining_seconds <= 0:
        text += f"\n🛑 ПЕРЕРЫВ ПРОСРОЧЕН! Остановись СЕЙЧАС!\n"

    keyboard = [
        [InlineKeyboardButton("⏹ Закончить вождение", callback_data="end_driving")],
        [InlineKeyboardButton("🔄 Обновить", callback_data="refresh_driving")],
        [InlineKeyboardButton("◀️ Меню", callback_data="main_menu")],
    ]
    await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))


def _schedule_driving_reminders(context, user_id, ec_rules):
    """Schedule funny reminders before mandatory break."""
    # Remove any existing reminders for this user
    current_jobs = context.job_queue.get_jobs_by_name(str(user_id))
    for job in current_jobs:
        job.schedule_removal()

    driving_start = ec_rules.get_current_driving_session_start_time()
    if not driving_start:
        return

    now = datetime.now()
    elapsed = (now - driving_start).total_seconds()
    max_driving = 4.5 * 3600  # 4h30m

    # 30 min before (at 4h mark)
    time_to_30 = max_driving - 30 * 60 - elapsed
    if time_to_30 > 0:
        context.job_queue.run_once(
            driving_reminder, time_to_30,
            chat_id=user_id, name=str(user_id),
            data=random.choice(REMINDER_30MIN)
        )

    # 15 min before (at 4h15m mark)
    time_to_15 = max_driving - 15 * 60 - elapsed
    if time_to_15 > 0:
        context.job_queue.run_once(
            driving_reminder, time_to_15,
            chat_id=user_id, name=str(user_id),
            data=random.choice(REMINDER_15MIN)
        )

    # 10 min before (at 4h20m mark)
    time_to_10 = max_driving - 10 * 60 - elapsed
    if time_to_10 > 0:
        context.job_queue.run_once(
            driving_reminder, time_to_10,
            chat_id=user_id, name=str(user_id),
            data=random.choice(REMINDER_10MIN)
        )

    # 5 min before (at 4h25m mark)
    time_to_5 = max_driving - 5 * 60 - elapsed
    if time_to_5 > 0:
        context.job_queue.run_once(
            driving_reminder, time_to_5,
            chat_id=user_id, name=str(user_id),
            data=random.choice(REMINDER_5MIN)
        )

    logger.info(f"Scheduled driving reminders for user {user_id}")


# --- Location Handler ---

async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get("awaiting_location"):
        latitude = update.message.location.latitude
        longitude = update.message.location.longitude
        await update.message.reply_text("🔍 Ищу парковки для грузовиков рядом...")

        try:
            parking_finder = ParkingSearch()
            parkings = parking_finder.search_parking(latitude, longitude, radius=50000)

            if parkings:
                response_text = "🅿️ Парковки для грузовиков:\n\n"
                for i, parking in enumerate(parkings[:7], 1):
                    name = parking.get("name", "Без названия")
                    lat = parking.get("latitude")
                    lon = parking.get("longitude")
                    distance = parking.get("distance", 0)

                    amenities_str = []
                    if parking["amenities"]["shower"]:
                        amenities_str.append("🚿")
                    if parking["amenities"]["wc"]:
                        amenities_str.append("🚻")
                    if parking["amenities"]["fuel"]:
                        amenities_str.append("⛽")
                    if parking["amenities"]["restaurant"]:
                        amenities_str.append("🍽")
                    amenities_display = f" {' '.join(amenities_str)}" if amenities_str else ""

                    response_text += f"{i}. {name} — {distance:.1f} км{amenities_display}\n"
                    response_text += f"   📍 https://www.google.com/maps?q={lat},{lon}\n\n"

                response_text += "🔗 Ещё: transparking.eu/map"
                await update.message.reply_text(response_text)
            else:
                await update.message.reply_text(
                    "❌ Парковки не найдены в радиусе 50 км.\n\n"
                    "Попробуй:\n"
                    "• Отправить другую геолокацию\n"
                    "• Посмотреть на transparking.eu/map")
        except Exception as e:
            logger.error(f"Parking search error: {e}")
            await update.message.reply_text("⚠️ Ошибка при поиске. Попробуй позже.")

        context.user_data["awaiting_location"] = False
        await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=get_main_menu_keyboard())
    else:
        await update.message.reply_text(
            "Используй кнопку '🅿️ Поиск парковки' в меню.",
            reply_markup=get_main_menu_keyboard())


# --- Text Input Handler ---

async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get("awaiting_route"):
        route_query = update.message.text.strip()

        # Parse cities - support multiple formats:
        # "Берлин Париж", "Берлин - Париж", "из Берлина в Париж", "Берлин-Париж"
        cities = _parse_route_cities(route_query)

        if cities:
            origin_city, destination_city = cities
            await update.message.reply_text(f"🗺️ Строю маршрут: {origin_city} → {destination_city}...")

            try:
                origin_coords = await geocode_city(origin_city)
                destination_coords = await geocode_city(destination_city)

                if not origin_coords:
                    await update.message.reply_text(f"❌ Не могу найти город: {origin_city}")
                    context.user_data["awaiting_route"] = False
                    await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=get_main_menu_keyboard())
                    return
                if not destination_coords:
                    await update.message.reply_text(f"❌ Не могу найти город: {destination_city}")
                    context.user_data["awaiting_route"] = False
                    await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=get_main_menu_keyboard())
                    return

                route_details = await get_route_details(origin_coords, destination_coords)

                if route_details:
                    distance_km = route_details["distance_km"]
                    duration_hours = route_details["duration_hours"]

                    text = f"🗺️ Маршрут: {origin_city} → {destination_city}\n"
                    text += f"━━━━━━━━━━━━━━━\n"
                    text += f"📏 Расстояние: {distance_km:.0f} км\n"
                    text += f"⏱ Время в пути: {format_minutes(int(duration_hours * 60))}\n\n"

                    # EC 561/2006 stops
                    text += "☕ Остановки по EC 561/2006:\n"
                    current_hours = 0
                    stop_num = 1
                    total_time_with_breaks = 0

                    while current_hours < duration_hours:
                        current_hours += 4.5
                        if current_hours < duration_hours:
                            km_at_stop = int(distance_km * (current_hours / duration_hours))
                            text += f"  {stop_num}. ~{km_at_stop} км — перерыв 45 мин\n"
                            stop_num += 1
                            total_time_with_breaks += 45  # minutes
                        else:
                            text += f"  🏁 Прибытие\n"

                    if stop_num > 1:
                        total_with_breaks = duration_hours * 60 + total_time_with_breaks
                        text += f"\n⏱ С перерывами: ~{format_minutes(int(total_with_breaks))}\n"

                    # Daily limit check
                    if duration_hours > 9:
                        days_needed = int(duration_hours / 9) + 1
                        text += f"\n📅 Потребуется дней: ~{days_needed} (по 9ч вождения/день)\n"

                    await update.message.reply_text(text)
                else:
                    await update.message.reply_text("❌ Не удалось построить маршрут. Попробуй другие города.")

            except Exception as e:
                logger.error(f"Route planning error: {e}")
                await update.message.reply_text("⚠️ Ошибка. Попробуй позже.")
        else:
            await update.message.reply_text(
                "❌ Не понял маршрут. Напиши два города, например:\n"
                "• Берлин Париж\n"
                "• Варшава - Мадрид\n"
                "• из Мюнхена в Рим")

        context.user_data["awaiting_route"] = False
        await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=get_main_menu_keyboard())

    elif context.user_data.get("awaiting_comm_input"):
        user_input = update.message.text
        comm_type = context.user_data.get("communication_type")

        templates = {
            "comm_delay": f"Уважаемый получатель,\n\nСообщаю о задержке доставки.\n{user_input}\n\nС уважением,\nВодитель",
            "comm_breakdown": f"Уважаемый получатель,\n\nСообщаю о поломке ТС.\n{user_input}\nОжидаю инструкций.\n\nС уважением,\nВодитель",
            "comm_route_change": f"Уважаемый получатель,\n\nСообщаю об изменении маршрута.\n{user_input}\n\nС уважением,\nВодитель",
            "comm_free_text": f"Уважаемый получатель,\n\n{user_input}\n\nС уважением,\nВодитель",
        }

        generated = templates.get(comm_type, user_input)
        await update.message.reply_text(f"📋 Готовое сообщение (скопируй):\n\n{generated}")

        context.user_data["awaiting_comm_input"] = False
        context.user_data["communication_type"] = None
        await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=get_main_menu_keyboard())
    else:
        await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=get_main_menu_keyboard())


def _parse_route_cities(text):
    """Parse two cities from various input formats."""
    text = text.strip()

    # Try "из X в Y" format
    match = re.match(r"(?:из\s+)?(.+?)\s+(?:в|->|→)\s+(.+)", text, re.IGNORECASE)
    if match:
        return match.group(1).strip(), match.group(2).strip()

    # Try "X - Y" or "X — Y" format
    match = re.match(r"(.+?)\s*[-—–]\s*(.+)", text)
    if match:
        city1 = match.group(1).strip()
        city2 = match.group(2).strip()
        if city1 and city2:
            return city1, city2

    # Try just two words/phrases separated by space (at least 2 chars each)
    # Split by multiple spaces or common separators
    parts = re.split(r'\s{2,}', text)
    if len(parts) == 2 and len(parts[0]) >= 2 and len(parts[1]) >= 2:
        return parts[0].strip(), parts[1].strip()

    # Last resort: split by space if exactly 2 words
    words = text.split()
    if len(words) == 2 and len(words[0]) >= 2 and len(words[1]) >= 2:
        return words[0], words[1]

    # If more than 2 words, try to split in half
    if len(words) >= 3:
        # Check if "из" is first word
        if words[0].lower() == "из":
            remaining = " ".join(words[1:])
            # Try to find "в" separator
            v_match = re.match(r"(.+?)\s+в\s+(.+)", remaining, re.IGNORECASE)
            if v_match:
                return v_match.group(1).strip(), v_match.group(2).strip()

    return None


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

    # Text messages
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_input))

    # Buttons
    application.add_handler(CallbackQueryHandler(button))

    # Error handler
    async def error_handler(update, context):
        logger.error(f"Exception: {context.error}")
        if update and update.callback_query:
            try:
                keyboard = [[InlineKeyboardButton("◀️ Меню", callback_data="main_menu")]]
                await update.callback_query.edit_message_text(
                    text="⚠️ Ошибка. Попробуй /start",
                    reply_markup=InlineKeyboardMarkup(keyboard))
            except Exception:
                pass
        elif update and update.message:
            try:
                await update.message.reply_text("⚠️ Ошибка. Попробуй /start")
            except Exception:
                pass

    application.add_error_handler(error_handler)

    # Run
    logger.info("Bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
