import logging
import os
import sqlite3
import re
from datetime import datetime, timedelta

import requests
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters, JobQueue

from ec_rules import ECRules
from parking_search import ParkingSearch

# Enable logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# Bot token and API keys (set in Railway environment variables)
TOKEN = os.environ.get("BOT_TOKEN")
OPENROUTE_SERVICE_API_KEY = os.environ.get("OPENROUTE_SERVICE_API_KEY", "") # New API key for OpenRouteService

# Admin settings
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

if not TOKEN:
    raise ValueError("BOT_TOKEN environment variable is not set! Add it in Railway settings.")

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
        [InlineKeyboardButton("🗺️ Планировщик маршрута", callback_data="route_planner")], # New button
        [InlineKeyboardButton("📊 Статус РТиО", callback_data="rtio_status")],
        [InlineKeyboardButton("📈 Статистика", callback_data="statistics")],
        [InlineKeyboardButton("💬 Помощник общения", callback_data="communication_helper")], # New button
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
        username = f"@{u["telegram_username"]}" if u["telegram_username"] else "без username"
        name = u["first_name"] or ""
        if u["last_name"]:
            name += f" {u["last_name"]}"
        reg_date = u["registration_date"][:10] if u["registration_date"] else "?"
        text += f"• {name} ({username}) — ID: {u["user_id"]} — рег: {reg_date}\n"

    await update.message.reply_text(text)


# --- JobQueue Callbacks for Notifications ---

async def continuous_driving_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    job = context.job
    await context.bot.send_message(job.chat_id, text=job.data)


# --- OpenRouteService Helper Functions ---

async def geocode_city(city_name: str) -> tuple[float, float] | None:
    if not OPENROUTE_SERVICE_API_KEY:
        logger.error("OPENROUTE_SERVICE_API_KEY is not set.")
        return None
    
    geocode_url = "https://api.openrouteservice.org/geocode/search"
    params = {
        "api_key": OPENROUTE_SERVICE_API_KEY,
        "text": city_name,
        "boundary.country": "DE,FR,BE,NL,LU,AT,CH,PL,CZ,SK,HU,SI,HR,IT,ES,PT,GB,IE,DK,SE,NO,FI,EE,LV,LT,BG,RO,GR", # Limit to Europe
        "size": 1
    }
    try:
        response = requests.get(geocode_url, params=params)
        response.raise_for_status()
        data = response.json()
        if data and data["features"]:
            coords = data["features"][0]["geometry"]["coordinates"]
            return coords[1], coords[0] # OpenRouteService returns [lon, lat]
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"OpenRouteService geocoding error for {city_name}: {e}")
        return None

async def get_route_details(start_coords: tuple[float, float], end_coords: tuple[float, float]) -> dict | None:
    if not OPENROUTE_SERVICE_API_KEY:
        logger.error("OPENROUTE_SERVICE_API_KEY is not set.")
        return None

    route_url = "https://api.openrouteservice.org/v2/directions/driving-hgv"
    headers = {
        "Authorization": OPENROUTE_SERVICE_API_KEY,
        "Content-Type": "application/json"
    }
    body = {
        "coordinates": [
            [start_coords[1], start_coords[0]], # ORS expects [lon, lat]
            [end_coords[1], end_coords[0]]
        ]
    }
    try:
        response = requests.post(route_url, headers=headers, json=body)
        response.raise_for_status()
        data = response.json()
        if data and data["routes"]:
            route = data["routes"][0]
            distance_km = route["summary"]["distance"] / 1000
            duration_seconds = route["summary"]["duration"]
            duration_hours = duration_seconds / 3600
            return {
                "distance_km": distance_km,
                "duration_hours": duration_hours,
                "geometry": route["geometry"] # GeoJSON linestring
            }
        return None
    except requests.exceptions.RequestException as e:
        logger.error(f"OpenRouteService routing error: {e}")
        return None


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
            # Remove all scheduled jobs for this user when shift ends
            current_jobs = context.job_queue.get_jobs_by_name(str(user_id))
            for job in current_jobs:
                job.schedule_removal()
            logger.info(f"Removed all jobs for user {user_id} due to end_shift.")

            keyboard = [[InlineKeyboardButton("◀️ Меню", callback_data="main_menu")]]
            await query.edit_message_text(text=response, reply_markup=InlineKeyboardMarkup(keyboard))

        elif query.data == "start_driving":
            response = ec_rules.start_driving()
            if "✅ Вождение начато." in response: # Only schedule if driving successfully started
                # Schedule notifications
                now = datetime.now()
                # 30 min before 4.5h limit (4h driving)
                # Calculate time until 4.5h driving limit from now
                time_to_4_5h_limit = ec_rules.MAX_CONTINUOUS_DRIVING.total_seconds() - (now - ec_rules.get_current_driving_session_start_time()).total_seconds()
                if time_to_4_5h_limit > 30*60: # Ensure there's enough time for the reminder
                    context.job_queue.run_once(continuous_driving_reminder, time_to_4_5h_limit - 30*60, 
                                               chat_id=user_id, name=str(user_id), data="Через 30 минут нужна пауза")
                
                # 1 hour before daily limit
                daily_limit_seconds = ec_rules.MAX_DAILY_DRIVING_NORMAL.total_seconds()
                if ec_rules.extended_driving_count < ec_rules.MAX_EXTENDED_DRIVING_PER_WEEK:
                    daily_limit_seconds = ec_rules.MAX_DAILY_DRIVING_EXTENDED.total_seconds()
                
                # Calculate time until daily limit from now
                time_to_daily_limit = daily_limit_seconds - (ec_rules.daily_driving_minutes * 60 + (now - ec_rules.get_current_driving_session_start_time()).total_seconds() if ec_rules.get_current_driving_session_start_time() else 0)
                if time_to_daily_limit > 60*60: # Ensure there's enough time for the reminder
                    context.job_queue.run_once(continuous_driving_reminder, time_to_daily_limit - 60*60, 
                                               chat_id=user_id, name=str(user_id), data="Через 1 час заканчивается дневной лимит")
                
                # 15 min before 4.5h limit
                if time_to_4_5h_limit > 15*60: # Ensure there's enough time for the reminder
                    context.job_queue.run_once(continuous_driving_reminder, time_to_4_5h_limit - 15*60, 
                                               chat_id=user_id, name=str(user_id), data="Осталось 15 минут вождения!")
                logger.info(f"Scheduled driving reminders for user {user_id}.")

            keyboard = [
                [InlineKeyboardButton("⏹ Закончить вождение", callback_data="end_driving")],
                [InlineKeyboardButton("◀️ Меню", callback_data="main_menu")],
            ]
            await query.edit_message_text(text=response, reply_markup=InlineKeyboardMarkup(keyboard))

        elif query.data == "end_driving":
            response = ec_rules.end_driving()
            # Remove continuous driving reminders
            current_jobs = context.job_queue.get_jobs_by_name(str(user_id))
            for job in current_jobs:
                if job.data in ["Через 30 минут нужна пауза", "Через 1 час заканчивается дневной лимит", "Осталось 15 минут вождения!"]:
                    job.schedule_removal()
            logger.info(f"Removed continuous driving jobs for user {user_id}.")

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

        elif query.data == "route_planner":
            await query.edit_message_text(
                text="🗺️ Отправьте мне ваш маршрут в формате: `Из [Город А] в [Город Б]` (например, `Из Берлина в Париж`).")
            context.user_data["awaiting_route"] = True

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
                f"• {status["extended_driving_info"]}\n"
                f"• {status["weekly_rest_info"]}\n"
            )
            keyboard = [[InlineKeyboardButton("◀️ Меню", callback_data="main_menu")]]
            await query.edit_message_text(text=text, reply_markup=InlineKeyboardMarkup(keyboard))

        elif query.data == "communication_helper":
            keyboard = [
                [InlineKeyboardButton("Написать о задержке", callback_data="comm_delay")],
                [InlineKeyboardButton("Сообщить о поломке", callback_data="comm_breakdown")],
                [InlineKeyboardButton("Изменение маршрута", callback_data="comm_route_change")],
                [InlineKeyboardButton("Свободный текст", callback_data="comm_free_text")],
                [InlineKeyboardButton("◀️ Меню", callback_data="main_menu")],
            ]
            await query.edit_message_text(text="💬 Выберите тип сообщения:", reply_markup=InlineKeyboardMarkup(keyboard))

        elif query.data.startswith("comm_"):
            context.user_data["communication_type"] = query.data
            if query.data == "comm_delay":
                await query.edit_message_text("Напишите причину задержки и ожидаемое время прибытия (например, `Пробка на А2, прибуду через 2 часа`).")
                context.user_data["awaiting_comm_input"] = True
            elif query.data == "comm_breakdown":
                await query.edit_message_text("Опишите поломку и ваше текущее местоположение (например, `Прокол колеса, стою на 150 км трассы Е40`).")
                context.user_data["awaiting_comm_input"] = True
            elif query.data == "comm_route_change":
                await query.edit_message_text("Опишите причину изменения маршрута и новый маршрут (например, `Объезд из-за ремонта дороги, поеду через город X`).")
                context.user_data["awaiting_comm_input"] = True
            elif query.data == "comm_free_text":
                await query.edit_message_text("Напишите текст, который нужно оформить профессионально.")
                context.user_data["awaiting_comm_input"] = True

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
        f"⏱ Непрерывное вождение: {status["remaining_continuous_driving"]}\n"
        f"☕ Перерыв: {status["required_break"]}\n\n"
        f"📊 Дневной лимит: {status["daily_driving_limit_status"]}\n"
        f"📊 Недельный лимит: {status["weekly_driving_limit_status"]}\n"
        f"📊 2-недельный лимит: {status["bi_weekly_driving_limit_status"]}\n\n"
        f"😴 Отдых: {status["daily_rest_status"]}\n\n"
        f"📋 Лимиты на неделю:\n"
        f"• {status["extended_driving_info"]}\n"
        f"• {status["weekly_rest_info"]}\n"
    )


async def handle_location(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get("awaiting_location"):
        latitude = update.message.location.latitude
        longitude = update.message.location.longitude
        await update.message.reply_text("🔍 Ищу парковки для грузовиков рядом...")

        try:
            parking_finder = ParkingSearch()
            parkings = parking_finder.search_parking(latitude, longitude, radius=25000) # Use updated radius

            if parkings:
                response_text = "🅿️ Парковки для грузовиков:\n\n"
                for i, parking in enumerate(parkings[:5], 1):
                    name = parking.get("name", "Без названия")
                    lat = parking.get("latitude")
                    lon = parking.get("longitude")
                    distance = parking.get("distance", 0)

                    amenities_str = []
                    if parking["amenities"]["shower"]: amenities_str.append("Душ")
                    if parking["amenities"]["wc"]: amenities_str.append("Туалет")
                    if parking["amenities"]["fuel"]: amenities_str.append("Топливо")
                    if parking["amenities"]["restaurant"]: amenities_str.append("Ресторан")
                    amenities_display = f" ({", ".join(amenities_str)})" if amenities_str else ""

                    response_text += f"{i}. {name} ({distance:.2f} км){amenities_display}\n"
                    response_text += f"   📍 https://www.google.com/maps/search/?api=1&query={lat},{lon}\n\n"
                
                response_text += "\n🔗 Больше парковок: https://www.transparking.eu/map"
                await update.message.reply_text(response_text)
            else:
                await update.message.reply_text(
                    "❌ Парковки для грузовиков не найдены в радиусе 25 км.\n"
                    "Попробуйте отправить другую геолокацию.")
        except Exception as e:
            logger.error(f"Parking search error: {e}")
            await update.message.reply_text("⚠️ Ошибка при поиске парковок. Попробуйте позже.")

        context.user_data["awaiting_location"] = False
        await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=get_main_menu_keyboard())
    else:
        await update.message.reply_text(
            "Используйте кнопку \'🅿️ Поиск парковки\' в меню.",
            reply_markup=get_main_menu_keyboard())


async def handle_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if context.user_data.get("awaiting_route"):
        route_query = update.message.text
        match = re.match(r"Из (.+) в (.+)", route_query, re.IGNORECASE)
        if match:
            origin_city = match.group(1).strip()
            destination_city = match.group(2).strip()
            await update.message.reply_text(f"🗺️ Строю маршрут из {origin_city} в {destination_city}...")

            try:
                origin_coords = await geocode_city(origin_city)
                destination_coords = await geocode_city(destination_city)

                if not origin_coords:
                    await update.message.reply_text(f"Не удалось найти координаты для города: {origin_city}")
                    context.user_data["awaiting_route"] = False
                    await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=get_main_menu_keyboard())
                    return
                if not destination_coords:
                    await update.message.reply_text(f"Не удалось найти координаты для города: {destination_city}")
                    context.user_data["awaiting_route"] = False
                    await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=get_main_menu_keyboard())
                    return

                route_details = await get_route_details(origin_coords, destination_coords)

                if route_details:
                    distance_km = route_details["distance_km"]
                    driving_time_hours = route_details["duration_hours"]
                    route_geometry = route_details["geometry"]

                    response_text = f"🗺️ Маршрут: {origin_city} - {destination_city}\n"
                    response_text += f"📏 Расстояние: {distance_km:.0f} км\n"
                    response_text += f"⏱ Расчетное время в пути: {driving_time_hours:.1f} ч\n\n"
                    response_text += "☕ Рекомендуемые остановки для отдыха (по EC 561/2006):\n"

                    current_driving_hours = 0
                    stop_count = 1
                    while current_driving_hours < driving_time_hours:
                        current_driving_hours += 4.5 # Drive for 4.5 hours
                        if current_driving_hours < driving_time_hours:
                            response_text += f"  {stop_count}. После {min(current_driving_hours, driving_time_hours):.1f} ч вождения: 45 мин перерыв.\n"
                            stop_count += 1
                        else:
                            response_text += f"  {stop_count}. Конечная точка маршрута.\n"
                    
                    # For now, we won't search parkings along the route dynamically due to complexity and potential API limits.
                    # This would require sampling points along the route geometry and performing multiple parking searches.
                    response_text += "\n🅿️ Поиск парковок вдоль маршрута: (для этой функции требуется более сложная интеграция, используйте отдельный поиск парковок по геолокации)"
                    await update.message.reply_text(response_text)
                else:
                    await update.message.reply_text("Не удалось построить маршрут. Проверьте названия городов или попробуйте позже.")

            except Exception as e:
                logger.error(f"Route planning error: {e}")
                await update.message.reply_text("⚠️ Ошибка при планировании маршрута. Попробуйте позже.")
        else:
            await update.message.reply_text("Неверный формат. Пожалуйста, используйте: `Из [Город А] в [Город Б]`")
        
        context.user_data["awaiting_route"] = False
        await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=get_main_menu_keyboard())
    elif context.user_data.get("awaiting_comm_input"):
        user_input = update.message.text
        comm_type = context.user_data.get("communication_type")
        generated_message = ""

        if comm_type == "comm_delay":
            generated_message = f"Уважаемый получатель,\n\nСообщаю о задержке доставки. Причина: {user_input}. Ожидаемое время прибытия изменено.\n\nС уважением, Водитель."
        elif comm_type == "comm_breakdown":
            generated_message = f"Уважаемый получатель,\n\nСообщаю о поломке транспортного средства. Описание: {user_input}. Ожидаю дальнейших инструкций.\n\nС уважением, Водитель."
        elif comm_type == "comm_route_change":
            generated_message = f"Уважаемый получатель,\n\nСообщаю об изменении маршрута. Причина и новый маршрут: {user_input}.\n\nС уважением, Водитель."
        elif comm_type == "comm_free_text":
            # Simple professional formatting for free text
            generated_message = f"Уважаемый получатель,\n\n{user_input}\n\nС уважением, Водитель."
        
        await update.message.reply_text(f"Ваше профессиональное сообщение:\n\n```\n{generated_message}\n```")
        context.user_data["awaiting_comm_input"] = False
        context.user_data["communication_type"] = None
        await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=get_main_menu_keyboard())
    else:
        await update.message.reply_text(MAIN_MENU_TEXT, reply_markup=get_main_menu_keyboard())


def main() -> None:
    """Start the bot."""
    application = Application.builder().token(TOKEN).build()
    job_queue = application.job_queue # Initialize JobQueue

    # Commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", status_command))
    application.add_handler(CommandHandler("admin", admin_command))

    # Location
    application.add_handler(MessageHandler(filters.LOCATION, handle_location))

    # Text messages (for route planning and communication helper)
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
    main()
