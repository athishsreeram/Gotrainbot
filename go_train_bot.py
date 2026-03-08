import os
import sys
import logging
import requests
from bs4 import BeautifulSoup
from datetime import datetime

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ---------------- LOGGING ----------------
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ---------------- STATIONS ----------------
STATIONS = {
    "mountplesant": "MO",
    "lakeshorewest": "LW",
    "lakeshoreeast": "LE",
    "stouffville": "ST",
    "richmondhill": "RH",
    "barrie": "BR",
    "kitchener": "KI",
}

BASE_URL = "https://www.gotracker.ca/gotracker/mobile/StationDeparture/GT/{}"

# ---------------- SCRAPING ----------------
def fetch_trains(station_code: str):
    url = BASE_URL.format(station_code.upper())
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://www.gotracker.ca/",
    }
    logger.info(f"Scraping URL: {url}")

    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        # Departures table
        trips = []
        rows = soup.select("table tr")
        for row in rows[1:]:  # skip header
            cols = row.find_all("td")
            if len(cols) < 4:
                continue
            scheduled = cols[0].text.strip()
            actual = cols[1].text.strip() or scheduled
            dest = cols[2].text.strip()
            platform = cols[3].text.strip()
            status = cols[4].text.strip() if len(cols) > 4 else "On time"

            trips.append({
                "ScheduledTime": scheduled,
                "ActualTime": actual,
                "TripDestName": dest,
                "Platform": platform,
                "Status": status,
            })
        return trips
    except Exception as e:
        logger.exception("Failed to fetch departures")
        return None

# ---------------- FORMAT ----------------
def format_trips(station_name, trips):
    if trips is None:
        return "Could not reach GO Tracker. Try again shortly."
    if not trips:
        return f"No upcoming trains for {station_name.title()}."

    msg = f"🚆 GO Departures — {station_name.title()}\n\n"
    for trip in trips[:8]:
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

        msg += f"{emoji} {time} → {dest}\nPlatform {platform} — {status}\n\n"
    return msg

# ---------------- COMMANDS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = "*🚆 GO Train Bot*\n\nCommands:\n"
    msg += "/stations — List stations\n"
    msg += "/go <station> — Live departures\n\n"
    msg += "Example: /go mountplesant"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def stations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "Available stations:\n" + "\n".join(STATIONS.keys())
    await update.message.reply_text(text)

async def go(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /go <station>")
        return
    station_name = context.args[0].lower()
    if station_name not in STATIONS:
        await update.message.reply_text("Unknown station. Use /stations to see available stations.")
        return

    await update.message.reply_text(f"Fetching departures for {station_name.title()}…")
    trips = fetch_trains(STATIONS[station_name])
    msg = format_trips(station_name, trips)
    await update.message.reply_text(msg)

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