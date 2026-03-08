import os
import sys
import json
import logging
import requests
from datetime import datetime
from uuid import uuid4
from pathlib import Path

from telegram import Update, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    InlineQueryHandler,
    ContextTypes,
)

# ---------------- LOGGING ---------------- #

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)

# ---------------- CONFIG ---------------- #

BASE_URL = "https://www.gotracker.ca/GOTracker/web/GODataAPIProxy.svc"

LINE_CODES = {
    "MO": "Milton",
    "LW": "Lakeshore West",
    "LE": "Lakeshore East",
    "ST": "Stouffville",
    "RH": "Richmond Hill",
    "BR": "Barrie",
    "KI": "Kitchener",
}

USER_DB = Path("users.json")

# ---------------- USER STORAGE ---------------- #

def load_users():
    if USER_DB.exists():
        try:
            return json.loads(USER_DB.read_text())
        except Exception:
            return {}
    return {}


def save_users(users):
    USER_DB.write_text(json.dumps(users, indent=2))


def get_user(user_id):
    return load_users().get(str(user_id), {})


def set_user(user_id, data):
    users = load_users()
    uid = str(user_id)

    users[uid] = {**users.get(uid, {}), **data}

    save_users(users)

# ---------------- GO API ---------------- #

def fetch_departures(station_cd):

    url = f"{BASE_URL}/StationStatusJSON/Service/StationCd/Lang/GT/{station_cd}/EN"

    logger.info(f"Calling GO API for {station_cd}")
    logger.info(url)

    try:
        resp = requests.get(url, timeout=10)

        logger.info(f"API status: {resp.status_code}")

        resp.raise_for_status()

        data = resp.json()

        logger.info("API response received")

        return data

    except Exception as e:
        logger.exception("GO API ERROR")
        return None


def parse_trips(data):

    try:

        inner = data.get("d") or data.get("ReturnStringValue", {}).get("Data", "")

        if isinstance(inner, str):
            inner = json.loads(inner)

        trips = (
            inner.get("Trips")
            or inner.get("trips")
            or inner.get("StationStatusJSON", {}).get("Trips")
            or []
        )

        logger.info(f"Trips parsed: {len(trips)}")

        return trips

    except Exception:
        logger.exception("Trip parsing failed")
        return []

# ---------------- MESSAGE FORMAT ---------------- #

def format_message(station_cd, header_suffix=""):

    logger.info(f"Formatting message for {station_cd}")

    label = LINE_CODES.get(station_cd.upper(), station_cd.upper())

    data = fetch_departures(station_cd.upper())

    if data is None:
        return "⚠️ Could not reach GO Tracker API."

    trips = parse_trips(data)

    if not trips:
        return f"*GO Train - {label} line*\n\nNo upcoming departures."

    lines = [
        f"*GO Train - {label} line*{header_suffix}",
        f"_Union Station - {datetime.now().strftime('%H:%M')}_\n",
    ]

    for trip in trips[:8]:

        dest = trip.get("TripDestName") or "?"
        sched = trip.get("ScheduledTime") or "?"
        actual = trip.get("ActualTime") or sched
        platform = trip.get("Platform") or "?"
        status = trip.get("Status") or "On time"
        train = trip.get("TripNumber") or ""

        delay = f" _(was {sched})_" if actual != sched else ""

        if "cancel" in status.lower():
            emoji = "🔴"
        elif "on time" in status.lower():
            emoji = "🟢"
        else:
            emoji = "🟡"

        lines.append(
            f"{emoji} *{actual}*{delay} to {dest}\n"
            f"   #{train} - Platform {platform} - {status}"
        )

    lines.append("\n_gotracker.ca_")

    return "\n".join(lines)

# ---------------- COMMANDS ---------------- #

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    logger.info("Command /start received")

    bot_username = context.bot.username

    msg = (
        "*GO Train Departures Bot*\n\n"
        "Commands:\n"
        "/go MO - Get live departures\n"
        "/lines - Show line codes\n\n"
        f"Inline usage:\n@{bot_username} MO\n\n"
        "Available lines:\n"
        + "\n".join(f"{k} - {v}" for k, v in LINE_CODES.items())
    )

    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_lines(update: Update, context: ContextTypes.DEFAULT_TYPE):

    logger.info("Command /lines received")

    text = "*GO Train Line Codes*\n\n"

    text += "\n".join(f"{k} - {v}" for k, v in LINE_CODES.items())

    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_go(update: Update, context: ContextTypes.DEFAULT_TYPE):

    try:

        logger.info("Command /go received")

        code = context.args[0].upper() if context.args else "MO"

        logger.info(f"Line requested: {code}")

        if code not in LINE_CODES:

            logger.warning("Invalid line")

            await update.message.reply_text(
                "Unknown line. Use /lines."
            )

            return

        msg = await update.message.reply_text("Fetching departures...")

        result = format_message(code)

        logger.info("Sending formatted message")

        await msg.edit_text(result, parse_mode="Markdown")

    except Exception:

        logger.exception("Error in /go command")

        await update.message.reply_text("⚠️ Error fetching departures.")

# ---------------- INLINE ---------------- #

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.inline_query.query.strip().upper()

    logger.info(f"Inline query: {query}")

    results = []

    if query and query not in LINE_CODES:

        results.append(
            InlineQueryResultArticle(
                id=str(uuid4()),
                title="Unknown line code",
                description="Try: " + " ".join(LINE_CODES.keys()),
                input_message_content=InputTextMessageContent(
                    "Unknown line code."
                ),
            )
        )

        await update.inline_query.answer(results)

        return

    lines_to_show = {query: LINE_CODES[query]} if query else LINE_CODES

    for code, name in lines_to_show.items():

        results.append(
            InlineQueryResultArticle(
                id=str(uuid4()),
                title=f"{name} ({code})",
                description="Share live departures",
                input_message_content=InputTextMessageContent(
                    format_message(code),
                    parse_mode="Markdown",
                ),
            )
        )

    await update.inline_query.answer(results)

# ---------------- ERROR HANDLER ---------------- #

async def error_handler(update, context):

    logger.exception("Telegram error", exc_info=context.error)

# ---------------- MAIN ---------------- #

def main():

    token = os.environ.get("BOT_TOKEN")

    if not token:
        print("ERROR: BOT_TOKEN not set")
        sys.exit(1)

    logger.info("Starting bot")

    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("lines", cmd_lines))
    app.add_handler(CommandHandler("go", cmd_go))
    app.add_handler(InlineQueryHandler(inline_query))

    app.add_error_handler(error_handler)

    logger.info("Bot running")

    app.run_polling(allowed_updates=["message", "inline_query"])


if __name__ == "__main__":
    main()