# #!/usr/bin/env python3
“””
GO Train Union Station Departures — Inline Telegram Bot

Anyone can use this in ANY chat by typing:
@YourBot MO        → Milton line
@YourBot LE        → Lakeshore East
@YourBot BR        → Barrie
@YourBot           → shows all line options to pick from

Setup:
pip install python-telegram-bot requests

Also enable inline mode in @BotFather:
/setinline → @YourBot → set placeholder text e.g. “MO, LE, BR…”

Run:
BOT_TOKEN=<your_token> python go_train_bot.py
“””

import os
import sys
import logging
import requests
import json
from datetime import datetime
from uuid import uuid4

from telegram import (
Update,
InlineQueryResultArticle,
InputTextMessageContent,
)
from telegram.ext import (
ApplicationBuilder,
CommandHandler,
InlineQueryHandler,
ContextTypes,
)

logging.basicConfig(
format=”%(asctime)s [%(levelname)s] %(message)s”,
level=logging.INFO,
)
logger = logging.getLogger(**name**)

# ── Config ────────────────────────────────────────────────────────────────────

BASE_URL = “https://www.gotracker.ca/GOTracker/web/GODataAPIProxy.svc”

LINE_CODES = {
“MO”: “Milton”,
“LW”: “Lakeshore West”,
“LE”: “Lakeshore East”,
“ST”: “Stouffville”,
“RH”: “Richmond Hill”,
“BR”: “Barrie”,
“KI”: “Kitchener”,
}

# ── GO Tracker API ────────────────────────────────────────────────────────────

def fetch_departures(station_cd: str, lang: str = “EN”) -> dict | None:
url = f”{BASE_URL}/StationStatusJSON/Service/StationCd/Lang/GT/{station_cd}/{lang}”
try:
resp = requests.get(url, timeout=10)
resp.raise_for_status()
return resp.json()
except Exception as e:
logger.error(“API error for %s: %s”, station_cd, e)
return None

def parse_trips(data: dict) -> list:
try:
inner = data.get(“d”) or data.get(“ReturnStringValue”, {}).get(“Data”, “”)
if isinstance(inner, str):
inner = json.loads(inner)
return (
inner.get(“Trips”)
or inner.get(“trips”)
or inner.get(“StationStatusJSON”, {}).get(“Trips”)
or []
)
except Exception:
return []

def format_message(station_cd: str) -> str:
label = LINE_CODES.get(station_cd.upper(), station_cd.upper())
data = fetch_departures(station_cd.upper())

```
if data is None:
    return "❌ Could not reach GO Tracker. Try again in a moment."

trips = parse_trips(data)

if not trips:
    return f"🚂 *GO Train — {label} line*\n\nNo upcoming departures right now."

lines = [
    f"🚂 *GO Train — {label} line*",
    f"_Union Station departures · {datetime.now().strftime('%H:%M')}_\n",
]

for trip in trips[:8]:
    dest        = trip.get("TripDestName") or trip.get("destination") or "?"
    sched_time  = trip.get("ScheduledTime") or trip.get("scheduledTime") or "?"
    actual_time = trip.get("ActualTime") or trip.get("actualTime") or sched_time
    platform    = trip.get("Platform") or trip.get("platform") or "?"
    status      = trip.get("Status") or trip.get("status") or "On time"
    train_num   = trip.get("TripNumber") or trip.get("tripNumber") or ""

    delay_str = f" _(was {sched_time})_" if actual_time != sched_time else ""
    emoji = "🟢" if "on time" in status.lower() else "🔴" if "cancel" in status.lower() else "🟡"

    lines.append(
        f"{emoji} *{actual_time}*{delay_str} → {dest}\n"
        f"   `#{train_num}` · Platform {platform} · {status}"
    )

lines.append("\n_[gotracker.ca](https://www.gotracker.ca)_")
return "\n".join(lines)
```

# ── Inline Query Handler ──────────────────────────────────────────────────────

async def inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
query = update.inline_query.query.strip().upper()
results = []

```
if query and query not in LINE_CODES:
    results.append(
        InlineQueryResultArticle(
            id=str(uuid4()),
            title="❓ Unknown line code",
            description=f"'{query}' not found. Try: MO LW LE ST RH BR KI",
            input_message_content=InputTextMessageContent(
                f"Unknown GO Train line `{query}`.\nValid codes: `{'` `'.join(LINE_CODES)}`",
                parse_mode="Markdown",
            ),
        )
    )
    await update.inline_query.answer(results, cache_time=5)
    return

lines_to_show = {query: LINE_CODES[query]} if query else LINE_CODES

for code, name in lines_to_show.items():
    message_text = format_message(code)
    results.append(
        InlineQueryResultArticle(
            id=str(uuid4()),
            title=f"🚂 {name}  ({code})",
            description="Tap to share live departures from Union Station",
            input_message_content=InputTextMessageContent(
                message_text,
                parse_mode="Markdown",
                disable_web_page_preview=True,
            ),
        )
    )

await update.inline_query.answer(results, cache_time=30)
```

# ── Commands ──────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
bot_username = context.bot.username
msg = (
“👋 *GO Train Departures Bot*\n\n”
“Use me *in any Telegram chat* — no need to add me!\n\n”
“Just type in any chat input box:\n”
f”`@{bot_username} MO` → Milton line\n”
f”`@{bot_username} LE` → Lakeshore East\n”
f”`@{bot_username}`   → Browse all lines\n\n”
“*Available line codes:*\n”
+ “\n”.join(f”  `{k}` — {v}” for k, v in LINE_CODES.items())
+ “\n\nData from [GO Tracker](https://www.gotracker.ca) · Real-time ✅”
)
await update.message.reply_text(msg, parse_mode=“Markdown”, disable_web_page_preview=True)

async def cmd_go(update: Update, context: ContextTypes.DEFAULT_TYPE):
code = (context.args[0].upper() if context.args else “MO”)
if code not in LINE_CODES:
await update.message.reply_text(
f”Unknown line `{code}`. Valid: `{'` `'.join(LINE_CODES)}`”,
parse_mode=“Markdown”
)
return
await update.message.reply_text(“🔄 Fetching…”)
await update.message.reply_text(
format_message(code),
parse_mode=“Markdown”,
disable_web_page_preview=True
)

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
token = os.environ.get(“BOT_TOKEN”)
if not token:
print(“ERROR: Set BOT_TOKEN environment variable.”)
sys.exit(1)

```
app = ApplicationBuilder().token(token).build()
app.add_handler(InlineQueryHandler(inline_query))
app.add_handler(CommandHandler("start", cmd_start))
app.add_handler(CommandHandler("help", cmd_start))
app.add_handler(CommandHandler("go", cmd_go))

logger.info("✅ Inline bot running!")
app.run_polling(allowed_updates=["message", "inline_query"])
```

if **name** == “**main**”:
main()
