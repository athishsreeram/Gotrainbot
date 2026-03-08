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

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)

logger = logging.getLogger(__name__)

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


def fetch_departures(station_cd):
    url = f"{BASE_URL}/StationStatusJSON/Service/StationCd/Lang/GT/{station_cd}/EN"

    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error("API error for %s: %s", station_cd, e)
        return None


def parse_trips(data):
    try:
        inner = data.get("d") or data.get("ReturnStringValue", {}).get("Data", "")

        if isinstance(inner, str):
            inner = json.loads(inner)

        return (
            inner.get("Trips")
            or inner.get("trips")
            or inner.get("StationStatusJSON", {}).get("Trips")
            or []
        )
    except Exception:
        return []


def format_message(station_cd, header_suffix=""):
    label = LINE_CODES.get(station_cd.upper(), station_cd.upper())

    data = fetch_departures(station_cd.upper())

    if data is None:
        return "Could not reach GO Tracker. Try again shortly."

    trips = parse_trips(data)

    if not trips:
        return f"*GO Train - {label} line*\n\nNo upcoming departures right now."

    lines = [
        f"*GO Train - {label} line*{header_suffix}",
        f"_Union Station - {datetime.now().strftime('%H:%M')}_\n",
    ]

    for trip in trips[:8]:
        dest = trip.get("TripDestName") or trip.get("destination") or "?"
        sched_time = trip.get("ScheduledTime") or trip.get("scheduledTime") or "?"
        actual_time = trip.get("ActualTime") or trip.get("actualTime") or sched_time
        platform = trip.get("Platform") or trip.get("platform") or "?"
        status = trip.get("Status") or trip.get("status") or "On time"
        train_num = trip.get("TripNumber") or trip.get("tripNumber") or ""

        delay_str = f" _(was {sched_time})_" if actual_time != sched_time else ""

        if "cancel" in status.lower():
            dot = "🔴"
        elif "on time" in status.lower():
            dot = "🟢"
        else:
            dot = "🟡"

        lines.append(
            f"{dot} *{actual_time}*{delay_str} to {dest}\n"
            f"   #{train_num} - Platform {platform} - {status}"
        )

    lines.append("\n_gotracker.ca_")

    return "\n".join(lines)


async def send_alert(context: ContextTypes.DEFAULT_TYPE):
    job = context.job

    user_id = job.data["user_id"]
    chat_id = job.data["chat_id"]
    line_code = job.data["line_code"]

    msg = format_message(line_code, header_suffix=" - Daily Alert")

    await context.bot.send_message(
        chat_id=chat_id,
        text=msg,
        parse_mode="Markdown"
    )


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_username = context.bot.username

    msg = (
        "*Welcome to GO Train Departures Bot!*\n\n"
        "Quick start:\n"
        "1. Save your line: /setfav MO\n"
        "2. Get departures anytime: /myfav\n"
        "3. Set a daily alert: /setalert MO 08:00\n"
        f"4. Use inline anywhere: @{bot_username} MO\n\n"
        "Line codes:\n"
        + "\n".join(f"  {k} - {v}" for k, v in LINE_CODES.items())
        + "\n\n/help for full command list"
    )

    await update.message.reply_text(msg, parse_mode="Markdown")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    bot_username = context.bot.username

    msg = (
        "*GO Train Bot - Commands*\n\n"
        "/go MO - Live departures\n"
        "/setfav MO - Save favourite line\n"
        "/myfav - Get favourite departures\n"
        "/setalert MO 08:00 - Daily alert\n"
        "/cancelalert - Cancel alert\n"
        "/mystatus - View settings\n"
        "/lines - Show codes\n\n"
        f"Inline:\n@{bot_username} MO"
    )

    await update.message.reply_text(msg, parse_mode="Markdown")


async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query.query.strip().upper()

    results = []

    if query and query not in LINE_CODES:
        results.append(
            InlineQueryResultArticle(
                id=str(uuid4()),
                title="Unknown line code",
                description="Try: " + " ".join(LINE_CODES.keys()),
                input_message_content=InputTextMessageContent(
                    "Unknown line. Valid codes: " + " ".join(LINE_CODES.keys())
                ),
            )
        )

        await update.inline_query.answer(results, cache_time=5)
        return

    lines_to_show = {query: LINE_CODES[query]} if query else LINE_CODES

    for code, name in lines_to_show.items():
        results.append(
            InlineQueryResultArticle(
                id=str(uuid4()),
                title=f"{name} ({code})",
                description="Tap to share live departures",
                input_message_content=InputTextMessageContent(
                    format_message(code),
                    parse_mode="Markdown"
                ),
            )
        )

    await update.inline_query.answer(results, cache_time=30)


def main():

    token = os.environ.get("BOT_TOKEN")

    if not token:
        print("ERROR: Set BOT_TOKEN environment variable.")
        sys.exit(1)

    app = ApplicationBuilder().token(token).build()

    app.add_handler(InlineQueryHandler(inline_query))

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))

    logger.info("Bot running!")

    app.run_polling(allowed_updates=["message", "inline_query"])


if __name__ == "__main__":
    main()