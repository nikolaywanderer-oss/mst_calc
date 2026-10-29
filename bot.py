import os
import asyncio
import threading
import logging
import json
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ADMIN_ID_RAW = os.environ.get("ADMIN_TELEGRAM_ID", "0")
ADMIN_ID = int(ADMIN_ID_RAW) if ADMIN_ID_RAW.isdigit() else 0
HERE_API_KEY = os.environ.get("HERE_API_KEY", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")

# Conversation states
CHOOSE_LANG, GET_FROM, GET_TO, CHOOSE_CAR, GET_DATETIME, GET_PHONE = range(6)

TEXTS = {
    "ru": {
        "welcome": (
            "🚗 **Добро пожаловать в MySwissTransfer!**\n\n"
            "Вы можете отправить заказ в произвольной форме, например:\n"
            "💬 *«Цюрих аэропорт — Цюрих 29.12.26 13:10 для 2 человек»*\n\n"
            "Или нажмите /start для пошагового заполнения."
        ),
        "ask_from": "📍 Введите адрес отправления (Точка А):",
        "ask_to": "📍 Введите адрес прибытия (Точка Б):",
        "calculating": "⏳ Рассчитываю маршрут...",
        "distance": "📏 Расстояние: {:.1f} км\n\nВыберите класс автомобиля:",
        "ask_datetime": "📅 Введите дату и время поездки\n(например: 25.07.2025 14:30):",
        "ask_phone": "📞 Введите ваш номер телефона:",
        "order_summary": (
            "✅ Ваш заказ принят!\n\n"
            "📋 Детали заказа:\n"
            "🛣 Маршрут: {from_} → {to}\n"
            "📏 Расстояние: {dist:.1f} км\n"
            "🚗 Автомобиль: {car}\n"
            "💶 Стоимость: €{price:.0f}\n"
            "📅 Дата/время: {dt}\n"
            "📞 Телефон: {phone}\n"
            "👥 Пассажиров: {passengers}\n\n"
            "Спасибо! Мы свяжемся с вами для подтверждения."
        ),
        "admin_order": (
            "🚖 НОВЫЙ ЗАКАЗ!\n\n"
            "🛣 Маршрут: {from_} → {to}\n"
            "📏 Расстояние: {dist:.1f} км\n"
            "🚗 Автомобиль: {car}\n"
            "💶 Стоимость: €{price:.0f}\n"
            "📅 Дата/время: {dt}\n"
            "📞 Телефон: {phone}\n"
            "👥 Пассажиров: {passengers}\n"
            "👤 Клиент: @{username}"
        ),
        "error_geocode": "❌ Адрес не найден. Введите более точный адрес:",
        "error_route": "❌ Не удалось рассчитать маршрут. Проверьте адреса и попробуйте снова.",
        "cancel": "Расчёт отменён. Напишите /start чтобы начать заново.",
    },
    "en": {
        "welcome": (
            "🚗 **Welcome to MySwissTransfer!**\n\n"
            "You can send your transfer request in free form, for example:\n"
            "💬 *«Zurich Airport — Zurich 29.12.26 13:10 for 2 people»*\n\n"
            "Or type /start for step-by-step entry."
        ),
        "ask_from": "📍 Enter pickup address (Point A):",
        "ask_to": "📍 Enter destination address (Point B):",
        "calculating": "⏳ Calculating route...",
        "distance": "📏 Distance: {:.1f} km\n\nChoose car class:",
        "ask_datetime": "📅 Enter the date and time of the trip\n(e.g.: 25.07.2025 14:30):",
        "ask_phone": "📞 Enter your phone number:",
        "order_summary": (
            "✅ Your order is accepted!\n\n"
            "📋 Order details:\n"
            "🛣 Route: {from_} → {to}\n"
            "📏 Distance: {dist:.1f} km\n"
            "🚗 Car: {car}\n"
            "💶 Price: €{price:.0f}\n"
            "📅 Date/time: {dt}\n"
            "📞 Phone: {phone}\n"
            "👥 Passengers: {passengers}\n\n"
            "Thank you! We will contact you to confirm."
        ),
        "admin_order": (
            "🚖 NEW ORDER!\n\n"
            "🛣 Route: {from_} → {to}\n"
            "📏 Distance: {dist:.1f} km\n"
            "🚗 Car: {car}\n"
            "💶 Price: €{price:.0f}\n"
            "📅 Date/time: {dt}\n"
            "📞 Phone: {phone}\n"
            "👥 Passengers: {passengers}\n"
            "👤 Client: @{username}"
        ),
        "error_geocode": "❌ Address not found. Please enter a more precise address:",
        "error_route": "❌ Could not calculate route. Check addresses and try again.",
        "cancel": "Calculation cancelled. Type /start to begin again.",
    },
}

CAR_NAMES = {
    "ru": {
        "bk": "Бизнес класс",
        "ek": "Эконом класс",
        "em": "Эконом минивен",
        "bm": "Бизнес минивен",
        "sv": "Кроссовер",
    },
    "en": {
        "bk": "Business class",
        "ek": "Economy class",
        "em": "Economy minivan",
        "bm": "Business minivan",
        "sv": "Crossover SUV",
    },
}


def calc_price(car_type: str, length: float) -> float:
    rates = {
        "bk": [
            (15, 82), (35, 3.55), (50, 3.37), (70, 3.22), (105, 3.12),
            (150, 3.0), (200, 2.9), (250, 2.81), (300, 2.78), (400, 2.74),
            (float("inf"), 2.7),
        ],
        "ek": [
            (15, 65), (35, 3.2), (50, 2.8), (70, 2.38), (105, 2.22),
            (150, 2.10), (200, 2.05), (250, 1.95), (300, 1.94), (400, 1.93),
            (float("inf"), 1.92),
        ],
        "em": [
            (15, 90), (35, 3.78), (50, 3.59), (70, 3.21), (105, 2.72),
            (150, 2.47), (200, 2.4), (250, 2.32), (300, 2.31), (400, 2.28),
            (float("inf"), 2.25),
        ],
        "bm": [
            (15, 105), (35, 4.52), (50, 4.16), (70, 4.08), (105, 4.0),
            (150, 3.84), (200, 3.69), (250, 3.6), (300, 3.57), (400, 3.5),
            (float("inf"), 3.45),
        ],
        "sv": [
            (15, 75), (35, 3.94), (50, 3.44), (70, 2.81), (105, 2.5),
            (150, 2.35), (200, 2.22), (250, 2.17), (300, 2.15), (400, 2.12),
            (float("inf"), 2.1),
        ],
    }
    tiers = rates[car_type]
    for i, (limit, rate) in enumerate(tiers):
        if length <= limit:
            if i == 0:
                return rate
            return length * rate
    return length * tiers[-1][1]


def geocode(address: str):
    url = "https://geocode.search.hereapi.com/v1/geocode"
    params = {"q": address, "apiKey": HERE_API_KEY, "limit": 1}
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if not items:
            return None
        pos = items[0]["position"]
        return pos["lat"], pos["lng"]
    except Exception as e:
        logger.error(f"Geocode error: {e}")
        return None


def get_distance(origin, destination):
    url = "https://router.hereapi.com/v8/routes"
    params = {
        "transportMode": "car",
        "origin": f"{origin[0]},{origin[1]}",
        "destination": f"{destination[0]},{destination[1]}",
        "return": "summary",
        "apiKey": HERE_API_KEY,
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        routes = resp.json().get("routes", [])
        if not routes:
            return None
        length = routes[0]["sections"][0]["summary"]["length"]
        return length / 1000
    except Exception as e:
        logger.error(f"Route error: {e}")
        return None


def parse_order_with_ai(text: str) -> dict:
    """Парсит свободный текст через OpenRouter LLM."""
    if not OPENROUTER_API_KEY:
        return {"is_order": False}
    
    prompt = f"""
    Проанализируй текст и определи, является ли он запросом на расчет трансфера или заказа автомобиля.
    Верни ТОЛЬКО валидный JSON без markdown-разметки (без ```json ... ```) со следующими полями:
    - "is_order": true, если клиент указал откуда и куда ехать, иначе false
    - "from": точка отправления (строка или null)
    - "to": точка назначения (строка или null)
    - "datetime": дата и время поездки если указаны (строка или null)
    - "passengers": количество пассажиров числом (если указано, иначе null)

    Текст клиента: "{text}"
    """
    
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": "openai/gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1
    }
    
    try:
        response = requests.post("https://openrouter.ai/api/v1/chat/completions", headers=headers, json=payload, timeout=10)
        res_json = response.json()
        content = res_json["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.strip("`")
            if content.startswith("json"):
                content = content[4:].strip()
        return json.loads(content)
    except Exception as e:
        logger.error(f"AI parse error: {e}")
        return {"is_order": False}


# ─── Handlers ────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    lang = "ru"
    context.user_data["lang"] = lang
    await update.message.reply_text(
        TEXTS[lang]["welcome"],
        parse_mode="Markdown"
    )
    return GET_FROM


async def handle_free_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text
    lang = context.user_data.get("lang", "ru")
    t = TEXTS[lang]

    # Пытаемся распознать заказ через ИИ
    parsed = parse_order_with_ai(text)

    if parsed.get("is_order") and parsed.get("from") and parsed.get("to"):
        # Если ИИ успешно распознал маршрут
        context.user_data["from"] = parsed["from"]
        context.user_data["to"] = parsed["to"]
        if parsed.get("datetime"):
            context.user_data["datetime"] = parsed["datetime"]
        if parsed.get("passengers"):
            context.user_data["passengers"] = parsed["passengers"]
        else:
            context.user_data["passengers"] = "Не указано"

        calc_msg = await update.message.reply_text(t["calculating"])

        from_coords = geocode(context.user_data["from"])
        if not from_coords:
            await calc_msg.delete()
            await update.message.reply_text(t["error_geocode"])
            return GET_FROM

        to_coords = geocode(context.user_data["to"])
        if not to_coords:
            await calc_msg.delete()
            await update.message.reply_text(t["error_geocode"])
            return GET_TO

        distance = get_distance(from_coords, to_coords)
        await calc_msg.delete()
        if distance is None:
            await update.message.reply_text(t["error_route"])
            return GET_TO

        context.user_data["distance"] = distance
        car_names = CAR_NAMES[lang]

        keyboard = []
        for car_id, car_name in car_names.items():
            price = calc_price(car_id, distance)
            keyboard.append(
                [InlineKeyboardButton(f"{car_name} — €{price:.0f}", callback_data=f"car_{car_id}")]
            )

        await update.message.reply_text(
            t["distance"].format(distance),
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return CHOOSE_CAR

    else:
        # Если текст не похож на заказ — выводим приветствие и инструкцию
        context.user_data["lang"] = lang
        await update.message.reply_text(
            TEXTS[lang]["welcome"],
            parse_mode="Markdown"
        )
        return GET_FROM


async def get_from(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lang = context.user_data.get("lang", "ru")
    text = update.message.text
    
    # Проверяем, не отправил ли пользователь сразу готовый запрос
    parsed = parse_order_with_ai(text)
    if parsed.get("is_order") and parsed.get("from") and parsed.get("to"):
        return await handle_free_text(update, context)

    context.user_data["from"] = text
    await update.message.reply_text(TEXTS[lang]["ask_to"])
    return GET_TO


async def get_to(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lang = context.user_data.get("lang", "ru")
    t = TEXTS[lang]
    context.user_data["to"] = update.message.text
    calc_msg = await update.message.reply_text(t["calculating"])

    from_coords = geocode(context.user_data["from"])
    if not from_coords:
        await calc_msg.delete()
        context.user_data.pop("from", None)
        await update.message.reply_text(t["error_geocode"])
        await update.message.reply_text(t["ask_from"])
        return GET_FROM

    to_coords = geocode(context.user_data["to"])
    if not to_coords:
        await calc_msg.delete()
        context.user_data.pop("to", None)
        await update.message.reply_text(t["error_geocode"])
        await update.message.reply_text(t["ask_to"])
        return GET_TO

    distance = get_distance(from_coords, to_coords)
    await calc_msg.delete()
    if distance is None:
        await update.message.reply_text(t["error_route"])
        return GET_TO

    context.user_data["distance"] = distance
    car_names = CAR_NAMES[lang]

    keyboard = []
    for car_id, car_name in car_names.items():
        price = calc_price(car_id, distance)
        keyboard.append(
            [InlineKeyboardButton(f"{car_name} — €{price:.0f}", callback_data=f"car_{car_id}")]
        )

    await update.message.reply_text(
        t["distance"].format(distance),
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return CHOOSE_CAR


async def choose_car(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    lang = context.user_data.get("lang", "ru")
    car_id = query.data.replace("car_", "")
    context.user_data["car_id"] = car_id
    context.user_data["car_name"] = CAR_NAMES[lang][car_id]
    context.user_data["price"] = calc_price(car_id, context.user_data["distance"])
    await query.edit_message_reply_markup(None)
    
    # Если дата/время уже были переданы через ИИ, пропускаем вопрос о дате
    if context.user_data.get("datetime"):
        await query.message.reply_text(TEXTS[lang]["ask_phone"])
        return GET_PHONE
    else:
        await query.message.reply_text(TEXTS[lang]["ask_datetime"])
        return GET_DATETIME


async def get_datetime(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lang = context.user_data.get("lang", "ru")
    context.user_data["datetime"] = update.message.text
    await update.message.reply_text(TEXTS[lang]["ask_phone"])
    return GET_PHONE


async def get_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lang = context.user_data.get("lang", "ru")
    t = TEXTS[lang]
    context.user_data["phone"] = update.message.text
    d = context.user_data

    passengers = d.get("passengers", "Не указано")
    dt = d.get("datetime", "Не указано")

    summary = t["order_summary"].format(
        from_=d["from"],
        to=d["to"],
        dist=d["distance"],
        car=d["car_name"],
        price=d["price"],
        dt=dt,
        phone=d["phone"],
        passengers=passengers,
    )
    await update.message.reply_text(summary)

    if ADMIN_ID:
        username = (
            update.effective_user.username
            or update.effective_user.first_name
            or "unknown"
        )
        admin_text = TEXTS["ru"]["admin_order"].format(
            from_=d["from"],
            to=d["to"],
            dist=d["distance"],
            car=d["car_name"],
            price=d["price"],
            dt=dt,
            phone=d["phone"],
            passengers=passengers,
            username=username,
        )
        try:
            await context.bot.send_message(chat_id=ADMIN_ID, text=admin_text)
        except Exception as e:
            logger.error(f"Failed to notify admin: {e}")

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    lang = context.user_data.get("lang", "ru")
    await update.message.reply_text(TEXTS[lang]["cancel"])
    return ConversationHandler.END


# ─── Keep-alive web server ────────────────────────────────────────────────────

class AliveHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"I am alive")

    def log_message(self, format, *args):
        pass


def run_web_server():
    server = HTTPServer(("0.0.0.0", 3000), AliveHandler)
    logger.info("Keep-alive server running on port 3000")
    server.serve_forever()


# ─── Entry point ─────────────────────────────────────────────────────────────

def main():
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set.")
    if not HERE_API_KEY:
        raise RuntimeError("HERE_API_KEY is not set.")

    threading.Thread(target=run_web_server, daemon=True).start()

    application = Application.builder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_free_text)
        ],
        states={
            GET_FROM:    [MessageHandler(filters.TEXT & ~filters.COMMAND, get_from)],
            GET_TO:      [MessageHandler(filters.TEXT & ~filters.COMMAND, get_to)],
            CHOOSE_CAR:  [CallbackQueryHandler(choose_car, pattern="^car_")],
            GET_DATETIME:[MessageHandler(filters.TEXT & ~filters.COMMAND, get_datetime)],
            GET_PHONE:   [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        allow_reentry=True,
    )

    application.add_handler(conv)
    logger.info("Transfer bot started. Polling for updates...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
