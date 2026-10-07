# -*- coding: utf-8 -*-
# VorzaRBX Admin Bot
# Панель управления магазином Robux.

import os
import json
import time
import logging
import traceback
from typing import Dict, Any, Optional

import requests
import telebot
from telebot import types
from flask import Flask, request

# ============ НАСТРОЙКИ ============
ADMIN_BOT_TOKEN = os.environ["ADMIN_BOT_TOKEN"]
ADMIN_USER_ID = int(os.environ["ADMIN_USER_ID"])
ADMIN_PASSWORD = os.environ["ADMIN_PASSWORD"]

SETTINGS_FILE = "settings.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("VorzaAdmin")

bot = telebot.TeleBot(ADMIN_BOT_TOKEN, parse_mode=None)
app = Flask(__name__)

# Кто авторизован (только ты)
authorized: Dict[int, bool] = {}

# Состояния диалогов
state: Dict[int, Dict[str, Any]] = {}

# Дефолтные настройки магазина
DEFAULT_SETTINGS = {
    "shop_bot_token": "",
    "shop_enabled": True,
    "rbxcrate_key": "",
    "rbxcrate_endpoint": "https://rbxcrate.com/api/orders/gamepass",
    "price_rub_per_100": 50,
    "min_robux": 100,
    "max_robux": 100000,
    "gross_divider": 0.7,
    "welcome_text": (
        "👋 Привет! Я VorzaRBX — бот для покупки Robux.\n\n"
        "⚡ Автовыдача 24/7\n"
        "🔒 Безопасно через Game Pass\n"
        "💬 Поддержка на связи\n\n"
        "Нажми /buy чтобы начать покупку."
    ),
}


# ============ РАБОТА С НАСТРОЙКАМИ ============
def load_settings() -> Dict[str, Any]:
    if not os.path.exists(SETTINGS_FILE):
        save_settings(DEFAULT_SETTINGS.copy())
        return DEFAULT_SETTINGS.copy()
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = DEFAULT_SETTINGS.copy()
        if isinstance(data, dict):
            merged.update(data)
        return merged
    except Exception:
        logger.error("settings.json повреждён:\n" + traceback.format_exc())
        return DEFAULT_SETTINGS.copy()


def save_settings(data: Dict[str, Any]) -> None:
    merged = DEFAULT_SETTINGS.copy()
    merged.update(data or {})
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)


# ============ КЛАВИАТУРЫ ============
def kbm_main():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("📊 Статус", "⚙️ Настройки")
    kb.add("📦 Заказы", "📈 Статистика")
    kb.add("❓ Помощь")
    return kb


def kbm_cancel():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("❌ Отмена")
    return kb


# ============ ПРОВЕРКА ДОСТУПА ============
def is_admin(chat_id: int) -> bool:
    return chat_id == ADMIN_USER_ID


def is_authorized(chat_id: int) -> bool:
    return authorized.get(chat_id, False)


def check_access(m) -> bool:
    """True — если можно продолжать."""
    if not is_admin(m.chat.id):
        bot.send_message(m.chat.id, "⛔ Доступ запрещён.")
        return False
    if not is_authorized(m.chat.id):
        bot.send_message(m.chat.id, "🔒 Сначала войди: /login <пароль>")
        return False
    return True


# ============ КОМАНДЫ ============
@bot.message_handler(commands=["start"])
def cmd_start(m):
    if not is_admin(m.chat.id):
        bot.send_message(m.chat.id, "⛔ Доступ запрещён.")
        return
    bot.send_message(m.chat.id,
                     "👋 Привет, админ!\n\n"
                     "Войди командой:\n"
                     "/login <пароль>")


@bot.message_handler(commands=["login"])
def cmd_login(m):
    if not is_admin(m.chat.id):
        bot.send_message(m.chat.id, "⛔ Доступ запрещён.")
        return

    parts = (m.text or "").split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(m.chat.id, "Формат: /login <пароль>")
        return

    password = parts[1].strip()
    if password != ADMIN_PASSWORD:
        bot.send_message(m.chat.id, "❌ Неверный пароль.")
        return

    authorized[m.chat.id] = True
    bot.send_message(m.chat.id,
                     "✅ Вход выполнен.\n\n"
                     "Панель управления VorzaRBX:",
                     reply_markup=kbm_main())


@bot.message_handler(commands=["logout"])
def cmd_logout(m):
    authorized.pop(m.chat.id, None)
    bot.send_message(m.chat.id, "🚪 Ты вышел.", reply_markup=types.ReplyKeyboardRemove())


@bot.message_handler(commands=["help"])
def cmd_help(m):
    if not check_access(m):
        return
    text = (
        "📖 Команды админки:\n\n"
        "🔧 Настройки:\n"
        "/set_shop_token <токен>      — токен магазин-бота\n"
        "/set_rbxcrate_key <ключ>     — RBXcrate API key\n"
        "/set_rbxcrate_endpoint <url> — endpoint RBXcrate\n"
        "/set_price <руб за 100 R$>   — цена\n"
        "/set_min_max <min> <max>     — лимиты Robux\n"
        "/set_welcome <текст>         — приветствие магазина\n\n"
        "📦 Управление:\n"
        "/on /off                     — вкл/выкл магазин\n"
        "/status                      — текущие настройки\n"
        "/orders                      — последние заказы\n"
        "/stats                       — статистика\n"
        "/reload                      — перечитать настройки\n"
        "/logout                      — выйти\n"
    )
    bot.send_message(m.chat.id, text)


# ============ НАСТРОЙКИ ============
@bot.message_handler(commands=["status"])
def cmd_status(m):
    if not check_access(m):
        return
    s = load_settings()
    key = s.get("rbxcrate_key", "")
    key_masked = (key[:8] + "..." + key[-4:]) if len(key) > 15 else "(не задан)"
    token = s.get("shop_bot_token", "")
    token_masked = (token[:10] + "...") if token else "(не задан)"

    text = (
        "⚙️ Текущие настройки:\n\n"
        f"🛒 Магазин: {'включён ✅' if s.get('shop_enabled') else 'выключен ❌'}\n"
        f"🤖 Токен магазина: {token_masked}\n"
        f"🔑 RBXcrate key: {key_masked}\n"
        f"🌐 Endpoint: {s.get('rbxcrate_endpoint')}\n"
        f"💰 Цена: {s.get('price_rub_per_100')} ₽ за 100 R$\n"
        f"📊 Лимиты: {s.get('min_robux')} – {s.get('max_robux')} R$\n"
        f"➗ Gross divider: {s.get('gross_divider')}\n"
    )
    bot.send_message(m.chat.id, text)


@bot.message_handler(commands=["set_shop_token"])
def cmd_set_shop_token(m):
    if not check_access(m):
        return
    parts = (m.text or "").split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(m.chat.id, "Формат: /set_shop_token <токен>")
        return
    s = load_settings()
    s["shop_bot_token"] = parts[1].strip()
    save_settings(s)
    bot.send_message(m.chat.id, "✅ Токен магазина сохранён.")


@bot.message_handler(commands=["set_rbxcrate_key"])
def cmd_set_rbxcrate_key(m):
    if not check_access(m):
        return
    parts = (m.text or "").split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(m.chat.id, "Формат: /set_rbxcrate_key <ключ>")
        return
    s = load_settings()
    s["rbxcrate_key"] = parts[1].strip()
    save_settings(s)
    bot.send_message(m.chat.id, "✅ RBXcrate API key сохранён.")


@bot.message_handler(commands=["set_rbxcrate_endpoint"])
def cmd_set_rbxcrate_endpoint(m):
    if not check_access(m):
        return
    parts = (m.text or "").split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(m.chat.id, "Формат: /set_rbxcrate_endpoint <url>")
        return
    url = parts[1].strip()
    if not url.startswith("http"):
        bot.send_message(m.chat.id, "❌ URL должен начинаться с http:// или https://")
        return
    s = load_settings()
    s["rbxcrate_endpoint"] = url
    save_settings(s)
    bot.send_message(m.chat.id, "✅ Endpoint сохранён.")


@bot.message_handler(commands=["set_price"])
def cmd_set_price(m):
    if not check_access(m):
        return
    parts = (m.text or "").split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(m.chat.id, "Формат: /set_price <руб за 100 R$>\nПример: /set_price 50")
        return
    try:
        price = float(parts[1].replace(",", "."))
    except Exception:
        bot.send_message(m.chat.id, "❌ Нужно число.")
        return
    s = load_settings()
    s["price_rub_per_100"] = price
    save_settings(s)
    bot.send_message(m.chat.id, f"✅ Цена сохранена: {price} ₽ за 100 R$")


@bot.message_handler(commands=["set_min_max"])
def cmd_set_min_max(m):
    if not check_access(m):
        return
    parts = (m.text or "").split()
    if len(parts) < 3:
        bot.send_message(m.chat.id, "Формат: /set_min_max <min> <max>\nПример: /set_min_max 100 100000")
        return
    try:
        mn, mx = int(parts[1]), int(parts[2])
    except Exception:
        bot.send_message(m.chat.id, "❌ Нужны два целых числа.")
        return
    if mn >= mx:
        bot.send_message(m.chat.id, "❌ min должен быть меньше max.")
        return
    s = load_settings()
    s["min_robux"] = mn
    s["max_robux"] = mx
    save_settings(s)
    bot.send_message(m.chat.id, f"✅ Лимиты: {mn} – {mx} R$")


@bot.message_handler(commands=["set_welcome"])
def cmd_set_welcome(m):
    if not check_access(m):
        return
    parts = (m.text or "").split(maxsplit=1)
    if len(parts) < 2:
        bot.send_message(m.chat.id, "Формат: /set_welcome <текст>")
        return
    s = load_settings()
    s["welcome_text"] = parts[1].strip()
    save_settings(s)
    bot.send_message(m.chat.id, "✅ Приветствие магазина сохранено.")


# ============ УПРАВЛЕНИЕ ============
@bot.message_handler(commands=["on"])
def cmd_on(m):
    if not check_access(m):
        return
    s = load_settings()
    s["shop_enabled"] = True
    save_settings(s)
    bot.send_message(m.chat.id, "✅ Магазин включён.")


@bot.message_handler(commands=["off"])
def cmd_off(m):
    if not check_access(m):
        return
    s = load_settings()
    s["shop_enabled"] = False
    save_settings(s)
    bot.send_message(m.chat.id, "⛔ Магазин выключен.")


@bot.message_handler(commands=["reload"])
def cmd_reload(m):
    if not check_access(m):
        return
    load_settings()
    bot.send_message(m.chat.id, "🔄 Настройки перечитаны.")


@bot.message_handler(commands=["orders"])
def cmd_orders(m):
    if not check_access(m):
        return
    # Пока нет интеграции с магазином — заглушка
    bot.send_message(m.chat.id, "📦 Заказов пока нет. (Функция появится после подключения магазина.)")


@bot.message_handler(commands=["stats"])
def cmd_stats(m):
    if not check_access(m):
        return
    bot.send_message(m.chat.id, "📈 Статистика появится после подключения магазина.")


# ============ КНОПКИ ============
@bot.message_handler(content_types=["text"])
def on_text(m):
    if not is_admin(m.chat.id):
        return
    text = (m.text or "").strip()

    if text == "❌ Отмена":
        state.pop(m.chat.id, None)
        bot.send_message(m.chat.id, "Отменено.", reply_markup=kbm_main() if is_authorized(m.chat.id) else types.ReplyKeyboardRemove())
        return

    if text == "📊 Статус":
        cmd_status(m)
        return
    if text == "📦 Заказы":
        cmd_orders(m)
        return
    if text == "📈 Статистика":
        cmd_stats(m)
        return
    if text == "❓ Помощь":
        cmd_help(m)
        return
    if text == "⚙️ Настройки":
        if not check_access(m):
            return
        bot.send_message(m.chat.id,
                         "⚙️ Настройки меняются командами:\n"
                         "/set_shop_token, /set_rbxcrate_key, /set_rbxcrate_endpoint,\n"
                         "/set_price, /set_min_max, /set_welcome\n\n"
                         "Полный список — /help")
        return

    if not is_authorized(m.chat.id):
        bot.send_message(m.chat.id, "🔒 Войди: /login <пароль>")


# ============ FLASK / WEBHOOK ============
@app.route("/", methods=["GET"])
def index():
    return "VorzaRBX Admin bot is running", 200


@app.route(f"/webhook/{ADMIN_BOT_TOKEN}", methods=["POST"])
def webhook():
    if request.headers.get("content-type") == "application/json":
        json_str = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(json_str)
        bot.process_new_updates([update])
    return "ok", 200


def set_webhook():
    url = os.environ.get("PUBLIC_URL", "").rstrip("/")
    if not url:
        logger.warning("PUBLIC_URL не задан — вебхук не установлен.")
        return
    full = f"{url}/webhook/{ADMIN_BOT_TOKEN}"
    try:
        r = requests.get(f"https://api.telegram.org/bot{ADMIN_BOT_TOKEN}/setWebhook",
                         params={"url": full}, timeout=20)
        logger.info("setWebhook: %s %s", r.status_code, r.text[:300])
    except Exception:
        logger.error(traceback.format_exc())


if __name__ == "__main__":
    set_webhook()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
