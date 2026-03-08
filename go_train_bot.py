import os
import sys
import logging
import threading
import requests
from bs4 import BeautifulSoup
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ---------------- LOGGING ----------------
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ---------------- FLASK SERVER ----------------
app = Flask(__name__)

@app.route("/")
def home():
    return "Bot is running"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# ---------------- STATIONS ----------------
STATIONS = {
    "milton": "MO",
    "barrie": "BR",
    "lakeshorewest": "LW",
}

BASE_URL = "https://www.gotracker.ca/gotracker/mobile/StationDeparture/GT/{}"

# ---------------- SCRAPER ----------------
def fetch_trains(station_code):
    url = BASE_URL.format(station_code)

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.gotracker.ca/"
    }

    logger.info(f"Scraping URL: {url}")

    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()

        soup = BeautifulSoup(r.text, "html.parser")

        trips = []

        rows = soup.select("table tr")

        for row in rows[1:]:
            cols = row.find_all("td")
            if len(cols) < 4:
                continue

            trips.append({
                "time": cols[0].text.strip(),
                "dest": cols[2].text.strip(),
                "platform": cols[3].text.strip()
            })

        return trips

    except Exception as e:
        logger.exception("Scraping failed")
        return None

# ---------------- COMMANDS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚆 GO Train Bot\n\n"
        "/stations - list stations\n"
        "/go <station>"
    )

async def stations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Available:\n" + "\n".join(STATIONS.keys())
    )

async def go(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if not context.args:
        await update.message.reply_text("Usage: /go <station>")
        return

    station = context.args[0].lower()

    if station not in STATIONS:
        await update.message.reply_text("Unknown station")
        return

    trips = fetch_trains(STATIONS[station])

    if not trips:
        await update.message.reply_text("Could not fetch trains")
        return

    msg = f"🚆 {station.title()} departures\n\n"

    for t in trips[:6]:
        msg += f"{t['time']} → {t['dest']} (Platform {t['platform']})\n"

    await update.message.reply_text(msg)

# ---------------- TELEGRAM BOT ----------------
def run_bot():

    token = os.environ.get("BOT_TOKEN")

    if not token:
        logger.error("BOT_TOKEN missing")
        sys.exit(1)

    bot = ApplicationBuilder().token(token).build()

    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CommandHandler("stations", stations))
    bot.add_handler(CommandHandler("go", go))

    logger.info("Telegram bot running")

    bot.run_polling()

# ---------------- MAIN ----------------
if __name__ == "__main__":

    # start web server thread
    threading.Thread(target=run_web).start()

    # start telegram bot
    run_bot()