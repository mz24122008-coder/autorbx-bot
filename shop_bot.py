# -*- coding: utf-8 -*-
# VorzaRBX Shop Bot — магазин для покупателей

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

logger = logging.getLogger("VorzaShop")

SHOP_BOT_TOKEN = os.environ["SHOP_BOT_TOKEN"]
SETTINGS_FILE = "settings.json"

PLACE_INSTRUCTION = """Инструкция (ПК):
1. Открой roblox.com -> Discover -> нужная игра
2. Посмотри URL: roblox.com/games/123456789/Name
3. Число после /games/ — это Place ID

Инструкция (телефон):
1. Открой roblox.com в браузере (не в приложении)
2. Discover -> выбери игру
3. Нажми на адресную строку — увидишь .../games/123456789/
"""

PASS_INSTRUCTION = """Как создать Game Pass:
1. Открой Creator Dashboard на roblox.com (в браузере)
2. Выбери свою игру
3. Monetization -> Passes -> Create a Pass
4. Задай имя, описание, иконку
5. Открой Pass -> Sales -> поставь цену -> Save Changes
"""


# ============ НАСТРОЙКИ ============
def load_settings() -> Dict[str, Any]:
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        logger.error("settings.json не читается:\n" + traceback.format_exc())
        return {}


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


def calc_gross(net: int, divider: float = 0.7) -> int:
    return int(math.ceil(int(net) / divider))


# ============ RBXcrate ============
def rbxcrate_headers(settings: Dict[str, Any]) -> Dict[str, str]:
    return {
        "api-key": settings.get("rbxcrate_key", ""),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def rbxcrate_create_order(settings: Dict[str, Any], order_id: str, username: str,
                          gross: int, place_id: int, gamepass_id: Optional[int]) -> Tuple[bool, str]:
    endpoint = settings.get("rbxcrate_endpoint") or "https://rbxcrate.com/api/orders/gamepass"
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
        r = requests.post(endpoint, json=payload, headers=rbxcrate_headers(settings), timeout=30)
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


# ============ МАГАЗИН ============
class ShopBot:
    def __init__(self, token: str):
        self.bot = telebot.TeleBot(token, parse_mode=None)
        self.state: Dict[int, Dict[str, Any]] = {}
        self.orders: Dict[str, Dict[str, Any]] = {}
        self._register_handlers()

    def _register_handlers(self):
        bot = self.bot

        @bot.message_handler(commands=["start"])
        def cmd_start(m):
            s = load_settings()
            if not s.get("shop_enabled", True):
                bot.send_message(m.chat.id, "⛔ Магазин временно выключен.")
                return
            self.state.pop(m.chat.id, None)
            welcome = s.get("welcome_text") or "👋 Привет!"
            kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
            kb.add("🛒 Купить Robux", "❓ Помощь")
            bot.send_message(m.chat.id, welcome, reply_markup=kb)

        @bot.message_handler(commands=["help"])
        def cmd_help(m):
            bot.send_message(m.chat.id,
                             "Как купить:\n"
                             "1. /buy\n"
                             "2. Напиши, сколько Robux хочешь\n"
                             "3. Пришли свой Roblox username\n"
                             "4. Выбери Place\n"
                             "5. Создай Game Pass на указанную сумму\n"
                             "6. Нажми «Я оплатил»\n\n"
                             "!pass — как создать Game Pass\n"
                             "!place — как найти Place ID")

        @bot.message_handler(commands=["buy"])
        def cmd_buy(m):
            s = load_settings()
            if not s.get("shop_enabled", True):
                bot.send_message(m.chat.id, "⛔ Магазин временно выключен.")
                return
            self.state[m.chat.id] = {"step": "await_amount", "created_ts": time.time()}
            mn = s.get("min_robux", 100)
            mx = s.get("max_robux", 100000)
            kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
            kb.add("❌ Отмена")
            bot.send_message(m.chat.id, f"Сколько Robux хочешь купить?\nОт {mn} до {mx}",
                             reply_markup=kb)

        @bot.message_handler(commands=["cancel"])
        def cmd_cancel(m):
            self.state.pop(m.chat.id, None)
            bot.send_message(m.chat.id, "❌ Отменено.")

        @bot.callback_query_handler(func=lambda c: str(c.data or "").startswith("paid:"))
        def cb_paid(call):
            order_id = call.data.split(":", 1)[1]
            row = self.orders.get(order_id)
            if not row:
                bot.answer_callback_query(call.id, "Заказ не найден")
                return
            bot.answer_callback_query(call.id, "Отправляю...")

            s = load_settings()
            ok, resp = rbxcrate_create_order(
                settings=s,
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
                                 "⏳ Robux придут в течение 3–5 дней.")
            else:
                row["status"] = "WAITING_GAMEPASS_LINK"
                self.state[call.message.chat.id]["step"] = "await_gamepass"
                bot.send_message(call.message.chat.id,
                                 "⚠️ Ошибка отправки:\n" + resp[:500])

        @bot.message_handler(content_types=["text"])
        def on_text(m):
            chat_id = m.chat.id
            text = (m.text or "").strip()
            low = text.lower()

            if text == "❌ Отмена" or low == "/cancel":
                self.state.pop(chat_id, None)
                bot.send_message(chat_id, "❌ Отменено.")
                return

            if low in ("!pass", "пасс"):
                bot.send_message(chat_id, PASS_INSTRUCTION)
                return
            if low in ("!place", "плейс"):
                bot.send_message(chat_id, PLACE_INSTRUCTION)
                return

            st = self.state.get(chat_id)
            if not st:
                if text == "🛒 Купить Robux":
                    cmd_buy(m)
                    return
                if text == "❓ Помощь":
                    cmd_help(m)
                    return
                bot.send_message(chat_id, "Напиши /buy чтобы начать.")
                return

            s = load_settings()
            step = st.get("step")

            if step == "await_amount":
                try:
                    n = int(text)
                except Exception:
                    bot.send_message(chat_id, "Напиши целое число.")
                    return
                mn = s.get("min_robux", 100)
                mx = s.get("max_robux", 100000)
                if n < mn or n > mx:
                    bot.send_message(chat_id, f"Число от {mn} до {mx}")
                    return
                st["net_robux"] = n
                st["gross_robux"] = calc_gross(n, float(s.get("gross_divider", 0.7)))
                price_per_100 = float(s.get("price_rub_per_100", 50))
                st["price_rub"] = round(n / 100 * price_per_100, 2)
                st["step"] = "await_username"
                bot.send_message(chat_id,
                                 f"Ок, {n} Robux.\n"
                                 f"Game Pass на {st['gross_robux']} R$.\n"
                                 f"К оплате: {st['price_rub']} ₽\n\n"
                                 "Пришли свой Roblox username:")
                return

            if step == "await_username":
                u = validate_username(text)
                if not u:
                    bot.send_message(chat_id, "❌ Неверный формат ника.")
                    return
                try:
                    user = roblox_find_user(u)
                except Exception:
                    bot.send_message(chat_id, "⚠️ Не удалось проверить Roblox.")
                    return
                if not user:
                    bot.send_message(chat_id, "❌ Аккаунт не найден.")
                    return
                st["roblox_username"] = user.get("name") or u
                st["roblox_user_id"] = int(user.get("id"))
                try:
                    places = roblox_get_user_places(st["roblox_user_id"])
                except Exception:
                    places = []
                if not places:
                    st["step"] = "await_place_manual"
                    bot.send_message(chat_id, f"✅ Аккаунт: {st['roblox_username']}\n\n"
                                              "⚠️ Публичных игр нет. Пришли Place ID вручную.")
                    return
                st["places"] = places
                st["step"] = "await_place"
                lines = [f"✅ Аккаунт: {st['roblox_username']}", "", "Выбери Place (номер):", ""]
                for i, p in enumerate(places[:10], 1):
                    lines.append(f"{i}) {p['name']}  (ID {p['place_id']})")
                bot.send_message(chat_id, "\n".join(lines))
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
                    bot.send_message(chat_id, "❌ Пришли номер или Place ID.")
                    return
                st["place_id"] = chosen["place_id"]
                st["place_name"] = chosen["name"]
                st["step"] = "await_gamepass"
                bot.send_message(chat_id,
                                 f"✅ Place: {chosen['name']} (ID {chosen['place_id']})\n\n"
                                 f"Создай Game Pass на {st['gross_robux']} R$ (!pass — как).\n"
                                 "Затем пришли ссылку/ID Game Pass.")
                return

            if step == "await_place_manual":
                pid = parse_place_id(text)
                if not pid:
                    bot.send_message(chat_id, "❌ Пришли числовой Place ID.")
                    return
                st["place_id"] = pid
                st["place_name"] = "manual"
                st["step"] = "await_gamepass"
                bot.send_message(chat_id, f"✅ Place ID: {pid}\n\n"
                                          f"Создай Game Pass на {st['gross_robux']} R$.\n"
                                          "Затем пришли ссылку/ID.")
                return

            if step == "await_gamepass":
                gp_id = parse_gamepass_id(text)
                if not gp_id:
                    bot.send_message(chat_id, "❌ Не понял. Пришли ссылку/ID Game Pass.")
                    return
                details = roblox_get_gamepass_details(gp_id)
                if details:
                    price = details.get("price") or details.get("Price") or details.get("priceInRobux")
                    if price is not None and int(price) != int(st["gross_robux"]):
                        bot.send_message(chat_id,
                                         f"❌ Цена Game Pass не совпадает.\n"
                                         f"Нужно: {st['gross_robux']}\nСейчас: {price}")
                        return
                st["gamepass_id"] = gp_id
                order_id = f"T{int(time.time())}{chat_id % 10000}"
                st["order_id"] = order_id
                self.orders[order_id] = {
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
                kb = types.InlineKeyboardMarkup()
                kb.add(types.InlineKeyboardButton("✅ Я оплатил", callback_data=f"paid:{order_id}"))
                bot.send_message(chat_id,
                                 "📋 Заказ собран:\n\n"
                                 f"ID: {order_id}\n"
                                 f"Roblox: {st['roblox_username']}\n"
                                 f"Получишь: {st['net_robux']} R$\n"
                                 f"К оплате: {st['price_rub']} ₽\n\n"
                                 "💳 (Тест) Нажми кнопку:",
                                 reply_markup=kb)
                return

    def process_update(self, update):
        self.bot.process_new_updates([update])


# Глобальный инстанс (создаётся в bot.py)
shop_instance: Optional[ShopBot] = None


def get_shop_bot() -> Optional[ShopBot]:
    return shop_instance


def init_shop_bot():
    global shop_instance
    if shop_instance is None:
        shop_instance = ShopBot(SHOP_BOT_TOKEN)
        logger.info("Shop bot инициализирован.")
    return shop_instance
