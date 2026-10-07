# -*- coding: utf-8 -*-
# AutoRobux Telegram Bot (test version)
# Логика перенесена из плагина Cardinal.

import os
import re
import json
import math
import time
import logging
import traceback
import threading
from typing import Dict, Any, Optional, Tuple, List

import requests
import telebot
from telebot import types
from flask import Flask, request

# ============ НАСТРОЙКИ (тест) ============
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8902190310:AAEOCeyrXEd6w9Ri0vvDUZjaSLfocFDvy9Y")
RBXCRATE_KEY = os.environ.get("RBXCRATE_KEY", "PltoTMg09vEb6PEmEnVTs4CmIYshXTlsaoJVgOkPM1Qcalo4eDqLT6Ayo8smLyylFVNw69C7gdCOoB0X")

RBXCRATE_GAMEPASS_ENDPOINT = "https://rbxcrate.com/api/orders/gamepass"
RBXCRATE_INFO_ENDPOINT = "https://rbxcrate.com/api/orders/info"

GROSS_DIVIDER = 0.7
MIN_ROBUX = 1
MAX_ROBUX = 100000

# Тестовая цена: 1 Robux = 0.5 рубля (потом поменяешь)
PRICE_RUB_PER_ROBUX = 0.5

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("AutoRobuxBot")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)
app = Flask(__name__)

# Состояния диалогов: chat_id -> dict
state: Dict[int, Dict[str, Any]] = {}
orders: Dict[str, Dict[str, Any]] = {}

PLACE_INSTRUCTION = """Инструкция (ПК):
1. Открой roblox.com → Discover → нужная игра
2. Посмотри URL: roblox.com/games/123456789/Name
3. Число после /games/ — это Place ID

Инструкция (телефон):
1. Открой roblox.com в браузере (не в приложении)
2. Discover → выбери игру
3. Нажми на адресную строку — увидишь .../games/123456789/
"""

PASS_INSTRUCTION = """Как создать Game Pass:
1. Открой Creator Dashboard на roblox.com (в браузере)
2. Выбери свою игру
3. Monetization → Passes → Create a Pass
4. Задай имя, описание, иконку
5. Открой Pass → Sales → поставь цену → Save Changes
"""

# ============ Roblox API ============
def roblox_find_user(username: str) -> Optional[Dict[str, Any]]:
    r = requests.post(
        "https://users.roblox.com/v1/usernames/users",
        json={"usernames": [username], "excludeBannedUsers": True},
        timeout=20
    )
    r.raise_for_status()
    users = (r.json() or {}).get("data") or []
    return users[0] if users else None


def roblox_get_user_places(user_id: int) -> List[Dict[str, Any]]:
    r = requests.get(
        f"https://games.roblox.com/v2/users/{int(user_id)}/games",
        params={"accessFilter": "Public", "sortOrder": "Asc", "limit": 50},
        timeout=20
    )
    r.raise_for_status()
    games = (r.json() or {}).get("data") or []
    places = []
    for g in games:
        root = g.get("rootPlace") or {}
        place_id = root.get("id") or g.get("rootPlaceId")
        if not place_id:
            continue
        places.append({
            "name": g.get("name") or f"Place {place_id}",
            "place_id": int(place_id),
        })
    return places


def roblox_get_gamepass_details(gamepass_id: int) -> Optional[Dict[str, Any]]:
    try:
        r = requests.get(
            f"https://apis.roblox.com/game-passes/v1/game-passes/{int(gamepass_id)}/product-info",
            timeout=20
        )
        if r.ok:
            return r.json()
    except Exception:
        logger.warning("gamepass details failed:\n" + traceback.format_exc())
    return None


# ============ Валидация / парсинг ============
def validate_username(text: str) -> Optional[str]:
    u = str(text or "").strip().lstrip("@").replace(" ", "")
    if re.fullmatch(r"[A-Za-z0-9_]{3,20}", u):
        return u
    return None


def parse_place_id(text: str) -> Optional[int]:
    t = str(text or "").strip()
    m = re.search(r"/games/(\d+)", t, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(\d{4,20})\b", t)
    if m:
        return int(m.group(1))
    return None


def parse_gamepass_id(text: str) -> Optional[int]:
    t = str(text or "").strip()
    for p in (r"/game-pass/(\d+)", r"/passes/(\d+)", r"[?&]id=(\d+)"):
        m = re.search(p, t, re.I)
        if m:
            return int(m.group(1))
    m = re.search(r"\b(\d{4,20})\b", t)
    if m:
        return int(m.group(1))
    return None


def calc_gross(net: int) -> int:
    return int(math.ceil(int(net) / GROSS_DIVIDER))


# ============ RBXcrate ============
def rbxcrate_headers() -> Dict[str, str]:
    return {
        "api-key": RBXCRATE_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def rbxcrate_create_order(order_id: str, username: str, gross: int,
                          place_id: int, gamepass_id: Optional[int]) -> Tuple[bool, str]:
    payload = {
        "robloxUsername": username,
        "orderId": str(order_id),
        "robuxAmount": int(gross),
        "placeId": int(place_id),
        "isPreOrder": True,
        "checkOwnership": True,
    }
    if gamepass_id:
        payload["gamePassId"] = int(gamepass_id)
    try:
        r = requests.post(RBXCRATE_GAMEPASS_ENDPOINT, json=payload,
                          headers=rbxcrate_headers(), timeout=30)
        try:
            data = r.json()
        except Exception:
            data = {"raw": r.text}
        text = json.dumps(data, ensure_ascii=False)
        if not r.ok:
            return False, f"HTTP {r.status_code}: {text}"
        if isinstance(data, dict) and data.get("success") is False:
            return False, text
        return True, text
    except Exception as e:
        return False, f"request failed: {e}"


def rbxcrate_get_info(order_id: str) -> Tuple[bool, Dict[str, Any], str]:
    try:
        r = requests.post(RBXCRATE_INFO_ENDPOINT, json={"orderId": str(order_id)},
                          headers=rbxcrate_headers(), timeout=30)
        try:
            data = r.json()
        except Exception:
            data = {"raw": r.text}
        if not r.ok:
            return False, {}, f"HTTP {r.status_code}: {data}"
        if not isinstance(data, dict):
            return False, {}, f"unexpected: {data!r}"
        return True, data, ""
    except Exception as e:
        return False, {}, f"request failed: {e}"


# ============ Сообщения ============
def msg_start():
    return ("👋 Привет! Я бот для покупки Robux (тестовый режим).\n\n"
            "Команды:\n"
            "/buy — купить Robux\n"
            "/cancel — отменить заказ\n"
            "/help — помощь")


def msg_help():
    return ("Как купить:\n"
            "1. /buy\n"
            "2. Напиши, сколько Robux хочешь\n"
            "3. Пришли свой Roblox username\n"
            "4. Выбери Place (или пришли Place ID)\n"
            "5. Создай Game Pass на указанную сумму и пришли ссылку/ID\n"
            "6. Нажми «Я оплатил» (в тесте — просто кнопка)\n"
            "7. Бот отправит заказ и проследит за статусом\n\n"
            "Написать: !pass — как создать Game Pass\n"
            "!place — как найти Place ID")


def kbm_main():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("🛒 Купить Robux", "❓ Помощь")
    return kb


def kbm_cancel():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.add("❌ Отмена")
    return kb


def kbi_paid(order_id: str):
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton("✅ Я оплатил", callback_data=f"paid:{order_id}"))
    return kb


# ============ Хендлеры Telegram ============
@bot.message_handler(commands=["start"])
def cmd_start(m):
    state.pop(m.chat.id, None)
    bot.send_message(m.chat.id, msg_start(), reply_markup=kbm_main())


@bot.message_handler(commands=["help"])
def cmd_help(m):
    bot.send_message(m.chat.id, msg_help(), reply_markup=kbm_main())


@bot.message_handler(commands=["buy"])
def cmd_buy(m):
    state[m.chat.id] = {"step": "await_amount", "created_ts": time.time()}
    bot.send_message(m.chat.id, "Сколько Robux хочешь купить?\nНапиши число, например: 500",
                     reply_markup=kbm_cancel())


@bot.message_handler(commands=["cancel"])
def cmd_cancel(m):
    state.pop(m.chat.id, None)
    bot.send_message(m.chat.id, "❌ Отменено.", reply_markup=kbm_main())


@bot.callback_query_handler(func=lambda c: str(c.data or "").startswith("paid:"))
def cb_paid(call):
    order_id = call.data.split(":", 1)[1]
    row = orders.get(order_id)
    if not row:
        bot.answer_callback_query(call.id, "Заказ не найден")
        return
    bot.answer_callback_query(call.id, "Проверяю оплату...")

    st = state.get(call.message.chat.id)
    if not st or st.get("order_id") != order_id:
        bot.send_message(call.message.chat.id, "Сессия устарела, начни заново /buy")
        return

    # В тесте оплата считается подтверждённой сразу
    ok, resp = rbxcrate_create_order(
        order_id=order_id,
        username=row["roblox_username"],
        gross=row["gross_robux"],
        place_id=row["place_id"],
        gamepass_id=row.get("gamepass_id"),
    )
    row["rbxcrate_response"] = resp
    if ok:
        row["status"] = "PROCESSING"
        bot.send_message(call.message.chat.id,
                         "✅ Оплата подтверждена.\nЗаказ отправлен в обработку.\n\n"
                         f"Roblox: {row['roblox_username']}\n"
                         f"Получишь: {row['net_robux']} Robux\n\n"
                         "⏳ Robux придут в течение 3–5 дней (системная задержка Roblox).",
                         reply_markup=kbm_main())
        threading.Thread(target=poll_order, args=(call.message.chat.id, order_id), daemon=True).start()
    else:
        row["status"] = "WAITING_GAMEPASS_LINK"
        st["step"] = "await_gamepass"
        bot.send_message(call.message.chat.id,
                         "⚠️ Не удалось отправить заказ.\nОтвет сервиса:\n" + resp[:500] + "\n\n"
                         "Проверь Game Pass и пришли ссылку ещё раз.",
                         reply_markup=kbm_cancel())


@bot.message_handler(content_types=["text"])
def on_text(m):
    chat_id = m.chat.id
    text = (m.text or "").strip()
    low = text.lower()

    if text == "❌ Отмена" or low == "/cancel":
        state.pop(chat_id, None)
        bot.send_message(chat_id, "❌ Отменено.", reply_markup=kbm_main())
        return

    if low in ("!pass", "!пасс", "пасс"):
        bot.send_message(chat_id, PASS_INSTRUCTION)
        return

    if low in ("!place", "!плейс", "плейс"):
        bot.send_message(chat_id, PLACE_INSTRUCTION)
        return

    st = state.get(chat_id)
    if not st:
        if text == "🛒 Купить Robux":
            cmd_buy(m)
            return
        if text == "❓ Помощь":
            cmd_help(m)
            return
        bot.send_message(chat_id, "Напиши /buy чтобы начать, или /help.", reply_markup=kbm_main())
        return

    step = st.get("step")

    if step == "await_amount":
        try:
            n = int(text)
        except Exception:
            bot.send_message(chat_id, "Напиши целое число, например 500", reply_markup=kbm_cancel())
            return
        if n < MIN_ROBUX or n > MAX_ROBUX:
            bot.send_message(chat_id, f"Число должно быть от {MIN_ROBUX} до {MAX_ROBUX}",
                             reply_markup=kbm_cancel())
            return
        st["net_robux"] = n
        st["gross_robux"] = calc_gross(n)
        st["price_rub"] = round(n * PRICE_RUB_PER_ROBUX, 2)
        st["step"] = "await_username"
        bot.send_message(chat_id,
                         f"Ок, {n} Robux.\n"
                         f"Создать Game Pass нужно на {st['gross_robux']} Robux.\n"
                         f"Цена (тест): {st['price_rub']} ₽\n\n"
                         "Пришли свой Roblox username (например Builderman):",
                         reply_markup=kbm_cancel())
        return

    if step == "await_username":
        u = validate_username(text)
        if not u:
            bot.send_message(chat_id, "❌ Неверный формат. Только латиница, цифры, _, 3–20 символов.",
                             reply_markup=kbm_cancel())
            return
        try:
            user = roblox_find_user(u)
        except Exception:
            logger.error(traceback.format_exc())
            bot.send_message(chat_id, "⚠️ Не удалось проверить Roblox. Попробуй ещё раз.")
            return
        if not user:
            bot.send_message(chat_id, "❌ Roblox-аккаунт не найден. Попробуй другой ник.",
                             reply_markup=kbm_cancel())
            return
        st["roblox_username"] = user.get("name") or u
        st["roblox_user_id"] = int(user.get("id"))
        try:
            places = roblox_get_user_places(st["roblox_user_id"])
        except Exception:
            logger.error(traceback.format_exc())
            places = []
        if not places:
            st["step"] = "await_place_manual"
            bot.send_message(chat_id,
                             f"✅ Аккаунт найден: {st['roblox_username']} (ID {st['roblox_user_id']}).\n\n"
                             "⚠️ Не удалось найти публичные игры. Пришли Place ID вручную.\n"
                             "Как найти — напиши: !place",
                             reply_markup=kbm_cancel())
            return
        st["places"] = places
        st["step"] = "await_place"
        lines = [f"✅ Аккаунт: {st['roblox_username']} (ID {st['roblox_user_id']})",
                 "", "Выбери Place (номер):", ""]
        for i, p in enumerate(places[:10], 1):
            lines.append(f"{i}) {p['name']}  (Place ID {p['place_id']})")
        lines += ["", "Или пришли Place ID вручную.", "Как найти — !place"]
        bot.send_message(chat_id, "\n".join(lines), reply_markup=kbm_cancel())
        return

    if step == "await_place":
        places = st.get("places") or []
        chosen = None
        if text.isdigit() and 1 <= int(text) <= min(len(places), 10):
            chosen = places[int(text) - 1]
        else:
            pid = parse_place_id(text)
            if pid:
                for p in places:
                    if p["place_id"] == pid:
                        chosen = p
                        break
                if not chosen:
                    chosen = {"name": "manual", "place_id": pid}
        if not chosen:
            bot.send_message(chat_id, "❌ Пришли номер из списка или Place ID.", reply_markup=kbm_cancel())
            return
        st["place_id"] = chosen["place_id"]
        st["place_name"] = chosen["name"]
        st["step"] = "await_gamepass"
        bot.send_message(chat_id,
                         f"✅ Place: {chosen['name']} (ID {chosen['place_id']})\n\n"
                         f"Теперь создай Game Pass на {st['gross_robux']} Robux.\n"
                         "Как создать — напиши: !pass\n\n"
                         "Когда создашь — пришли ссылку/ID Game Pass.")
        return

    if step == "await_place_manual":
        pid = parse_place_id(text)
        if not pid:
            bot.send_message(chat_id, "❌ Пришли числовой Place ID.", reply_markup=kbm_cancel())
            return
        st["place_id"] = pid
        st["place_name"] = "manual"
        st["step"] = "await_gamepass"
        bot.send_message(chat_id,
                         f"✅ Place ID: {pid}\n\n"
                         f"Создай Game Pass на {st['gross_robux']} Robux (как — !pass).\n"
                         "Затем пришли ссылку/ID Game Pass.")
        return

    if step == "await_gamepass":
        gp_id = parse_gamepass_id(text)
        if not gp_id:
            bot.send_message(chat_id, "❌ Не понял. Пришли ссылку на Game Pass или его ID.",
                             reply_markup=kbm_cancel())
            return
        # Проверка цены
        details = roblox_get_gamepass_details(gp_id)
        if details:
            price = details.get("price") or details.get("Price") or details.get("priceInRobux")
            if price is not None and int(price) != int(st["gross_robux"]):
                bot.send_message(chat_id,
                                 f"❌ Цена Game Pass не совпадает.\n"
                                 f"Нужно: {st['gross_robux']}\nСейчас: {price}\n\n"
                                 "Измени цену и пришли ссылку снова.",
                                 reply_markup=kbm_cancel())
                return
        st["gamepass_id"] = gp_id

        order_id = f"T{int(time.time())}{chat_id % 10000}"
        st["order_id"] = order_id
        orders[order_id] = {
            "order_id": order_id,
            "chat_id": chat_id,
            "net_robux": st["net_robux"],
            "gross_robux": st["gross_robux"],
            "price_rub": st["price_rub"],
            "roblox_username": st["roblox_username"],
            "roblox_user_id": st["roblox_user_id"],
            "place_id": st["place_id"],
            "place_name": st["place_name"],
            "gamepass_id": gp_id,
            "status": "WAITING_PAYMENT",
            "created_ts": time.time(),
        }
        st["step"] = "await_payment"
        bot.send_message(chat_id,
                         "📋 Заказ собран:\n\n"
                         f"ID: {order_id}\n"
                         f"Roblox: {st['roblox_username']}\n"
                         f"Получишь: {st['net_robux']} Robux\n"
                         f"К оплате: {st['price_rub']} ₽\n\n"
                         "💳 (Тестовый режим) Оплата не настоящая — просто нажми кнопку:",
                         reply_markup=kbi_paid(order_id))
        return

    bot.send_message(chat_id, "Не понял. /cancel чтобы сбросить.", reply_markup=kbm_cancel())


# ============ Поллинг статуса RBXcrate ============
def poll_order(chat_id: int, order_id: str, max_minutes: int = 180):
    deadline = time.time() + max_minutes * 60
    while time.time() < deadline:
        row = orders.get(order_id)
        if not row:
            return
        if row.get("status") in ("COMPLETED", "FAILED", "REFUNDED"):
            return
        ok, info, err = rbxcrate_get_info(order_id)
        if ok:
            status = str(info.get("status") or "").lower()
            row["rbxcrate_status"] = status
            if status in ("completed", "complete", "done", "success", "succeeded", "paid", "finished", "delivered", "approved"):
                row["status"] = "COMPLETED"
                bot.send_message(chat_id,
                                 "✅ Заказ выполнен!\n"
                                 "Robux придут в течение ~120 часов (системная задержка Roblox).\n\n"
                                 "Спасибо за покупку!")
                return
            if status in ("error", "failed", "fail", "cancelled", "canceled", "refunded"):
                row["status"] = "FAILED"
                bot.send_message(chat_id,
                                 "⚠️ Заказ не выполнен автоматически. Ожидай связи с оператором.")
                return
        time.sleep(45)


# ============ Flask / webhook ============
@app.route("/", methods=["GET"])
def index():
    return "AutoRobux bot is running", 200


@app.route(f"/webhook/{BOT_TOKEN}", methods=["POST"])
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
    full = f"{url}/webhook/{BOT_TOKEN}"
    try:
        r = requests.get(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook",
                         params={"url": full}, timeout=20)
        logger.info("setWebhook: %s %s", r.status_code, r.text[:300])
    except Exception:
        logger.error(traceback.format_exc())


if __name__ == "__main__":
    set_webhook()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
