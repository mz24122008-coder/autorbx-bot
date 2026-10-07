# -*- coding: utf-8 -*-
# VorzaRBX — единая точка входа: запускает админ

-бота и магазинlogging.

import os
import logging
import traceback

import requests
from flask import Flask, request
import telebot

import admin_bot
import shop_bot.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("VorzaMain")

ADMIN_BOT_TOKEN = os.environ["ADMIN_BOT_TOKEN"]
SHOP_BOT_TOKEN = os.environ["SHOP_BOT_TOKEN"]
PUBLIC_URL = os.environ.get("PUBLIC_URL", "").rstrip("/")

app = Flask(__name__)

# Инициализируем магазин
shop_bot.init_shop_bot()


@app.route("/", methods=["GET"])
def index():
    return "VorzaRBX (admin + shop) is running", 200


@app.route(f"/webhook/admin/{ADMIN_BOT_TOKEN}", methods=["POST"])
def webhook_admin():
    if request.headers.get("content-type") == "application/json":
        json_str = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(json_str)
        admin_bot.bot.process_new_updates([update])
    return "ok", 200


@app.route(f"/webhook/shop/{SHOP_BOT_TOKEN}", methods=["POST"])
def webhook_shop():
    if request.headers.get("content-type") == "application/json":
        json_str = request.get_data().decode("utf-8")
        update = telebot.types.Update.de_json(json_str)
        s = shop_bot.get_shop_bot()
        if s:
            s.process_update(update)
    return "ok", 200


def set_webhooks():
    if not PUBLIC_URL:
        logger.warning("PUBLIC_URL не задан — вебхуки не установлены.")
        return
    try:
        r1 = requests.get(f"https://api.telegram.org/bot{ADMIN_BOT_TOKEN}/setWebhook",
                          params={"url": f"{PUBLIC_URL}/webhook/admin/{ADMIN_BOT_TOKEN}"},
                          timeout=20)
        logger.info("Admin setWebhook: %s %s", r1.status_code, r1.text[:200])
        r2 = requests.get(f"https://api.telegram.org/bot{SHOP_BOT_TOKEN}/setWebhook",
                          params={"url": f"{PUBLIC_URL}/webhook/shop/{SHOP_BOT_TOKEN}"},
                          timeout=20)
        logger.info("Shop setWebhook: %s %s", r2.status_code, r2.text[:200])
    except Exception:
        logger.error(traceback.format_exc())


if __name__ == "__main__":
    set_webhooks()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
