import os
import sys
import logging
import requests
from datetime import datetime
from cachetools import TTLCache

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ---------------- LOGGING ----------------

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)

# ---------------- CONFIG ----------------

BASE_URL = "https://www.gotracker.ca/GOTracker/web/GODataAPIProxy.svc"

STATIONS = {
    "union": "UN",
    "milton": "MI",
    "oakville": "OA",
    "burlington": "BU",
    "portcredit": "PC",
    "clarkson": "CL",
}

cache = TTLCache(maxsize=100, ttl=30)

# ---------------- API ----------------


def fetch_station(station):

    if station in cache:
        return cache[station]

    url = f"{BASE_URL}/StationStatusJSON/Service/StationCd/Lang/GT/{station}/EN"

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.gotracker.ca/",
    }

    try:

        logger.info(f"Calling GO API {station}")

        r = requests.get(url, headers=headers, timeout=10)

        logger.info(f"Status {r.status_code}")

        r.raise_for_status()

        data = r.json()

        cache[station] = data

        return data

    except Exception as e:

        logger.exception("API error")

        return None


def parse_trips(data):

    try:

        inner = data.get("d")

        if isinstance(inner, str):
            import json

            inner = json.loads(inner)

        return inner.get("Trips", [])

    except Exception:

        logger.exception("Parse failed")

        return []


# ---------------- FORMAT ----------------


def format_trips(station_name, trips):

    if not trips:
        return f"No upcoming trains for {station_name}"

    msg = f"🚆 GO Departures — {station_name.title()}\n\n"

    for trip in trips[:5]:

        dest = trip.get("TripDestName", "?")

        time = trip.get("ActualTime") or trip.get("ScheduledTime")

        platform = trip.get("Platform", "?")

        status = trip.get("Status", "On time")

        if "cancel" in status.lower():
            emoji = "🔴"
        elif "on" in status.lower():
            emoji = "🟢"
        else:
            emoji = "🟡"

        msg += f"{emoji} {time} → {dest}\nPlatform {platform}\n\n"

    return msg


# ---------------- COMMANDS ----------------


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    msg = """
🚆 GO Train Bot

Commands

/go union
/go milton

/stations
"""

    await update.message.reply_text(msg)


async def stations(update: Update, context: ContextTypes.DEFAULT_TYPE):

    text = "Available stations:\n\n"

    for s in STATIONS:
        text += f"{s}\n"

    await update.message.reply_text(text)


async def go(update: Update, context: ContextTypes.DEFAULT_TYPE):

    try:

        if not context.args:
            await update.message.reply_text("Usage: /go union")
            return

        station_name = context.args[0].lower()

        if station_name not in STATIONS:
            await update.message.reply_text("Unknown station")
            return

        station_code = STATIONS[station_name]

        await update.message.reply_text("Fetching trains...")

        data = fetch_station(station_code)

        trips = parse_trips(data)

        msg = format_trips(station_name, trips)

        await update.message.reply_text(msg)

    except Exception:

        logger.exception("Command failed")

        await update.message.reply_text("Error fetching trains")


# ---------------- MAIN ----------------


def main():

    token = os.environ.get("BOT_TOKEN")

    if not token:
        print("BOT_TOKEN missing")
        sys.exit(1)

    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stations", stations))
    app.add_handler(CommandHandler("go", go))

    logger.info("Bot running")

    app.run_polling()


if __name__ == "__main__":
    main()