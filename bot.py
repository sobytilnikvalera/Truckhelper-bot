import logging
import os
import sqlite3
import re
import random
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import requests
from flask import Flask

# --- Self-ping web server to prevent Render from sleeping ---
web_app = Flask(__name__)

@web_app.route('/')
def home():
    return 'TruckHelper Bot is running!'

@web_app.route('/health')
def health():
    return 'OK'

def run_web():
    port = int(os.environ.get('PORT', 10000))
    web_app.run(host='0.0.0.0', port=port)

def keep_alive():
    """Start web server in a separate thread."""
    t = threading.Thread(target=run_web, daemon=True)
    t.start()
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
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")

# Admin settings
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

if not TOKEN:
    raise ValueError("BOT_TOKEN environment variable is not set! Add it in Railway settings.")

# Timezone (Central European Time)
TZ = ZoneInfo(os.environ.get("BOT_TIMEZONE", "Europe/Berlin"))

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
    now = datetime.now(TZ).isoformat()

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
    "🔴 15 минут, бля! Серьёзно, ищи где встать!",
    "⚡ 15 мин осталось! Хули ты едешь?! Ищи парковку!",
    "🅿️ 15 минут! Не тупи, ищи место пока не поздно!",
]

REMINDER_10MIN = [
    "🚨 10 МИНУТ, ЁБАНЫЙ НАСОС! Ты рекорд решил поставить?! ТОРМОЗИ!",
    "😤 10 минут! Ты охуел что ли?! Хватит гнать, вставай!",
    "⛔ 10 мин! Тахограф тебя выебет, братан! ВСТАВАЙ!",
]

REMINDER_5MIN = [
    "🤬 5 МИНУТ, БЛЯТЬ! ТОРМОЗИ НАХУЙ! Штраф хочешь?!",
    "💀 Ё-МОЁ, 5 МИНУТ! Ты ебанулся?! СТОЙ СЕЙЧАС ЖЕ!",
    "🔥 ПЯТЬ МИНУТ! Пиздец тебе если не встанешь! ТОРМОЗИ!",
    "☠️ 5 МИН! Тахограф тебя сдаст нахуй! ВСТАВАЙ НЕМЕДЛЕННО!",
]

REMINDER_4MIN = [
    "🚨 4 МИНУТЫ! Ты ещё едешь?! Совсем ёбнулся?!",
    "⚠️ 4 МИН! Братан, ты в край охуел! ТОРМОЗИ!",
]

REMINDER_3MIN = [
    "💀 3 МИНУТЫ! ПИЗДЕЦ ПОДКРАЛСЯ! ВСТАВАЙ БЛЯТЬ!",
    "🔥 ТРИ МИНУТЫ! Ты что, суицидник?! ТОРМОЗИ НАХУЙ!",
]

REMINDER_2MIN = [
    "☠️ 2 МИНУТЫ!!! ТЕБЕ ПИЗДА! ТОРМОЗИ СУКА ТОРМОЗИ!",
    "🤯 ДВЕ МИНУТЫ! ТЫ МЁРТВ ЕСЛИ НЕ ВСТАНЕШЬ! СТОООЙ!",
]

REMINDER_1MIN = [
    "🆘 ОДНА МИНУТА!!! БЛЯТЬ СТОЙ!!! ШТРАФ 3000€!!! ТОРМОЗИИИИ!!!",
    "💀💀💀 МИНУТА! ПИЗДЕЦ ПРИЕХАЛ! ВСТАВАЙ ИЛИ ПРОЩАЙСЯ С ПРАВАМИ!!!",
]


# --- Main Menu ---

def get_main_menu_keyboard():
    keyboard = [
        [InlineKeyboardButton("🚛 Режим труда и отдыха", callback_data="rtio_menu")],
        [InlineKeyboardButton("🅿️ Поиск парковки", callback_data="find_parking")],
        [InlineKeyboardButton("🗺️ Маршрут", callback_data="route_planner")],
        [InlineKeyboardButton("🚫 Запреты движения", callback_data="driving_bans")],
        [InlineKeyboardButton("💰 Расходы", callback_data="expenses_menu")],
        [InlineKeyboardButton("📋 Штрафы", callback_data="fines_menu"), InlineKeyboardButton("💱 Валюты", callback_data="currency_menu")],
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
                   ((datetime.now(TZ) - timedelta(days=7)).isoformat(),))
    active_7d = cursor.fetchone()["active"]

    cursor.execute("SELECT COUNT(*) as active FROM users WHERE last_active >= ?",
                   ((datetime.now(TZ) - timedelta(days=1)).isoformat(),))
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
    text += f"🛞 Сегодня за рулём: {format_minutes(daily_min)}\n"
    text += f"📅 За неделю: {format_minutes(weekly_min)} / 56ч\n"
    text += f"📅 За 2 недели: {format_minutes(bi_weekly_min)} / 90ч\n\n"
    text += f"⏱ Непрерывное вождение: {status['remaining_continuous_driving']}\n"
    text += f"☕ Перерыв: {status['required_break']}\n\n"
    text += f"📊 Дневной лимит: {status['daily_driving_limit_status']}\n"
    text += f"📊 Недельный лимит: {status['weekly_driving_limit_status']}\n\n"
    text += f"😴 Отдых: {status['daily_rest_status']}\n"
    return text


# --- Journal (2-week history) ---

DAY_NAMES_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def _build_journal_text(user_id):
    """Build a 2-week journal showing daily shift/driving/rest data."""
    from datetime import date
    conn = get_db_connection()
    cursor = conn.cursor()

    today = date.today()
    # Start from Monday of previous week (14 days of history)
    # Find Monday of current week
    current_monday = today - timedelta(days=today.weekday())
    prev_monday = current_monday - timedelta(days=7)

    text = "📓 <b>Журнал за 2 недели</b>\n\n"

    week1_driving_total = 0
    week2_driving_total = 0

    for week_num, week_start in enumerate([prev_monday, current_monday], 1):
        week_end = week_start + timedelta(days=6)
        text += f"<b>Неделя {week_num}: {week_start.strftime('%d.%m')} — {week_end.strftime('%d.%m')}</b>\n"
        text += "─────────────────────\n"

        week_driving = 0
        has_extended = False

        for day_offset in range(7):
            day = week_start + timedelta(days=day_offset)
            day_str = day.isoformat()
            day_name = DAY_NAMES_RU[day.weekday()]
            day_display = day.strftime('%d.%m')

            # Get driving minutes for this day
            cursor.execute(
                "SELECT daily_driving_minutes FROM daily_stats WHERE user_id = ? AND date = ?",
                (user_id, day_str))
            daily_stat = cursor.fetchone()
            driving_min = daily_stat["daily_driving_minutes"] if daily_stat else 0

            # Get shift sessions for this day
            cursor.execute(
                "SELECT start_time, end_time, duration FROM driving_sessions "
                "WHERE user_id = ? AND session_type = 'shift' AND date(start_time) = ?",
                (user_id, day_str))
            shifts = cursor.fetchall()
            shift_min = 0
            for s in shifts:
                if s["duration"]:
                    shift_min += s["duration"]
                elif s["start_time"] and not s["end_time"]:
                    # Active shift - calculate from start to now
                    from datetime import datetime as dt
                    start = dt.fromisoformat(s["start_time"])
                    shift_min += int((datetime.now(TZ) - start.replace(tzinfo=TZ)).total_seconds() / 60)

            # Get rest sessions for this day
            cursor.execute(
                "SELECT duration_minutes, rest_type FROM rest_sessions "
                "WHERE user_id = ? AND date(start_time) = ?",
                (user_id, day_str))
            rests = cursor.fetchall()
            rest_min = sum(r["duration_minutes"] for r in rests if r["duration_minutes"])

            # Check if 10th hour was used
            extended_mark = ""
            if driving_min > 9 * 60:
                extended_mark = " ⚡10ч"
                has_extended = True

            week_driving += driving_min

            # Only show days that have data or are today/past
            if day > today:
                continue

            if driving_min == 0 and shift_min == 0 and rest_min == 0:
                text += f"{day_name} {day_display}: —\n"
            else:
                line = f"{day_name} {day_display}:"
                if shift_min > 0:
                    line += f" 📋{format_minutes(shift_min)}"
                if driving_min > 0:
                    line += f" 🛞{format_minutes(driving_min)}"
                if rest_min > 0:
                    line += f" 🛏{format_minutes(rest_min)}"
                line += extended_mark
                text += line + "\n"

        # Weekly summary
        if week_num == 1:
            week1_driving_total = week_driving
        else:
            week2_driving_total = week_driving

        text += "─────────────────────\n"
        text += f"📊 Итого вождение: <b>{format_minutes(week_driving)}</b> / 56ч"
        if has_extended:
            text += " (⚡ были 10ч дни)"
        text += "\n\n"

    # Bi-weekly total
    total_driving = week1_driving_total + week2_driving_total
    text += "━━━━━━━━━━━━━━━\n"
    text += f"📊 <b>Итого за 2 недели: {format_minutes(total_driving)} / 90ч</b>\n"

    remaining_biweekly = max(0, 90 * 60 - total_driving)
    if remaining_biweekly > 0:
        text += f"⏳ Осталось: {format_minutes(remaining_biweekly)}\n"
    else:
        text += "🛑 Лимит 90ч исчерпан!\n"

    conn.close()
    return text


# --- Driving Bans Data ---

DRIVING_BANS = {
    "de": {
        "country": "🇩🇪 Германия",
        "applies_to": "Грузовики >7.5т",
        "weekend": "Воскресенье и праздники: 00:00 — 22:00",
        "summer": "Лето (1.07—31.08): Сб 07:00—20:00 на автобанах (отпускной трафик)",
        "exceptions": "Скоропорт, молоко, животные, комбинированные перевозки",
        "fine": "Штраф: 75—600€",
    },
    "pl": {
        "country": "🇵🇱 Польша",
        "applies_to": "Грузовики >12т",
        "weekend": "Воскресенье и праздники: 08:00 — 22:00",
        "summer": "Лето (28.06—31.08): Пт 18:00—22:00, Сб 08:00—14:00",
        "exceptions": "Скоропорт, животные, почта, топливо",
        "fine": "Штраф: 500—2000 PLN (~115—460€)",
    },
    "fr": {
        "country": "🇫🇷 Франция",
        "applies_to": "Грузовики >7.5т",
        "weekend": "Воскресенье и праздники: 22:00 (Сб) — 22:00 (Вс)",
        "summer": "Лето: Сб 07:00—19:00 на основных трассах",
        "exceptions": "Скоропорт, транзит с разрешением",
        "fine": "Штраф: 135—1500€",
    },
    "at": {
        "country": "🇦🇹 Австрия",
        "applies_to": "Грузовики >7.5т",
        "weekend": "Воскресенье и праздники: Сб 15:00 — Вс 22:00",
        "summer": "Ночной запрет: 22:00—05:00 на некоторых трассах Тироля",
        "exceptions": "Скоропорт, комбинированные перевозки",
        "fine": "Штраф: 150—5000€",
    },
    "cz": {
        "country": "🇨🇿 Чехия",
        "applies_to": "Грузовики >7.5т или >12м",
        "weekend": "Воскресенье и праздники: 13:00 — 22:00",
        "summer": "Лето (1.07—31.08): Пт 17:00—21:00, Сб 07:00—13:00",
        "exceptions": "Скоропорт, животные, международный транзит (ограниченно)",
        "fine": "Штраф: до 100 000 CZK (~4000€)",
    },
    "it": {
        "country": "🇮🇹 Италия",
        "applies_to": "Грузовики >7.5т",
        "weekend": "Воскресенье и праздники: 08:00 — 22:00",
        "summer": "Лето: Сб 08:00—16:00 (июль—август)",
        "exceptions": "Скоропорт, международный транзит (с разрешением)",
        "fine": "Штраф: 400—1600€",
    },
    "es": {
        "country": "🇪🇸 Испания",
        "applies_to": "Грузовики >7.5т",
        "weekend": "Воскресенье и праздники: запреты на отдельных дорогах",
        "summer": "Лето: дополнительные ограничения на побережье",
        "exceptions": "Скоропорт, опасные грузы (с разрешением)",
        "fine": "Штраф: 200—6000€",
    },
    "be": {
        "country": "🇧🇪 Бельгия",
        "applies_to": "Грузовики >7.5т",
        "weekend": "Воскресенье и праздники: 22:00 (Сб) — 22:00 (Вс)",
        "summer": "Лето: дополнительных ограничений нет",
        "exceptions": "Скоропорт, международный транзит",
        "fine": "Штраф: 150—1500€",
    },
    "hu": {
        "country": "🇭🇺 Венгрия",
        "applies_to": "Грузовики >7.5т",
        "weekend": "Воскресенье и праздники: 22:00 (Сб) — 22:00 (Вс)",
        "summer": "Лето (15.06—15.09): Сб 08:00—22:00",
        "exceptions": "Скоропорт, животные",
        "fine": "Штраф: 50 000—300 000 HUF (~130—780€)",
    },
    "nl": {
        "country": "🇳🇱 Нидерланды",
        "applies_to": "Грузовики >7.5т",
        "weekend": "Нет общенационального запрета",
        "summer": "Местные ограничения в городах (milieuzones)",
        "exceptions": "Зоны низких выбросов в Амстердаме, Роттердаме, Утрехте",
        "fine": "Штраф: 250—500€",
    },
}


def _search_driving_bans_tavily(country_name):
    """Search current truck bans for today and the next three calendar days."""
    if not TAVILY_API_KEY:
        return None
    try:
        checked_at = datetime.now(TZ)
        period_start = checked_at.date()
        period_end = period_start + timedelta(days=3)
        start_iso = period_start.isoformat()
        end_iso = period_end.isoformat()
        query = (
            f"truck driving bans and traffic restrictions in {country_name} "
            f"from {start_iso} through {end_iso}; "
            "official calendar, weekends, public holidays, current updates; "
            "list exact dates and time windows, or say when no date-specific rule is found"
        )
        response = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "search_depth": "advanced",
                "max_results": 5,
                "include_answer": True,
            },
            timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            answer = data.get("answer", "")
            results = data.get("results", [])
            if not answer and results:
                snippets = []
                for result in results[:5]:
                    snippet = result.get("content", "").strip()
                    if snippet:
                        snippets.append(snippet[:500])
                answer = "\n\n".join(snippets)
            if answer or results:
                sources = []
                for result in results[:5]:
                    url = result.get("url")
                    if not url:
                        continue
                    sources.append({
                        "title": (result.get("title") or url).strip(),
                        "url": url,
                        "published_date": result.get("published_date"),
                    })
                return {
                    "answer": answer or "В найденных материалах нет краткого вывода.",
                    "sources": sources,
                    "evidence": "\n".join(
                        [answer] + [result.get("content", "") for result in results[:5]]
                    ),
                    "checked_at": checked_at,
                    "period_start": period_start,
                    "period_end": period_end,
                }
        return None
    except Exception as e:
        logger.warning(f"Tavily search failed: {e}")
        return None


def _easter_sunday(year):
    """Return Easter Sunday for the Gregorian calendar."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return datetime(year, month, day).date()


def _german_public_holidays(year):
    """Nationwide German public holidays; state-specific holidays need a region."""
    easter = _easter_sunday(year)
    return {
        datetime(year, 1, 1).date(),
        easter - timedelta(days=2),
        easter + timedelta(days=1),
        datetime(year, 5, 1).date(),
        easter + timedelta(days=39),
        easter + timedelta(days=50),
        datetime(year, 10, 3).date(),
        datetime(year, 12, 25).date(),
        datetime(year, 12, 26).date(),
    }


def _russian_full_date(day):
    weekdays = (
        "понедельник", "вторник", "среда", "четверг",
        "пятница", "суббота", "воскресенье",
    )
    months = (
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    )
    return f"{day.day} {months[day.month - 1]} {day.year} года — {weekdays[day.weekday()]}"


def _summarize_germany_ban(live_info):
    """Render Germany's confirmed base rule first, using web data only for other days."""
    live_info = live_info or {}
    evidence = live_info.get("evidence", "")
    period_start = live_info.get("period_start", datetime.now(TZ).date())
    sentences = re.split(r"(?<=[.!?])\s+", evidence)
    no_ban_markers = (
        "no ban", "no driving ban", "no restrictions", "without restrictions",
        "kein fahrverbot", "keine fahrverbote", "keine beschränkung",
        "нет запрета", "запретов нет",
    )
    ban_markers = (
        "driving ban", "truck ban", "traffic ban", "restriction applies",
        "fahrverbot", "fahrverbote", "lastkraftwagen", "truck restriction",
        "запрет движения", "ограничение движения",
    )

    def live_status(day):
        tokens = (
            day.strftime("%Y-%m-%d"), day.strftime("%d.%m.%Y"),
            day.strftime("%-d.%-m.%Y"), day.strftime("%d/%m/%Y"),
        )
        context = " ".join(
            sentence for sentence in sentences
            if any(re.search(re.escape(token), sentence) for token in tokens)
        ).lower()
        if not context:
            return "Нет точных данных"
        if any(marker in context for marker in no_ban_markers):
            return "запрета нет\nМожешь гнать, только помни: тормозить надо ногами, а не головой"
        if not any(marker in context for marker in ban_markers):
            return "Нет точных данных"
        time_match = re.search(
            r"\b\d{1,2}(?::|\.)\d{2}\s*(?:[-–—]|to|до)\s*\d{1,2}(?::|\.)\d{2}\b",
            context, re.I,
        )
        if not time_match:
            return "Нет точных данных"
        hours = re.sub(r"\s+to\s+", " до ", time_match.group(0), flags=re.I)
        hours = hours.replace("–", " до ").replace("—", " до ")
        return f"запрет {hours}"

    text = "🇩🇪 Германия\nДля грузовиков тяжелее 7,5 т и грузовиков с прицепом\n\n"
    holidays = _german_public_holidays(period_start.year)
    for offset in range(4):
        day = period_start + timedelta(days=offset)
        if day.weekday() == 6 or day in holidays:
            status = "запрет 00:00–22:00 (есть исключения)"
        else:
            status = live_status(day)
        text += f"{_russian_full_date(day)} — {status}\n"
    return text


def _summarize_live_ban(live_info, country_name):
    """Render only a cautious, four-day Russian table for a driver."""
    evidence = live_info.get("evidence", "")
    weekdays = (
        "понедельник", "вторник", "среда", "четверг",
        "пятница", "суббота", "воскресенье",
    )
    months = (
        "января", "февраля", "марта", "апреля", "мая", "июня",
        "июля", "августа", "сентября", "октября", "ноября", "декабря",
    )
    no_ban_markers = (
        "no ban", "no driving ban", "no restrictions", "without restrictions",
        "kein fahrverbot", "keine fahrverbote", "keine beschränkung",
        "нет запрета", "запретов нет",
    )
    ban_markers = (
        "driving ban", "truck ban", "traffic ban", "restriction applies",
        "fahrverbot", "fahrverbote", "lastkraftwagen", "truck restriction",
        "запрет движения", "ограничение движения",
    )
    weight_match = re.search(r"(?:>\s*|over\s*|above\s*|более\s*)(\d+(?:[.,]\d+)?)\s*t", evidence, re.I)
    weight_text = (
        f"Для грузовиков тяжелее {weight_match.group(1).replace(',', '.')} т"
        if weight_match else ""
    )

    def date_tokens(day):
        return (
            day.strftime("%Y-%m-%d"),
            day.strftime("%d.%m.%Y"),
            day.strftime("%-d.%-m.%Y"),
            day.strftime("%d/%m/%Y"),
        )

    def status_for_day(day):
        context_parts = []
        sentences = re.split(r"(?<=[.!?])\s+", evidence)
        for token in date_tokens(day):
            context_parts.extend(
                sentence for sentence in sentences if re.search(re.escape(token), sentence)
            )
        context = " ".join(context_parts).lower()
        if not context:
            return "Нет точных данных"
        has_no_ban = any(marker in context for marker in no_ban_markers)
        has_ban = any(marker in context for marker in ban_markers)
        if has_no_ban:
            return "Нет"
        if not has_ban:
            return "Нет точных данных"
        time_match = re.search(
            r"\b\d{1,2}(?::|\.)\d{2}\s*(?:[-–—]|to|до)\s*\d{1,2}(?::|\.)\d{2}\b",
            context, re.I,
        )
        if time_match:
            hours = re.sub(r"\s+to\s+", "–", time_match.group(0), flags=re.I).replace(" до ", "–")
            return f"запрет с {hours.replace('–', ' до ')}"
        return "Нет точных данных"

    text = f"{country_name}\n"
    if weight_text:
        text += f"{weight_text}\n"
    text += "\n"
    for offset in range(4):
        day = live_info["period_start"] + timedelta(days=offset)
        date_text = f"{day.day} {months[day.month - 1]} {day.year} года"
        status = status_for_day(day)
        if status == "Нет":
            status = "запрета нет\nМожешь гнать, только помни: тормозить надо ногами, а не головой"
        elif status == "Нет точных данных":
            status = "Нет точных данных"
        text += f"{date_text} — {weekdays[day.weekday()]} — {status}\n"
    return text


def _get_driving_ban_info(country_code):
    """Get driving ban info for a specific country. Uses Tavily for live data, falls back to static."""
    ban = DRIVING_BANS.get(country_code)
    if not ban:
        return "Информация не найдена."
    if country_code == "de":
        # Germany's nationwide Sunday/holiday rule is deterministic and must
        # remain visible even when Tavily is unavailable or inconclusive.
        live_info = _search_driving_bans_tavily(ban['country'])
        return _summarize_germany_ban(live_info)
    # Try to get live data from Tavily
    live_info = _search_driving_bans_tavily(ban['country'])

    if not live_info:
        checked_at = datetime.now(TZ)
        live_info = {
            "evidence": "",
            "period_start": checked_at.date(),
            "period_end": checked_at.date() + timedelta(days=3),
        }
    return _summarize_live_ban(live_info, ban['country'])


# --- Fines Reference Data ---

FINES_DATA = {
    "de": {
        "country": "🇩🇪 Германия",
        "fines": [
            ("Превышение РТО (>4.5ч без перерыва)", "60—600€"),
            ("Перегруз (>5%)", "150—425€"),
            ("Нарушение запрета движения", "75—600€"),
            ("Неисправный тахограф", "250—5000€"),
            ("Отсутствие карты водителя", "250€"),
            ("Превышение скорости (>20 км/ч)", "160—680€"),
            ("Неправильная крепёж груза", "75—425€"),
            ("Езда в нетрезвом состоянии (>0.5‰)", "500—1500€ + лишение"),
        ]
    },
    "pl": {
        "country": "🇵🇱 Польша",
        "fines": [
            ("Превышение РТО", "500—2000 PLN (~115—460€)"),
            ("Перегруз", "500—15000 PLN (~115—3450€)"),
            ("Нарушение запрета движения", "500—2000 PLN"),
            ("Неисправный тахограф", "2000—10000 PLN"),
            ("Отсутствие виньетки (viaTOLL)", "1500 PLN"),
            ("Превышение скорости (>20 км/ч)", "400—2500 PLN"),
        ]
    },
    "fr": {
        "country": "🇫🇷 Франция",
        "fines": [
            ("Превышение РТО", "135—1500€"),
            ("Перегруз", "135€ + 90€/тонну сверху"),
            ("Нарушение запрета движения", "135—1500€"),
            ("Неисправный тахограф", "750—3750€"),
            ("Превышение скорости (>20 км/ч)", "135—1500€"),
            ("Алкоголь (>0.5‰)", "135—4500€ + лишение"),
        ]
    },
    "at": {
        "country": "🇦🇹 Австрия",
        "fines": [
            ("Превышение РТО", "150—5000€"),
            ("Перегруз", "300—5000€"),
            ("Ночной запрет Тироль", "220—5000€"),
            ("Отсутствие GO-Box", "240€ + доплата"),
            ("Превышение скорости (>20 км/ч)", "70—2180€"),
        ]
    },
    "cz": {
        "country": "🇨🇿 Чехия",
        "fines": [
            ("Превышение РТО", "до 100 000 CZK (~4000€)"),
            ("Перегруз", "5000—50000 CZK (~200—2000€)"),
            ("Нарушение запрета движения", "5000—50000 CZK"),
            ("Отсутствие виньетки", "5000 CZK (~200€)"),
            ("Превышение скорости (>20 км/ч)", "2500—25000 CZK"),
        ]
    },
    "it": {
        "country": "🇮🇹 Италия",
        "fines": [
            ("Превышение РТО", "400—1600€"),
            ("Перегруз", "430—1731€"),
            ("Нарушение запрета движения", "400—1600€"),
            ("Превышение скорости (>20 км/ч)", "175—694€"),
            ("Алкоголь (>0.5‰)", "530—6000€ + лишение"),
        ]
    },
    "es": {
        "country": "🇪🇸 Испания",
        "fines": [
            ("Превышение РТО", "301—6000€"),
            ("Перегруз", "301—4600€"),
            ("Неисправный тахограф", "1001—2000€"),
            ("Превышение скорости (>20 км/ч)", "301—600€"),
            ("Алкоголь (>0.3‰)", "500—1000€ + лишение"),
        ]
    },
}


def _get_fines_info(country_code):
    """Get fines info for a specific country."""
    data = FINES_DATA.get(country_code)
    if not data:
        return "Информация не найдена."

    text = f"{data['country']}\n"
    text += f"━━━━━━━━━━━━━━━\n\n"
    for violation, amount in data['fines']:
        text += f"⚠️ {violation}\n   💰 {amount}\n\n"
    text += "ℹ️ Суммы могут меняться. Проверяй актуальную инфо!"
    return text


# --- Expense Log Helpers ---

def _save_expense(user_id, category, amount, note=""):
    """Save an expense to the database."""
    conn = get_db_connection()
    cursor = conn.cursor()
    now = datetime.now(TZ).isoformat()
    cursor.execute(
        "INSERT INTO expenses (user_id, date, category, amount, currency, note) VALUES (?, ?, ?, ?, 'EUR', ?)",
        (user_id, now, category, amount, note)
    )
    conn.commit()
    conn.close()


def _get_expense_summary(user_id, days=7):
    """Get expense summary for the last N days."""
    conn = get_db_connection()
    cursor = conn.cursor()
    since = (datetime.now(TZ) - timedelta(days=days)).isoformat()
    cursor.execute(
        "SELECT category, SUM(amount) as total FROM expenses WHERE user_id = ? AND date >= ? GROUP BY category",
        (user_id, since)
    )
    rows = cursor.fetchall()

    # Also get total
    cursor.execute(
        "SELECT SUM(amount) as grand_total FROM expenses WHERE user_id = ? AND date >= ?",
        (user_id, since)
    )
    grand_total = cursor.fetchone()["grand_total"] or 0

    # Get recent entries
    cursor.execute(
        "SELECT date, category, amount, note FROM expenses WHERE user_id = ? AND date >= ? ORDER BY date DESC LIMIT 10",
        (user_id, since)
    )
    recent = cursor.fetchall()
    conn.close()

    period = "неделю" if days == 7 else "месяц"
    cat_names = {"food": "🛒 Продукты", "alcohol": "🍺 Алкоголь", "cafe": "🍽 Еда (кафе)", "service": "🚿 Душ/стирка", "other": "📝 Другое", "fuel": "⛽ Топливо", "toll": "🛣 Дороги"}

    if not rows:
        return f"💰 Расходы за {period}\n\nНет записей."

    text = f"💰 Расходы за {period}\n━━━━━━━━━━━━━━━\n\n"

    for row in rows:
        cat = cat_names.get(row["category"], row["category"])
        text += f"{cat}: {row['total']:.2f}€\n"

    text += f"\n💵 ИТОГО: {grand_total:.2f}€\n"

    if recent:
        text += f"\n🗓 Последние записи:\n"
        for entry in recent[:5]:
            date_str = entry["date"][:10]
            cat = cat_names.get(entry["category"], entry["category"])
            note = f" ({entry['note']})".rstrip() if entry["note"] else ""
            text += f"  {date_str} {cat} {entry['amount']:.2f}€{note}\n"

    return text


# --- Currency Converter ---

CURRENCY_RATES = {
    # Approximate rates (updated periodically)
    "eur_pln": 4.28,
    "eur_czk": 25.10,
    "eur_huf": 395.0,
    "eur_ron": 4.97,
    "eur_gbp": 0.84,
    "eur_chf": 0.94,
}


def _convert_currency(amount, pair):
    """Convert currency. pair format: 'eur_pln' or 'pln_eur'."""
    parts = pair.lower().split("_")
    if len(parts) != 2:
        return None, None, None

    from_cur = parts[0].upper()
    to_cur = parts[1].upper()

    # Try direct rate
    direct_key = f"{parts[0]}_{parts[1]}"
    reverse_key = f"{parts[1]}_{parts[0]}"

    if direct_key in CURRENCY_RATES:
        rate = CURRENCY_RATES[direct_key]
        result = amount * rate
    elif reverse_key in CURRENCY_RATES:
        rate = 1 / CURRENCY_RATES[reverse_key]
        result = amount * rate
    else:
        return None, None, None

    return result, from_cur, to_cur


def _try_auto_currency(text):
    """Try to parse currency conversion from free text like '100 pln' or '500 czk'."""
    text = text.strip().lower()
    match = re.match(r'([\d.,]+)\s*(pln|czk|huf|ron|gbp|chf|eur)', text)
    if not match:
        return None

    amount = float(match.group(1).replace(',', '.'))
    currency = match.group(2)

    if currency == 'eur':
        return None  # Need to know target

    # Convert to EUR
    pair = f"{currency}_eur"
    result, from_cur, to_cur = _convert_currency(amount, pair)
    if result is not None:
        return f"💱 {amount:.2f} {from_cur} = {result:.2f} {to_cur}"
    return None


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
                    [InlineKeyboardButton("📓 Журнал (2 недели)", callback_data="show_journal")],
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
            # Show confirmation before finalizing
            driving_start = ec_rules.current_driving_start_time
            if not driving_start:
                keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="rtio_menu")]]
                await query.edit_message_text(text="⚠️ Нет активного вождения.", reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                now = datetime.now(TZ)
                if driving_start.tzinfo is None:
                    driving_start = driving_start.replace(tzinfo=TZ)
                duration = now - driving_start
                start_str = driving_start.strftime('%H:%M')
                end_str = now.strftime('%H:%M')
                dur_str = format_seconds(int(duration.total_seconds()))

                text = f"📝 Подтверди вождение:\n"
                text += f"━━━━━━━━━━━━━━━\n"
                text += f"🟢 Начало: {start_str}\n"
                text += f"🔴 Конец: {end_str}\n"
                text += f"⏱ Длительность: {dur_str}\n"
                text += f"━━━━━━━━━━━━━━━\n\n"
                text += "Всё верно?"

                keyboard = [
                    [InlineKeyboardButton("✅ Подтвердить", callback_data="confirm_end_driving")],
                    [InlineKeyboardButton("✏️ Редактировать время", callback_data="edit_driving_time")],
                    [InlineKeyboardButton("❌ Отмена", callback_data="refresh_driving")],
                ]
                await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))

        # === CONFIRM END DRIVING ===
        elif query.data == "confirm_end_driving":
            response = ec_rules.end_driving()
            # Remove driving reminders
            current_jobs = context.job_queue.get_jobs_by_name(str(user_id))
            for job in current_jobs:
                job.schedule_removal()

            if "✅" in response:
                ec_rules_fresh = ECRules(user_id)
                shift_start = ec_rules_fresh.current_shift_start_time
                shift_duration = datetime.now(TZ) - shift_start if shift_start else timedelta(0)

                text = f"✅ Вождение завершено!\n\n"
                text += f"{response}\n\n"
                text += f"━━━━━━━━━━━━━━━\n"
                text += f"📋 Смена активна с {shift_start.strftime('%H:%M') if shift_start else '?'}\n"
                text += f"⏱ Длительность смены: {format_seconds(int(shift_duration.total_seconds()))}\n"

                keyboard = [
                    [InlineKeyboardButton("🛞 Снова за руль", callback_data="start_driving")],
                    [InlineKeyboardButton("🔴 Закрыть смену", callback_data="end_shift")],
                    [InlineKeyboardButton("📊 Статус", callback_data="show_status")],
                    [InlineKeyboardButton("◀️ Меню", callback_data="main_menu")],
                ]
                await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))
            else:
                keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="rtio_menu")]]
                await query.edit_message_text(text=response, reply_markup=InlineKeyboardMarkup(keyboard))

        # === EDIT DRIVING TIME ===
        elif query.data == "edit_driving_time":
            context.user_data["awaiting_driving_edit"] = True
            text = "✏️ Редактирование времени вождения\n\n"
            text += "Напиши реальное время начала и конца:\n"
            text += "Формат: 08:30 12:45\n\n"
            text += "Пример: 06:00 10:30"
            await query.edit_message_text(text=text)

        # === REFRESH DRIVING STATUS ===
        elif query.data == "refresh_driving":
            if ec_rules.current_driving_start_time:
                try:
                    await _show_driving_active(query, ec_rules)
                except Exception as refresh_err:
                    if "message is not modified" in str(refresh_err).lower():
                        await query.answer("Данные актуальны ✓")
                    else:
                        raise
                # Re-schedule reminders (they are lost after bot restart)
                _schedule_driving_reminders(context, user_id, ec_rules)
            else:
                await _show_active_shift_menu(query, ec_rules)

        # === SHOW STATUS ===
        elif query.data == "show_status":
            text = _build_status_text(ec_rules)
            keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="rtio_menu")]]
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))

        # === SHOW JOURNAL (2 weeks) ===
        elif query.data == "show_journal":
            text = _build_journal_text(user_id)
            keyboard = [[InlineKeyboardButton("◀️ Назад", callback_data="rtio_menu")]]
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")

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

        # === DRIVING BANS ===
        elif query.data == "driving_bans":
            keyboard = [
                [InlineKeyboardButton("🇩🇪 Германия", callback_data="ban_de")],
                [InlineKeyboardButton("🇵🇱 Польша", callback_data="ban_pl")],
                [InlineKeyboardButton("🇫🇷 Франция", callback_data="ban_fr")],
                [InlineKeyboardButton("🇦🇹 Австрия", callback_data="ban_at")],
                [InlineKeyboardButton("🇨🇿 Чехия", callback_data="ban_cz")],
                [InlineKeyboardButton("🇮🇹 Италия", callback_data="ban_it")],
                [InlineKeyboardButton("🇪🇸 Испания", callback_data="ban_es")],
                [InlineKeyboardButton("🇧🇪 Бельгия", callback_data="ban_be")],
                [InlineKeyboardButton("🇭🇺 Венгрия", callback_data="ban_hu")],
                [InlineKeyboardButton("🇳🇱 Нидерланды", callback_data="ban_nl")],
                [InlineKeyboardButton("◀️ Меню", callback_data="main_menu")],
            ]
            await query.edit_message_text(
                text="🚫 Запреты\n\nВыбери страну:",
                reply_markup=InlineKeyboardMarkup(keyboard))

        elif query.data.startswith("refresh_ban_"):
            country_code = query.data[len("refresh_ban_"):]
            await query.edit_message_text(text="Обновляю...")
            text = _get_driving_ban_info(country_code)
            keyboard = [
                [InlineKeyboardButton("Обновить", callback_data=f"refresh_ban_{country_code}")],
                [InlineKeyboardButton("Страны", callback_data="driving_bans")],
                [InlineKeyboardButton("Меню", callback_data="main_menu")],
            ]
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))

        elif query.data.startswith("ban_"):
            country_code = query.data[4:]
            text = _get_driving_ban_info(country_code)
            keyboard = [
                [InlineKeyboardButton("Обновить", callback_data=f"refresh_ban_{country_code}")],
                [InlineKeyboardButton("Страны", callback_data="driving_bans")],
                [InlineKeyboardButton("Меню", callback_data="main_menu")],
            ]
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))

        # === FINES REFERENCE ===
        elif query.data == "fines_menu":
            keyboard = [
                [InlineKeyboardButton("🇩🇪 Германия", callback_data="fine_de")],
                [InlineKeyboardButton("🇵🇱 Польша", callback_data="fine_pl")],
                [InlineKeyboardButton("🇫🇷 Франция", callback_data="fine_fr")],
                [InlineKeyboardButton("🇦🇹 Австрия", callback_data="fine_at")],
                [InlineKeyboardButton("🇨🇿 Чехия", callback_data="fine_cz")],
                [InlineKeyboardButton("🇮🇹 Италия", callback_data="fine_it")],
                [InlineKeyboardButton("🇪🇸 Испания", callback_data="fine_es")],
                [InlineKeyboardButton("◀️ Меню", callback_data="main_menu")],
            ]
            await query.edit_message_text(
                text="📋 Штрафы для грузовиков\n\nВыбери страну:",
                reply_markup=InlineKeyboardMarkup(keyboard))

        elif query.data.startswith("fine_"):
            country_code = query.data[5:]
            text = _get_fines_info(country_code)
            keyboard = [
                [InlineKeyboardButton("◀️ Назад", callback_data="fines_menu")],
                [InlineKeyboardButton("◀️ Меню", callback_data="main_menu")],
            ]
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))

        # === EXPENSES LOG ===
        elif query.data == "expenses_menu":
            keyboard = [
                [InlineKeyboardButton("🛒 Продукты", callback_data="exp_add_food")],
                [InlineKeyboardButton("🍺 Алкоголь", callback_data="exp_add_alcohol")],
                [InlineKeyboardButton("🍽 Еда (кафе)", callback_data="exp_add_cafe")],
                [InlineKeyboardButton("🚿 Душ/стирка", callback_data="exp_add_service")],
                [InlineKeyboardButton("📝 Другое", callback_data="exp_add_other")],
                [InlineKeyboardButton("📊 Итог за неделю", callback_data="exp_summary_week")],
                [InlineKeyboardButton("📊 Итог за месяц", callback_data="exp_summary_month")],
                [InlineKeyboardButton("◀️ Меню", callback_data="main_menu")],
            ]
            await query.edit_message_text(
                text="💰 Лог расходов\n\nВыбери категорию для записи или посмотри итог:",
                reply_markup=InlineKeyboardMarkup(keyboard))

        elif query.data.startswith("exp_add_"):
            category = query.data[8:]  # food, alcohol, cafe, service, other
            cat_names = {"food": "Продукты", "alcohol": "Алкоголь", "cafe": "Еда (кафе)", "service": "Душ/стирка", "other": "Другое"}
            context.user_data["awaiting_expense"] = True
            context.user_data["expense_category"] = category
            await query.edit_message_text(
                text=f"💰 {cat_names.get(category, category)}\n\n"
                     f"Напиши сумму (можно с комментарием):\n"
                     f"Например: 85.50 Shell Гамбург")

        elif query.data == "exp_summary_week":
            text = _get_expense_summary(user_id, days=7)
            keyboard = [
                [InlineKeyboardButton("◀️ Назад", callback_data="expenses_menu")],
                [InlineKeyboardButton("◀️ Меню", callback_data="main_menu")],
            ]
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))

        elif query.data == "exp_summary_month":
            text = _get_expense_summary(user_id, days=30)
            keyboard = [
                [InlineKeyboardButton("◀️ Назад", callback_data="expenses_menu")],
                [InlineKeyboardButton("◀️ Меню", callback_data="main_menu")],
            ]
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))

        # === CURRENCY CONVERTER ===
        elif query.data == "currency_menu":
            keyboard = [
                [InlineKeyboardButton("🇵🇱 EUR→PLN", callback_data="cur_eur_pln"), InlineKeyboardButton("🇵🇱 PLN→EUR", callback_data="cur_pln_eur")],
                [InlineKeyboardButton("🇨🇿 EUR→CZK", callback_data="cur_eur_czk"), InlineKeyboardButton("🇨🇿 CZK→EUR", callback_data="cur_czk_eur")],
                [InlineKeyboardButton("🇭🇺 EUR→HUF", callback_data="cur_eur_huf"), InlineKeyboardButton("🇭🇺 HUF→EUR", callback_data="cur_huf_eur")],
                [InlineKeyboardButton("🇷🇴 EUR→RON", callback_data="cur_eur_ron"), InlineKeyboardButton("🇷🇴 RON→EUR", callback_data="cur_ron_eur")],
                [InlineKeyboardButton("🇬🇧 EUR→GBP", callback_data="cur_eur_gbp"), InlineKeyboardButton("🇬🇧 GBP→EUR", callback_data="cur_gbp_eur")],
                [InlineKeyboardButton("🇨🇭 EUR→CHF", callback_data="cur_eur_chf"), InlineKeyboardButton("🇨🇭 CHF→EUR", callback_data="cur_chf_eur")],
                [InlineKeyboardButton("◀️ Меню", callback_data="main_menu")],
            ]
            await query.edit_message_text(
                text="💱 Конвертер валют\n\nВыбери направление конвертации:\n(или напиши сумму, например: 100 pln)",
                reply_markup=InlineKeyboardMarkup(keyboard))

        elif query.data.startswith("cur_"):
            context.user_data["awaiting_currency"] = True
            context.user_data["currency_pair"] = query.data[4:]  # e.g. "eur_pln"
            pair = query.data[4:].upper().split("_")
            await query.edit_message_text(
                text=f"💱 {pair[0]} → {pair[1]}\n\nНапиши сумму в {pair[0]}:\n(например: 100)")

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
    now = datetime.now(TZ)
    # Make shift_start timezone-aware if it isn't
    if shift_start and shift_start.tzinfo is None:
        shift_start = shift_start.replace(tzinfo=TZ)
    shift_duration = now - shift_start if shift_start else timedelta(0)
    start_time_str = shift_start.strftime('%H:%M') if shift_start else "?"
    shift_hours = shift_duration.total_seconds() / 3600

    # Shift window limits (EC 561/2006)
    # Normal: 13h max shift window (24h - 11h rest)
    # Reduced rest (3x/week): 15h max shift window (24h - 9h rest)
    MAX_SHIFT_NORMAL = 13  # hours
    MAX_SHIFT_REDUCED = 15  # hours
    reduced_rest_used = ec_rules.reduced_rest_count if hasattr(ec_rules, 'reduced_rest_count') else 0
    can_use_extended = reduced_rest_used < 3

    if ec_rules.current_driving_start_time:
        # Currently driving - show driving screen
        await _show_driving_active(query, ec_rules)
    else:
        # Shift active but not driving
        daily_driving = ec_rules.daily_driving_minutes

        # Determine shift status
        if shift_hours >= MAX_SHIFT_REDUCED:
            shift_status = "🛑 СМЕНА ПРОСРОЧЕНА!"
            shift_warning = f"\n⚠️ Смена идёт {format_seconds(int(shift_duration.total_seconds()))} — это больше максимума (15ч)!\nЗАКРОЙ СМЕНУ НЕМЕДЛЕННО!\n"
        elif shift_hours >= MAX_SHIFT_NORMAL:
            if can_use_extended:
                remaining_ext = MAX_SHIFT_REDUCED - shift_hours
                shift_status = "🟡 Смена продлена"
                shift_warning = f"\n⚠️ 13ч превышено! Работаешь по сокращённому отдыху (9ч).\n⏳ Осталось до лимита: {format_seconds(int(remaining_ext * 3600))}\n📊 Сокращённый отдых использован: {reduced_rest_used}/3 за неделю\n"
            else:
                shift_status = "🛑 СМЕНА ПРОСРОЧЕНА!"
                shift_warning = f"\n⚠️ Смена превысила 13ч! Сокращённый отдых уже использован 3/3 раза.\nЗАКРОЙ СМЕНУ НЕМЕДЛЕННО!\n"
        elif shift_hours >= MAX_SHIFT_NORMAL - 1:  # Less than 1h to limit
            remaining_norm = MAX_SHIFT_NORMAL - shift_hours
            shift_status = "🟢 Смена открыта"
            shift_warning = f"\n⏳ До лимита смены (13ч): {format_seconds(int(remaining_norm * 3600))}\n"
        else:
            remaining_norm = MAX_SHIFT_NORMAL - shift_hours
            shift_status = "🟢 Смена открыта"
            shift_warning = f"\n⏳ До лимита смены (13ч): {format_seconds(int(remaining_norm * 3600))}\n"

        text = f"{shift_status}\n"
        text += f"━━━━━━━━━━━━━━━\n"
        text += f"📋 Открытие смены: {start_time_str}\n"
        text += f"⏱ Смена идёт: {format_seconds(int(shift_duration.total_seconds()))}\n"
        text += shift_warning
        text += f"━━━━━━━━━━━━━━━\n"
        text += f"🛞 Вождение сегодня: {format_minutes(daily_driving)}\n"

        keyboard = [
            [InlineKeyboardButton("🚗 Начать вождение", callback_data="start_driving")],
            [InlineKeyboardButton("🔴 Закрыть смену", callback_data="end_shift")],
            [InlineKeyboardButton("📊 Полный статус", callback_data="show_status")],
            [InlineKeyboardButton("◀️ Меню", callback_data="main_menu")],
        ]
        await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))


async def _show_driving_active(query, ec_rules):
    """Show the active driving screen."""
    now = datetime.now(TZ)
    driving_start = ec_rules.current_driving_start_time
    if driving_start and driving_start.tzinfo is None:
        driving_start = driving_start.replace(tzinfo=TZ)
    driving_duration = now - driving_start if driving_start else timedelta(0)
    driving_start_str = driving_start.strftime('%H:%M') if driving_start else "?"

    shift_start = ec_rules.current_shift_start_time
    if shift_start and shift_start.tzinfo is None:
        shift_start = shift_start.replace(tzinfo=TZ)
    shift_duration = now - shift_start if shift_start else timedelta(0)

    # Calculate remaining continuous driving
    max_continuous = 4.5 * 3600  # 4h30m in seconds
    remaining_seconds = max_continuous - driving_duration.total_seconds()
    remaining_str = format_seconds(max(0, int(remaining_seconds)))

    # Shift window check
    shift_hours = shift_duration.total_seconds() / 3600
    MAX_SHIFT_NORMAL = 13
    MAX_SHIFT_REDUCED = 15
    remaining_shift = MAX_SHIFT_NORMAL - shift_hours
    shift_limit_str = ""
    if shift_hours >= MAX_SHIFT_REDUCED:
        shift_limit_str = "🛑 СМЕНА ПРОСРОЧЕНА! ЗАКРЫВАЙ!\n"
    elif shift_hours >= MAX_SHIFT_NORMAL:
        remaining_ext = MAX_SHIFT_REDUCED - shift_hours
        shift_limit_str = f"⚠️ Смена >13ч! До 15ч: {format_seconds(int(remaining_ext * 3600))}\n"
    elif remaining_shift <= 1:
        shift_limit_str = f"⏳ До лимита смены: {format_seconds(int(remaining_shift * 3600))}\n"

    text = f"🛞 ЗА РУЛЁМ\n"
    text += f"━━━━━━━━━━━━━━━\n"
    text += f"🟢 Начало вождения: {driving_start_str}\n"
    text += f"⏱ Едешь уже: {format_seconds(int(driving_duration.total_seconds()))}\n"
    text += f"⏳ До перерыва: {remaining_str}\n"
    text += f"━━━━━━━━━━━━━━━\n"
    text += f"📋 Смена идёт: {format_seconds(int(shift_duration.total_seconds()))}\n"
    if shift_limit_str:
        text += shift_limit_str
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

    now = datetime.now(TZ)
    if driving_start and driving_start.tzinfo is None:
        driving_start = driving_start.replace(tzinfo=TZ)
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

    # 4 min before
    time_to_4 = max_driving - 4 * 60 - elapsed
    if time_to_4 > 0:
        context.job_queue.run_once(
            driving_reminder, time_to_4,
            chat_id=user_id, name=str(user_id),
            data=random.choice(REMINDER_4MIN)
        )

    # 3 min before
    time_to_3 = max_driving - 3 * 60 - elapsed
    if time_to_3 > 0:
        context.job_queue.run_once(
            driving_reminder, time_to_3,
            chat_id=user_id, name=str(user_id),
            data=random.choice(REMINDER_3MIN)
        )

    # 2 min before
    time_to_2 = max_driving - 2 * 60 - elapsed
    if time_to_2 > 0:
        context.job_queue.run_once(
            driving_reminder, time_to_2,
            chat_id=user_id, name=str(user_id),
            data=random.choice(REMINDER_2MIN)
        )

    # 1 min before
    time_to_1 = max_driving - 1 * 60 - elapsed
    if time_to_1 > 0:
        context.job_queue.run_once(
            driving_reminder, time_to_1,
            chat_id=user_id, name=str(user_id),
            data=random.choice(REMINDER_1MIN)
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
    # --- Driving time edit input ---
    if context.user_data.get("awaiting_driving_edit"):
        text = update.message.text.strip()
        # Parse format: HH:MM HH:MM
        match = re.match(r'(\d{1,2})[:.](\d{2})\s+(\d{1,2})[:.](\d{2})', text)
        if match:
            try:
                start_h, start_m = int(match.group(1)), int(match.group(2))
                end_h, end_m = int(match.group(3)), int(match.group(4))

                user_id = update.message.from_user.id
                today = datetime.now(TZ).date()
                new_start = datetime(today.year, today.month, today.day, start_h, start_m, tzinfo=TZ)
                new_end = datetime(today.year, today.month, today.day, end_h, end_m, tzinfo=TZ)

                if new_end <= new_start:
                    await update.message.reply_text("❌ Время конца должно быть позже начала!")
                    context.user_data["awaiting_driving_edit"] = False
                    await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=get_main_menu_keyboard())
                    return

                duration_minutes = int((new_end - new_start).total_seconds() / 60)

                # Update the active driving session in DB with edited times
                conn = get_db_connection()
                cursor = conn.cursor()
                # Update start time of the active driving session
                cursor.execute(
                    "UPDATE driving_sessions SET start_time = ?, end_time = ?, duration = ? "
                    "WHERE user_id = ? AND session_type = 'driving' AND end_time IS NULL",
                    (new_start.isoformat(), new_end.isoformat(), duration_minutes, user_id))
                conn.commit()
                conn.close()

                # Update daily stats
                ec_rules = ECRules(user_id)
                ec_rules._update_driving_stats(duration_minutes)

                dur_str = format_seconds(duration_minutes * 60)
                await update.message.reply_text(
                    f"✅ Вождение записано!\n\n"
                    f"🟢 Начало: {start_h:02d}:{start_m:02d}\n"
                    f"🔴 Конец: {end_h:02d}:{end_m:02d}\n"
                    f"⏱ Длительность: {dur_str}")
            except (ValueError, OverflowError):
                await update.message.reply_text("❌ Неверный формат. Напиши: 08:30 12:45")
        else:
            await update.message.reply_text("❌ Формат: 08:30 12:45 (начало конец)")

        context.user_data["awaiting_driving_edit"] = False
        await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=get_main_menu_keyboard())
        return

    # --- Expense input ---
    if context.user_data.get("awaiting_expense"):
        text = update.message.text.strip()
        # Parse amount and optional note
        match = re.match(r'([\d.,]+)\s*(.*)', text)
        if match:
            try:
                amount = float(match.group(1).replace(',', '.'))
                note = match.group(2).strip()
                category = context.user_data.get("expense_category", "other")
                user_id = update.message.from_user.id
                _save_expense(user_id, category, amount, note)

                cat_names = {"food": "🛒 Продукты", "alcohol": "🍺 Алкоголь", "cafe": "🍽 Еда (кафе)", "service": "🚿 Душ/стирка", "other": "📝 Другое"}
                cat_name = cat_names.get(category, category)
                note_text = f" ({note})" if note else ""
                await update.message.reply_text(
                    f"✅ Записано: {cat_name} — {amount:.2f}€{note_text}")
            except ValueError:
                await update.message.reply_text("❌ Не понял сумму. Напиши число, например: 85.50 Shell")
        else:
            await update.message.reply_text("❌ Напиши сумму, например: 85.50 Shell Гамбург")

        context.user_data["awaiting_expense"] = False
        await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=get_main_menu_keyboard())
        return

    # --- Currency input ---
    if context.user_data.get("awaiting_currency"):
        text = update.message.text.strip()
        try:
            amount = float(text.replace(',', '.'))
            pair = context.user_data.get("currency_pair", "eur_pln")
            result, from_cur, to_cur = _convert_currency(amount, pair)
            if result is not None:
                await update.message.reply_text(f"💱 {amount:.2f} {from_cur} = {result:.2f} {to_cur}")
            else:
                await update.message.reply_text("❌ Не удалось конвертировать.")
        except ValueError:
            # Try auto-detect currency from text like "100 pln"
            auto_result = _try_auto_currency(text)
            if auto_result:
                await update.message.reply_text(auto_result)
            else:
                await update.message.reply_text("❌ Напиши число, например: 100")

        context.user_data["awaiting_currency"] = False
        await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=get_main_menu_keyboard())
        return

    # --- Route input ---
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

    # Start web server to keep Render alive
    keep_alive()
    
    # Run
    logger.info("Bot starting...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
