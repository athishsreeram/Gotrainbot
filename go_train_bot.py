"""
GO Train Telegram Bot — Kitchener Line
=======================================
Uses the gotracker.ca real-time signage API:
  GET https://www.gotracker.ca/gotracker/mobile/proxy/web/Messages/Signage/Rail/{LINE}/{STATION}

Kitchener line stations (west → east), codes verified from live API:
  Kitchener (KI) → Guelph Central (GL) → Acton (AC) → Georgetown (GE) →
  Mount Pleasant (MO) → Brampton Innovation District GO (BR) → Bramalea (BE) →
  Malton (MA) → Weston (WE) → Mount Dennis (MD) → Bloor (BL) → Union (UN)

Commands:
  /from <station>   — next trains FROM that station → Union  (Inbound)
  /to <station>     — next trains FROM Union → that station  (Outbound)
  /stations         — list all supported stations

Install deps:
  pip install python-telegram-bot flask requests
"""

import os
import sys
import logging
import threading
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import requests
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


# ── Flask keep-alive ──────────────────────────────────────────────────────────
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "GO Train Bot (Kitchener Line) Running ✅"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    logger.info(f"Web server starting on port {port}")
    flask_app.run(host="0.0.0.0", port=port, use_reloader=False)


# ── Station Config ────────────────────────────────────────────────────────────
# Codes verified directly from live gotracker API stopsList responses.
# Ordered west → east.
STATIONS_ORDERED = [
    ("kitchener",     "KI", "Kitchener"),
    ("guelphcentral", "GL", "Guelph Central"),
    ("acton",         "AC", "Acton"),
    ("georgetown",    "GE", "Georgetown"),
    ("mountpleasant", "MO", "Mount Pleasant"),
    ("brampton",      "BR", "Brampton Innovation District GO"),
    ("bramalea",      "BE", "Bramalea"),
    ("malton",        "MA", "Malton"),
    ("weston",        "WE", "Weston"),
    ("mountdennis",   "MD", "Mount Dennis"),
    ("bloor",         "BL", "Bloor"),
    ("union",         "UN", "Union"),
]

# canonical → (code, display_name)
STATIONS = {name: (code, display) for name, code, display in STATIONS_ORDERED}

# Aliases → canonical name
ALIASES = {
    # Kitchener
    "ki":                 "kitchener",
    "kit":                "kitchener",
    # Guelph
    "guelph":             "guelphcentral",
    "gl":                 "guelphcentral",
    "gue":                "guelphcentral",
    # Acton
    "ac":                 "acton",
    # Georgetown
    "geo":                "georgetown",
    "ge":                 "georgetown",
    # Mount Pleasant
    "mp":                 "mountpleasant",
    "mo":                 "mountpleasant",
    "mount":              "mountpleasant",
    "pleasant":           "mountpleasant",
    # Brampton
    "br":                 "brampton",
    "bra":                "brampton",
    "bramptoninnovation": "brampton",
    # Bramalea
    "be":                 "bramalea",
    "bram":               "bramalea",
    "bml":                "bramalea",
    # Malton
    "ma":                 "malton",
    "mal":                "malton",
    # Weston
    "we":                 "weston",
    # Mount Dennis
    "md":                 "mountdennis",
    "dennis":             "mountdennis",
    # Bloor
    "bl":                 "bloor",
    # Union
    "un":                 "union",
    "unionstation":       "union",
}

LINE_CODE = "GT"   # Kitchener line identifier on gotracker
BASE_URL  = "https://www.gotracker.ca/gotracker/mobile/proxy/web/Messages/Signage/Rail"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
        "Mobile/15E148 Safari/604.1"
    ),
    "Accept": "application/json",
}


# ── API Fetcher ───────────────────────────────────────────────────────────────
def fetch_departures(station_code: str, direction: str) -> list[dict]:
    """
    Fetches real-time departure data from the gotracker signage API.

    direction: "from" → filter Inbound trips  (station → Union)
               "to"   → filter Outbound trips (Union → station)

    Returns list of dicts with trip details.
    """
    url = f"{BASE_URL}/{LINE_CODE}/{station_code}"
    logger.info(f"[API] GET {url}")

    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    if data.get("errCode", 0) != 0:
        logger.warning(f"[API] Error in response: {data.get('errMsg')}")
        return []

    target_direction = "Inbound" if direction == "from" else "Outbound"
    trips = []

    for dir_block in data.get("directions", []):
        if dir_block.get("direction") != target_direction:
            continue

        for trip in dir_block.get("tripMessages", []):
            scheduled_raw = trip.get("scheduled", "")
            actual_raw    = trip.get("actual", "")

            scheduled_dt = _parse_dt(scheduled_raw)
            actual_dt    = _parse_dt(actual_raw)

            # Compute delay
            delay_str = ""
            if scheduled_dt and actual_dt:
                delta_secs = (actual_dt - scheduled_dt).total_seconds()
                if delta_secs > 60:
                    delay_str = f"+{int(delta_secs // 60)} min late"
                elif delta_secs < -60:
                    delay_str = f"{int(delta_secs // 60)} min early"
                else:
                    delay_str = "On time"

            sched_str = scheduled_dt.strftime("%-I:%M %p") if scheduled_dt else scheduled_raw
            actual_str = actual_dt.strftime("%-I:%M %p")   if actual_dt    else actual_raw

            stops = [s["stopName"] for s in trip.get("stopsList", []) if s.get("stopName")]

            trips.append({
                "scheduled":   sched_str,
                "actual":      actual_str,
                "destination": trip.get("destination", ""),
                "track":       trip.get("track", "—"),
                "trip":        trip.get("tripName", ""),
                "coaches":     trip.get("coachCount", ""),
                "delay":       delay_str,
                "is_express":  trip.get("isExpress", False),
                "stops":       stops,
            })

    return trips


def _parse_dt(s: str):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


# ── Station resolver ──────────────────────────────────────────────────────────
def resolve(raw: str):
    """Return (canonical, code, display) or (None, None, None)."""
    key = raw.lower().strip().replace(" ", "").replace("-", "").replace("_", "")
    key = ALIASES.get(key, key)
    if key in STATIONS:
        code, display = STATIONS[key]
        return key, code, display
    # Prefix match
    matches = [(k, *v) for k, v in STATIONS.items() if k.startswith(key)]
    if len(matches) == 1:
        return matches[0]
    return None, None, None


# ── Message formatter ─────────────────────────────────────────────────────────
def fmt_delay(d: str) -> str:
    if not d:              return ""
    if d == "On time":     return "✅ On time"
    if "late" in d:        return f"⚠️ {d}"
    if "early" in d:       return f"🔵 {d}"
    return d


def build_reply(trips: list[dict], title: str) -> str:
    if not trips:
        return (
            "😕 No upcoming departures found.\n"
            "_No trains currently scheduled, or service has ended for today._"
        )

    lines = [f"🚆 *{title}*\n"]
    for t in trips[:6]:
        express_tag = "  🚀 _Express_" if t["is_express"] else ""
        delay_tag   = f"  {fmt_delay(t['delay'])}" if t["delay"] else ""
        coaches_tag = f"  🚃 {t['coaches']} cars" if t["coaches"] else ""

        row = f"`{t['scheduled']}` — Track *{t['track']}*{delay_tag}{coaches_tag}{express_tag}"
        if t["destination"]:
            row += f"\n  └ ➡️ *{t['destination']}*"
        if t["stops"] and len(t["stops"]) > 1:
            row += f"\n  └ 🛑 {' → '.join(t['stops'])}"

        lines.append(row)

    return "\n\n".join(lines)


# ── Telegram commands ─────────────────────────────────────────────────────────
HELP_TEXT = (
    "🚆 *GO Train Bot — Kitchener Line*\n\n"
    "Commands:\n"
    "  `/from <station>` — trains FROM station eastbound to Union\n"
    "  `/to <station>` — trains FROM Union westbound to station\n"
    "  `/stations` — list all supported stations\n\n"
    "Examples:\n"
    "  `/from mountpleasant`  or  `/from mp`\n"
    "  `/to georgetown`       or  `/to geo`\n"
    "  `/from kitchener`      or  `/from ki`\n"
    "  `/to bramalea`         or  `/to bram`"
)


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode="Markdown")


async def cmd_stations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = ["📍 *Kitchener Line Stations* (west → east)\n"]
    for canonical, code, display in STATIONS_ORDERED:
        lines.append(f"  `{canonical}` — {display} `[{code}]`")
    lines.append(
        "\n_Aliases also work, e.g. `mp`, `geo`, `ki`, `bram`, `guelph`_"
    )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


executor = ThreadPoolExecutor(max_workers=4)


async def _handle(update: Update, context: ContextTypes.DEFAULT_TYPE, direction: str):
    cmd = "from" if direction == "from" else "to"

    if not context.args:
        await update.message.reply_text(
            f"Usage: `/{cmd} <station>`\nExample: `/{cmd} mountpleasant`\n\n"
            "Send /stations for the full list.",
            parse_mode="Markdown",
        )
        return

    raw_input = " ".join(context.args)
    canonical, code, display = resolve(raw_input)

    if not code:
        await update.message.reply_text(
            f"❓ Unknown station: *{raw_input}*\n\n"
            "Send /stations for the Kitchener line station list.",
            parse_mode="Markdown",
        )
        return

    arrow = "→ Union 🏙️" if direction == "from" else "← from Union 🏙️"
    await update.message.reply_text(
        f"⏳ Fetching *{display}* {arrow}",
        parse_mode="Markdown",
    )

    try:
        loop = asyncio.get_event_loop()
        trips = await loop.run_in_executor(
            executor, fetch_departures, code, direction
        )
    except Exception as e:
        logger.exception("Fetch failed")
        await update.message.reply_text(f"⚠️ Error fetching data: {e}")
        return

    title = (
        f"{display} → Union"
        if direction == "from"
        else f"Union → {display}"
    )
    await update.message.reply_text(
        build_reply(trips, title),
        parse_mode="Markdown",
    )


async def cmd_from(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _handle(update, context, "from")

async def cmd_to(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _handle(update, context, "to")


# ── Bot runner ────────────────────────────────────────────────────────────────
def run_bot():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN environment variable not set")
        sys.exit(1)

    logger.info("Starting GO Train Telegram Bot (Kitchener Line)")
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("help",     cmd_start))
    app.add_handler(CommandHandler("stations", cmd_stations))
    app.add_handler(CommandHandler("from",     cmd_from))
    app.add_handler(CommandHandler("to",       cmd_to))
    app.run_polling()


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    run_bot()