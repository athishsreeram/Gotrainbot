"""
GO Train Telegram Bot
=====================
Uses Playwright (headless Chromium) to render gotracker.ca — a JavaScript React app
that cannot be scraped with plain requests/BeautifulSoup.

Two modes per station:
  - /from <station>  →  StationDeparture  (station → Union)
  - /to <station>    →  UnionDeparture    (Union → station)

Install deps:
  pip install python-telegram-bot playwright flask
  playwright install chromium
"""

import os
import sys
import logging
import threading
import asyncio

from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
from playwright.async_api import async_playwright

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
    return "GO Train Bot Running"

def run_web():
    port = int(os.environ.get("PORT", 10000))
    logger.info(f"Web server starting on port {port}")
    flask_app.run(host="0.0.0.0", port=port, use_reloader=False)


# ── Station config ────────────────────────────────────────────────────────────
# Maps user-friendly name → GO Tracker station code (used in URL)
STATIONS = {
    "union":           "UN",
    "mountpleasant":   "MP",
    "brampton":        "BR",
    "bramalea":        "BM",
    "georgetown":      "GE",
    "acton":           "AC",
    "guelph":          "GU",
    "kitchener":       "KI",
    "milton":          "MI",
    "oakville":        "OA",
    "burlington":      "BU",
    "hamilton":        "HA",
    "westharbour":     "WR",
    "niagarafalls":    "NF",
    "oshawa":          "OS",
    "whitby":          "WH",
    "ajax":            "AJ",
    "pickering":       "PI",
    "rougehill":       "RO",
    "scarborough":     "SC",
    "eglinton":        "EG",
    "agincourt":       "AG",
    "milliken":        "MK",
    "unionville":      "UV",
    "centennial":      "CE",
    "markham":         "MR",
    "mountjoy":        "MJ",
    "stouffville":     "ST",
    "aurora":          "AU",
    "newmarket":       "NE",
    "bradford":        "BD",
    "innisfil":        "IN",
    "barriesouth":     "BS",
    "allandale":       "AL",
    "bloor":           "BL",
    "weston":          "WE",
    "etobicokenorth":  "ET",
    "malton":          "MA",
}

# Short aliases → canonical name
ALIASES = {
    "mp":     "mountpleasant",
    "mount":  "mountpleasant",
    "bram":   "bramalea",
    "geo":    "georgetown",
    "kit":    "kitchener",
    "mil":    "milton",
    "oak":    "oakville",
    "burl":   "burlington",
    "ham":    "hamilton",
    "osh":    "oshawa",
    "whi":    "whitby",
    "pick":   "pickering",
    "scar":   "scarborough",
    "mark":   "markham",
    "stou":   "stouffville",
    "aur":    "aurora",
    "new":    "newmarket",
    "brad":   "bradford",
    "bar":    "barriesouth",
    "barrie": "barriesouth",
}

BASE_URL = "https://www.gotracker.ca/gotracker/mobile"


# ── Playwright scraper ────────────────────────────────────────────────────────
async def scrape_page(url: str) -> list[dict]:
    """
    Renders the gotracker.ca React page with a real headless browser.
    Uses two strategies:
      1. Intercept the internal JSON API call the React app makes.
      2. Fall back to parsing the rendered DOM table/list.
    """
    trips = []
    intercepted = {}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
                "Mobile/15E148 Safari/604.1"
            ),
            viewport={"width": 390, "height": 844},
        )
        page = await context.new_page()

        # Strategy 1 — intercept JSON responses from the app's backend API
        async def on_response(resp):
            try:
                ct = resp.headers.get("content-type", "")
                if "json" in ct:
                    url_lower = resp.url.lower()
                    if any(k in url_lower for k in ("depart", "trip", "service", "stop", "station")):
                        data = await resp.json()
                        logger.info(f"[Intercept] {resp.url}")
                        intercepted.setdefault("calls", []).append(data)
            except Exception:
                pass

        page.on("response", on_response)

        logger.info(f"[Playwright] → {url}")
        try:
            await page.goto(url, wait_until="networkidle", timeout=30_000)
        except Exception as e:
            logger.warning(f"[Playwright] Navigation warning (continuing): {e}")

        # Extra wait for React rendering
        await page.wait_for_timeout(2500)

        # ── Parse intercepted JSON ────────────────────────────────────────────
        for data in intercepted.get("calls", []):
            trips = _extract_from_json(data)
            if trips:
                logger.info(f"[Strategy 1] Got {len(trips)} trips from JSON")
                await browser.close()
                return trips

        # ── Parse rendered DOM ────────────────────────────────────────────────
        logger.info("[Strategy 2] Parsing rendered DOM")

        # Try <table> rows first
        rows = await page.query_selector_all("tr")
        for row in rows:
            cells = await row.query_selector_all("td")
            if len(cells) >= 2:
                texts = [await c.inner_text() for c in cells]
                time_val = next((t.strip() for t in texts if _looks_like_time(t)), "")
                if time_val:
                    trips.append({
                        "time":     time_val,
                        "dest":     texts[1].strip() if len(texts) > 1 else "",
                        "platform": texts[2].strip() if len(texts) > 2 else "—",
                        "status":   texts[3].strip() if len(texts) > 3 else "",
                    })

        if not trips:
            # Try common React list/card selectors
            for selector in ["[class*='trip']", "[class*='departure']", "[class*='card']", "li"]:
                els = await page.query_selector_all(selector)
                for el in els:
                    txt = (await el.inner_text()).strip()
                    lines = [l.strip() for l in txt.splitlines() if l.strip()]
                    time_val = next((l for l in lines if _looks_like_time(l)), "")
                    if time_val:
                        trips.append({
                            "time":     time_val,
                            "dest":     lines[1] if len(lines) > 1 else "",
                            "platform": lines[2] if len(lines) > 2 else "—",
                            "status":   lines[3] if len(lines) > 3 else "",
                        })
                if trips:
                    break

        logger.info(f"[Strategy 2] Got {len(trips)} trips from DOM")
        await browser.close()

    return trips


def _looks_like_time(s: str) -> bool:
    """Returns True if the string looks like HH:MM or H:MM."""
    s = s.strip()
    if len(s) < 4 or len(s) > 8:
        return False
    return ":" in s and s.replace(":", "").replace(" ", "").replace("AM", "").replace("PM", "").isdigit()


def _extract_from_json(obj, _depth=0) -> list[dict]:
    """Recursively walk JSON looking for departure-like objects."""
    if _depth > 10:
        return []
    trips = []
    if isinstance(obj, list):
        for item in obj:
            trips.extend(_extract_from_json(item, _depth + 1))
    elif isinstance(obj, dict):
        lower_keys = {k.lower(): k for k in obj}
        time_key = next((lower_keys[k] for k in lower_keys if "time" in k or "depart" in k or "sched" in k), None)
        if time_key:
            dest_key = next((lower_keys[k] for k in lower_keys if "dest" in k or "to" in k or "name" in k), None)
            plat_key = next((lower_keys[k] for k in lower_keys if "plat" in k or "track" in k), None)
            stat_key = next((lower_keys[k] for k in lower_keys if "status" in k or "delay" in k or "actual" in k), None)
            trips.append({
                "time":     str(obj.get(time_key, "")),
                "dest":     str(obj.get(dest_key, "")) if dest_key else "",
                "platform": str(obj.get(plat_key, "—")) if plat_key else "—",
                "status":   str(obj.get(stat_key, "")) if stat_key else "",
            })
        else:
            for v in obj.values():
                trips.extend(_extract_from_json(v, _depth + 1))
    return trips


def fetch_sync(station_code: str, direction: str) -> list[dict]:
    """
    Blocking wrapper around the async scraper.
    direction: "from" → StationDeparture (station → Union)
               "to"   → UnionDeparture   (Union → station)
    """
    endpoint = "StationDeparture" if direction == "from" else "UnionDeparture"
    url = f"{BASE_URL}/{endpoint}/GT/{station_code}"
    return asyncio.run(scrape_page(url))


# ── Station resolver ──────────────────────────────────────────────────────────
def resolve(raw: str):
    """Return (canonical_name, code) or (None, None)."""
    name = raw.lower().strip().replace(" ", "").replace("-", "")
    name = ALIASES.get(name, name)
    code = STATIONS.get(name)
    if code:
        return name, code
    # Prefix match
    matches = [(k, v) for k, v in STATIONS.items() if k.startswith(name)]
    if len(matches) == 1:
        return matches[0]
    return None, None


# ── Message formatter ─────────────────────────────────────────────────────────
def fmt_status(s: str) -> str:
    u = s.upper()
    if not s:             return ""
    if "ON TIME" in u:    return "✅"
    if "DELAY"   in u:    return f"⚠️ {s}"
    if "CANCEL"  in u:    return f"❌ {s}"
    return s


def build_reply(trips: list[dict], title: str) -> str:
    if not trips:
        return "😕 No departures found right now."
    lines = [f"🚆 *{title}*\n"]
    for t in trips[:8]:
        st   = fmt_status(t.get("status", ""))
        plat = t.get("platform", "—")
        dest = t.get("dest", "")
        time = t.get("time", "")
        row  = f"`{time}` → *{dest}*  🚉 {plat}"
        if st:
            row += f"  {st}"
        lines.append(row)
    return "\n".join(lines)


# ── Telegram commands ─────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚆 *GO Train Bot*\n\n"
        "Commands:\n"
        "  `/from <station>` — train FROM station TO Union\n"
        "  `/to <station>` — train FROM Union TO station\n"
        "  `/stations` — list all station names\n\n"
        "Examples:\n"
        "  `/from mountpleasant`  or  `/from mp`\n"
        "  `/to mountpleasant`    or  `/to mp`",
        parse_mode="Markdown",
    )


async def cmd_stations(update: Update, context: ContextTypes.DEFAULT_TYPE):
    names = sorted(STATIONS.keys())
    text  = "\n".join(f"  • `{n}`" for n in names)
    await update.message.reply_text(f"📍 *Stations:*\n\n{text}", parse_mode="Markdown")


async def _handle(update: Update, context: ContextTypes.DEFAULT_TYPE, direction: str):
    cmd = "from" if direction == "from" else "to"
    if not context.args:
        await update.message.reply_text(
            f"Usage: `/{cmd} <station>`\nExample: `/{cmd} mountpleasant`",
            parse_mode="Markdown",
        )
        return

    canonical, code = resolve(" ".join(context.args))
    if not code:
        await update.message.reply_text(
            f"❓ Unknown station: *{' '.join(context.args)}*\n"
            "Send /stations for the full list.",
            parse_mode="Markdown",
        )
        return

    arrow = "→ Union" if direction == "from" else "← from Union"
    await update.message.reply_text(
        f"⏳ Fetching *{canonical.title()}* {arrow}…",
        parse_mode="Markdown",
    )

    try:
        loop = asyncio.get_event_loop()
        trips = await loop.run_in_executor(None, fetch_sync, code, direction)
    except Exception as e:
        logger.exception("Scraper failed")
        await update.message.reply_text(f"⚠️ Error fetching data: {e}")
        return

    title = (
        f"{canonical.title()} → Union"
        if direction == "from"
        else f"Union → {canonical.title()}"
    )
    await update.message.reply_text(build_reply(trips, title), parse_mode="Markdown")


async def cmd_from(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _handle(update, context, "from")

async def cmd_to(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _handle(update, context, "to")


# ── Bot runner ────────────────────────────────────────────────────────────────
def run_bot():
    token = os.environ.get("BOT_TOKEN")
    if not token:
        logger.error("BOT_TOKEN not set")
        sys.exit(1)

    logger.info("Starting Telegram bot")
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("stations", cmd_stations))
    app.add_handler(CommandHandler("from",     cmd_from))
    app.add_handler(CommandHandler("to",       cmd_to))
    app.run_polling()


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    threading.Thread(target=run_web, daemon=True).start()
    run_bot()