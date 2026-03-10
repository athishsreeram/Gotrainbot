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
    return "GO Train Bot Running"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    logger.info(f"Web server starting on port {port}")
    app.run(host="0.0.0.0", port=port, use_reloader=False)


# ---------------- STATIONS ----------------
# Station codes for the Metrolinx Open Data API
# Full list: https://api.openmetrolinx.com/OpenDataAPI/api/V1/Stop/GO
STATIONS = {
    "union":         "UN",
    "milton":        "MI",
    "brampton":      "BR",
    "mississauga":   "MS",
    "oakville":      "OA",
    "burlington":    "BU",
    "hamilton":      "HA",
    "oshawa":        "OS",
    "whitby":        "WH",
    "ajax":          "AJ",
    "pickering":     "PI",
    "rouge hill":    "RO",
    "scarborough":   "SC",
    "eglinton":      "EG",
    "agincourt":     "AG",
    "milliken":      "MK",
    "unionville":    "UV",
    "centennial":    "CE",
    "markham":       "MR",
    "mount joy":     "MJ",
    "stouffville":   "ST",
    "barrie south":  "BD",
    "allandale":     "AL",
    "aurora":        "AU",
    "newmarket":     "NE",
    "east gwillimbury": "EG",
    "bradford":      "BA",
    "innisfil":      "IN",
    "kitchener":     "KI",
    "guelph":        "GU",
    "guelph central":"GC",
    "acton":         "AC",
    "georgetown":    "GE",
    "mount pleasant":"MP",
    "brampton":      "BR",
    "bloor":         "BL",
    "weston":        "WE",
    "etobicoke north":"ET",
    "malton":        "MA",
    "bramalea":      "BM",
    "niagara falls": "NI",
    "st catharines": "SC",
    "grimsby":       "GR",
    "west harbour":  "WR",
}

# Simplified alias map (what users will actually type)
STATION_ALIASES = {
    "union":        "UN",
    "milton":       "MI",
    "oakville":     "OA",
    "burlington":   "BU",
    "hamilton":     "HA",
    "oshawa":       "OS",
    "whitby":       "WH",
    "ajax":         "AJ",
    "pickering":    "PI",
    "barrie":       "BD",
    "aurora":       "AU",
    "newmarket":    "NE",
    "bradford":     "BA",
    "kitchener":    "KI",
    "guelph":       "GU",
    "georgetown":   "GE",
    "brampton":     "BM",
    "bloor":        "BL",
    "weston":       "WE",
    "malton":       "MA",
}

# Metrolinx Open Data API — no API key needed for departure boards
# Docs: https://api.openmetrolinx.com/OpenDataAPI/Help
METROLINX_API = (
    "https://api.openmetrolinx.com/OpenDataAPI/api/V1/ServiceAtStop/GO/Departure/{}"
)

# Fallback: gotracker scrape (kept but updated URL pattern)
GOTRACKER_URL = "https://www.gotracker.ca/GoTracker/web/TripDeparture.aspx?stationCode={}"


# ---------------- PRIMARY: Metrolinx Open Data API ----------------
def fetch_trains_api(station_code: str) -> list[dict]:
    """
    Use the official Metrolinx Open Data departure API.
    Returns a list of dicts: {time, line, dest, platform, status}
    """
    url = METROLINX_API.format(station_code)
    headers = {
        "User-Agent": "GOTrainBot/1.0",
        "Accept": "application/json",
    }
    logger.info(f"[API] GET {url}")
    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()

        # Response shape:
        # { "ServiceAtStop": { "Lines": [ { "LineName", "Destinations": [ {
        #       "DestinationName", "Trips": [ { "ScheduledTime", "Platform",
        #       "Status", "ActualTime" } ] } ] } ] } }
        trips = []
        lines = (
            data.get("ServiceAtStop", {})
                .get("Lines", [])
        )
        for line in lines:
            line_name = line.get("LineName", "")
            for dest in line.get("Destinations", []):
                dest_name = dest.get("DestinationName", "")
                for trip in dest.get("Trips", []):
                    scheduled = trip.get("ScheduledTime", "")
                    actual    = trip.get("ActualTime", "")
                    platform  = trip.get("Platform", "—")
                    status    = trip.get("Status", "")
                    display_time = actual if actual else scheduled
                    trips.append({
                        "time":     display_time,
                        "line":     line_name,
                        "dest":     dest_name,
                        "platform": platform,
                        "status":   status,
                    })

        # Sort by scheduled time string (HH:MM format sorts lexicographically fine)
        trips.sort(key=lambda x: x["time"])
        logger.info(f"[API] {len(trips)} departures found for {station_code}")
        return trips

    except requests.exceptions.HTTPError as e:
        logger.warning(f"[API] HTTP error {e.response.status_code} for {station_code}")
        return []
    except Exception:
        logger.exception("[API] Unexpected error")
        return []


# ---------------- FALLBACK: Scraper ----------------
def fetch_trains_scraper(station_code: str) -> list[dict]:
    """
    Fallback HTML scraper against gotracker.ca.
    The site serves a simple mobile table — parse <tr>/<td>.
    """
    url = GOTRACKER_URL.format(station_code)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
        ),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-CA,en;q=0.9",
        "Referer": "https://www.gotracker.ca/",
    }
    logger.info(f"[SCRAPER] GET {url}")
    try:
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")

        trips = []
        # Try standard table rows first
        for row in soup.find_all("tr"):
            cols = row.find_all("td")
            if len(cols) < 3:
                continue
            time     = cols[0].get_text(strip=True)
            dest     = cols[1].get_text(strip=True) if len(cols) > 1 else ""
            platform = cols[2].get_text(strip=True) if len(cols) > 2 else "—"
            status   = cols[3].get_text(strip=True) if len(cols) > 3 else ""
            if time and ":" in time:   # basic sanity check it's a time
                trips.append({
                    "time":     time,
                    "line":     "",
                    "dest":     dest,
                    "platform": platform,
                    "status":   status,
                })

        logger.info(f"[SCRAPER] {len(trips)} rows parsed for {station_code}")
        return trips

    except Exception:
        logger.exception("[SCRAPER] Failed")
        return []


# ---------------- COMBINED FETCH ----------------
def fetch_trains(station_code: str) -> list[dict]:
    trips = fetch_trains_api(station_code)
    if not trips:
        logger.info("API returned nothing — trying scraper fallback")
        trips = fetch_trains_scraper(station_code)
    return trips


# ---------------- HELPERS ----------------
def format_status(status: str) -> str:
    s = status.upper()
    if "ON TIME" in s:
        return "✅"
    if "DELAY" in s or "LATE" in s:
        return "⚠️ Delayed"
    if "CANCEL" in s:
        return "❌ Cancelled"
    return status


def build_station_help() -> str:
    cols = sorted(STATION_ALIASES.keys())
    return "\n".join(f"  • {name}" for name in cols)


# ---------------- TELEGRAM COMMANDS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚆 *GO Train Departure Bot*\n\n"
        "Commands:\n"
        "  /stations — list all stations\n"
        "  /go <station> — next departures\n\n"
        "Example: `/go union`",
        parse_mode="Markdown",
    )


async def stations_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"📍 *Available stations:*\n\n{build_station_help()}\n\n"
        "Type `/go <name>` to get departures.",
        parse_mode="Markdown",
    )


async def go_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: `/go <station>`\nExample: `/go union`", parse_mode="Markdown")
        return

    station_input = " ".join(context.args).lower().strip()

    # Look up code
    code = STATION_ALIASES.get(station_input)
    if not code:
        # Fuzzy: find any key that starts with the input
        matches = [k for k in STATION_ALIASES if k.startswith(station_input)]
        if len(matches) == 1:
            station_input = matches[0]
            code = STATION_ALIASES[station_input]
        elif len(matches) > 1:
            await update.message.reply_text(
                f"Did you mean one of: {', '.join(matches)}?"
            )
            return
        else:
            await update.message.reply_text(
                f"❓ Unknown station: *{station_input}*\nSend /stations for a full list.",
                parse_mode="Markdown",
            )
            return

    await update.message.reply_text(f"⏳ Fetching departures for *{station_input.title()}*…", parse_mode="Markdown")

    trips = fetch_trains(code)

    if not trips:
        await update.message.reply_text(
            "😕 Could not fetch departure data right now.\n"
            "The GO Transit API may be temporarily unavailable."
        )
        return

    lines = [f"🚆 *{station_input.title()} Departures*\n"]
    for t in trips[:8]:
        status_icon = format_status(t.get("status", ""))
        platform    = t.get("platform", "—")
        dest        = t.get("dest", "")
        line_name   = f" [{t['line']}]" if t.get("line") else ""
        lines.append(
            f"`{t['time']}` → *{dest}*{line_name}  🚉{platform}  {status_icon}"
        )

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ---------------- TELEGRAM BOT ----------------
def run_bot():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN environment variable missing")
        sys.exit(1)

    logger.info("Starting Telegram bot")
    application = ApplicationBuilder().token(token).build()
    application.add_handler(CommandHandler("start",    start))
    application.add_handler(CommandHandler("stations", stations_cmd))
    application.add_handler(CommandHandler("go",       go_cmd))
    application.run_polling()


# ---------------- MAIN ----------------
if __name__ == "__main__":
    web_thread = threading.Thread(target=run_web, daemon=True)
    web_thread.start()
    run_bot()