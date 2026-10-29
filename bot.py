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
ADMIN_ID_RAW = os.environ.get("ADMIN_TELEGRAM_ID", "0").strip()
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
            "💬 *«Zurich Airport — Grindelwald 29.12.26 13:10 для 2 человек»*\n\n"
            "Или напишите точку отправления для пошагового расчёта."
        ),
        "ask_from": "📍 Введите адрес отправления (Точка А):",
        "ask_to": "📍 Введите адрес прибытия (Точка Б):",
        "calculating": "⏳ Рассчитываю маршрут...",
        "distance": "📏 Расстояние: {:.1f} км\n\nВыберите класс автомобиля:",
        "ask_datetime": "📅 Введите дату и время поездки\n(например: 25.07.2025 14:30):",
        "ask_phone": "📞 Введите ваш номер телефона:",
        "order_summary": (
            "🎉 **Ваш заказ успешно оформлен!**\n\n"
            "📋 **Детали заказа:**\n"
            "🛣 Маршрут: {from_} → {to}\n"
            "📏 Расстояние: {dist:.1f} км\n"
            "🚗 Автомобиль: {car}\n"
            "💶 Стоимость: €{price:.0f}\n"
            "📅 Дата/время: {dt}\n"
            "📞 Телефон: {phone}\n"
            "👥 Пассажиров: {passengers}\n\n"
            "Мы свяжемся с вами в ближайшее время для подтверждения!"
        ),
        "admin_order": (
            "🚖 **НОВЫЙ ЗАКАЗ С БОТА!**\n\n"
            "🛣 **Маршрут:** {from_} → {to}\n"
            "📏 **Расстояние:** {dist:.1f} км\n"
            "🚗 **Класс авто:** {car}\n"
            "💶 **Сумма:** €{price:.0f}\n"
            "📅 **Дата/время:** {dt}\n"
            "📞 **Телефон:** {phone}\n"
            "👥 **Пассажиров:** {passengers}\n"
            "👤 **Клиент:** @{username}"
        ),
        "error_geocode": "❌ Адрес не найден. Введите более точный адрес (на английском/немецком):",
        "error_route": "❌ Не удалось рассчитать маршрут. Проверьте адреса и попробуйте снова.",
        "cancel": "Расчёт отменён. Напишите любой текст, чтобы начать заново.",
    },
    "en": {
        "welcome": (
            "🚗 **Welcome to MySwissTransfer!**\n\n"
            "You can send your transfer request in free form, for example:\n"
            "💬 *«Zurich Airport — Zurich 29.12.26 13:10 for 2 people»*\n\n"
            "Or type pickup address to start step-by-step calculation."
        ),
        "ask_from": "📍 Enter pickup address (Point A):",
        "ask_to": "📍 Enter destination address (Point B):",
        "calculating": "⏳ Calculating route...",
        "distance": "📏 Distance: {:.1f} km\n\nChoose car class:",
        "ask_datetime": "📅 Enter the date and time of the trip\n(e.g.: 25.07.2025 14:30):",
        "ask_phone": "📞 Enter your phone number:",
        "order_summary": (
            "🎉 **Your order has been placed!**\n\n"
            "📋 **Order details:**\n"
            "🛣 Route: {from_} → {to}\n"
            "📏 Distance: {dist:.1f} km\n"
            "🚗 Car: {car}\n"
            "💶 Price: €{price:.0f}\n"
            "📅 Date/time: {dt}\n"
            "📞 Phone: {phone}\n"
            "👥 Passengers: {passengers}\n\n"
            "We will contact you shortly to confirm!"
        ),
        "admin_order": (
            "🚖 **NEW ORDER FROM BOT!**\n\n"
            "🛣 **Route:** {from_} → {to}\n"
            "📏 **Distance:** {dist:.1f} km\n"
            "🚗 **Car class:** {car}\n"
            "💶 **Price:** €{price:.0f}\n"
            "📅 **Date/time:** {dt}\n"
            "📞 **Phone:** {phone}\n"
            "👥 **Passengers:** {passengers}\n"
            "👤 **Client:** @{username}"
        ),
        "error_geocode": "❌ Address not found. Please enter a more precise address:",
        "error_route": "❌ Could not calculate route. Check addresses and try again.",
        "cancel": "Calculation cancelled. Type anything to start over.",
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
    url = "
