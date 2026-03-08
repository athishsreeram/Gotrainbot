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

logging.basicConfig(format=”%(asctime)s [%(levelname)s] %(message)s”, level=logging.INFO)
logger = logging.getLogger(**name**)

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

USER_DB = Path(“users.json”)

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
url = BASE_URL + “/StationStatusJSON/Service/StationCd/Lang/GT/” + station_cd + “/EN”
try:
resp = requests.get(url, timeout=10)
resp.raise_for_status()
return resp.json()
except Exception as e:
logger.error(“API error for %s: %s”, station_cd, e)
return None

def parse_trips(data):
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

def format_message(station_cd, header_suffix=””):
label = LINE_CODES.get(station_cd.upper(), station_cd.upper())
data = fetch_departures(station_cd.upper())
if data is None:
return “Could not reach GO Tracker. Try again shortly.”
trips = parse_trips(data)
if not trips:
return “*GO Train - “ + label + “ line*\n\nNo upcoming departures right now.”

```
lines = [
    "*GO Train - " + label + " line*" + header_suffix,
    "_Union Station - " + datetime.now().strftime("%H:%M") + "_\n",
]
for trip in trips[:8]:
    dest        = trip.get("TripDestName") or trip.get("destination") or "?"
    sched_time  = trip.get("ScheduledTime") or trip.get("scheduledTime") or "?"
    actual_time = trip.get("ActualTime") or trip.get("actualTime") or sched_time
    platform    = trip.get("Platform") or trip.get("platform") or "?"
    status      = trip.get("Status") or trip.get("status") or "On time"
    train_num   = trip.get("TripNumber") or trip.get("tripNumber") or ""
    delay_str   = " _(was " + sched_time + ")_" if actual_time != sched_time else ""
    emoji       = "green" if "on time" in status.lower() else "red" if "cancel" in status.lower() else "yellow"
    dot         = "\U0001f7e2" if emoji == "green" else "\U0001f534" if emoji == "red" else "\U0001f7e1"
    lines.append(
        dot + " *" + actual_time + "*" + delay_str + " to " + dest + "\n"
        + "   #" + str(train_num) + " - Platform " + str(platform) + " - " + status
    )
lines.append("\n_gotracker.ca_")
return "\n".join(lines)
```

async def send_alert(context):
job = context.job
user_id   = job.data[“user_id”]
chat_id   = job.data[“chat_id”]
line_code = job.data[“line_code”]
msg = format_message(line_code, header_suffix=” - Daily Alert”)
await context.bot.send_message(chat_id=chat_id, text=msg, parse_mode=“Markdown”)

async def cmd_start(update, context):
bot_username = context.bot.username
msg = (
“*Welcome to GO Train Departures Bot!*\n\n”
“Quick start:\n”
“1. Save your line: /setfav MO\n”
“2. Get departures anytime: /myfav\n”
“3. Set a daily alert: /setalert MO 08:00\n”
“4. Use inline anywhere: @” + bot_username + “ MO\n\n”
“Line codes:\n”
+ “\n”.join(”  “ + k + “ - “ + v for k, v in LINE_CODES.items())
+ “\n\n/help for full command list”
)
await update.message.reply_text(msg, parse_mode=“Markdown”)

async def cmd_help(update, context):
bot_username = context.bot.username
msg = (
“*GO Train Bot - Commands*\n\n”
“/go MO - Live departures for a line\n”
“/setfav MO - Save your favourite line\n”
“/myfav - Get your favourite line departures\n”
“/setalert MO 08:00 - Daily alert at 8am\n”
“/cancelalert - Cancel your daily alert\n”
“/mystatus - View your saved settings\n”
“/lines - All line codes\n\n”
“Inline (works in any chat):\n”
“@” + bot_username + “ MO - Share departures inline”
)
await update.message.reply_text(msg, parse_mode=“Markdown”)

async def cmd_lines(update, context):
text = “*GO Train Line Codes:*\n\n” + “\n”.join(k + “ - “ + v for k, v in LINE_CODES.items())
await update.message.reply_text(text, parse_mode=“Markdown”)

async def cmd_go(update, context):
code = context.args[0].upper() if context.args else “MO”
if code not in LINE_CODES:
await update.message.reply_text(“Unknown line “ + code + “. Use /lines to see options.”)
return
msg = await update.message.reply_text(“Fetching live departures…”)
await msg.edit_text(format_message(code), parse_mode=“Markdown”)

async def cmd_setfav(update, context):
if not context.args:
await update.message.reply_text(“Usage: /setfav MO\n\nUse /lines to see all codes.”)
return
code = context.args[0].upper()
if code not in LINE_CODES:
await update.message.reply_text(“Unknown line “ + code + “. Use /lines.”)
return
set_user(update.effective_user.id, {“favourite”: code})
await update.message.reply_text(“Favourite saved: “ + LINE_CODES[code] + “ (” + code + “)\n\nUse /myfav anytime.”)

async def cmd_myfav(update, context):
user = get_user(update.effective_user.id)
fav = user.get(“favourite”)
if not fav:
await update.message.reply_text(“No favourite saved yet.\n\nUse /setfav MO to save one.”)
return
msg = await update.message.reply_text(“Fetching “ + LINE_CODES[fav] + “ departures…”)
await msg.edit_text(format_message(fav), parse_mode=“Markdown”)

async def cmd_setalert(update, context):
if len(context.args) < 2:
await update.message.reply_text(“Usage: /setalert MO 08:00\n\nSends daily departures at that time.”)
return
code = context.args[0].upper()
time_str = context.args[1]
if code not in LINE_CODES:
await update.message.reply_text(“Unknown line “ + code + “. Use /lines.”)
return
try:
alert_time = datetime.strptime(time_str, “%H:%M”).time()
except ValueError:
await update.message.reply_text(“Invalid time. Use HH:MM format e.g. 08:00”)
return

```
user_id = update.effective_user.id
chat_id = update.effective_chat.id
job_name = "alert_" + str(user_id)

for job in context.job_queue.get_jobs_by_name(job_name):
    job.schedule_removal()

context.job_queue.run_daily(
    send_alert,
    time=alert_time,
    name=job_name,
    data={"user_id": user_id, "chat_id": chat_id, "line_code": code},
)
set_user(user_id, {"alert_line": code, "alert_time": time_str, "alert_chat": chat_id})
await update.message.reply_text(
    "Daily alert set!\n\nLine: " + LINE_CODES[code] + " (" + code + ")\nTime: " + time_str + " every day\n\nUse /cancelalert to stop."
)
```

async def cmd_cancelalert(update, context):
user_id = update.effective_user.id
job_name = “alert_” + str(user_id)
jobs = context.job_queue.get_jobs_by_name(job_name)
if not jobs:
await update.message.reply_text(“You do not have an active alert.”)
return
for job in jobs:
job.schedule_removal()
set_user(user_id, {“alert_line”: None, “alert_time”: None})
await update.message.reply_text(“Daily alert cancelled.”)

async def cmd_mystatus(update, context):
user = get_user(update.effective_user.id)
fav        = user.get(“favourite”)
alert_line = user.get(“alert_line”)
alert_time = user.get(“alert_time”)
fav_str    = fav + “ - “ + LINE_CODES.get(fav, “?”) if fav else “Not set”
alert_str  = alert_line + “ at “ + alert_time + “ daily” if alert_line and alert_time else “Not set”
await update.message.reply_text(
“*Your GO Train Bot settings:*\n\n”
“Favourite line: “ + fav_str + “\n”
“Daily alert: “ + alert_str + “\n\n”
“Change with /setfav or /setalert”,
parse_mode=“Markdown”,
)

async def inline_query(update, context):
query = update.inline_query.query.strip().upper()
results = []

```
if query and query not in LINE_CODES:
    results.append(InlineQueryResultArticle(
        id=str(uuid4()),
        title="Unknown line code",
        description="Try: " + " ".join(LINE_CODES.keys()),
        input_message_content=InputTextMessageContent(
            "Unknown line " + query + ". Valid codes: " + " ".join(LINE_CODES.keys())
        ),
    ))
    await update.inline_query.answer(results, cache_time=5)
    return

lines_to_show = {query: LINE_CODES[query]} if query else LINE_CODES

for code, name in lines_to_show.items():
    results.append(InlineQueryResultArticle(
        id=str(uuid4()),
        title=name + " (" + code + ")",
        description="Tap to share live Union Station departures",
        input_message_content=InputTextMessageContent(
            format_message(code),
            parse_mode="Markdown",
        ),
    ))

await update.inline_query.answer(results, cache_time=30)
```

def main():
token = os.environ.get(“BOT_TOKEN”)
if not token:
print(“ERROR: Set BOT_TOKEN environment variable.”)
sys.exit(1)

```
app = ApplicationBuilder().token(token).build()
app.add_handler(InlineQueryHandler(inline_query))
app.add_handler(CommandHandler("start",       cmd_start))
app.add_handler(CommandHandler("help",        cmd_help))
app.add_handler(CommandHandler("lines",       cmd_lines))
app.add_handler(CommandHandler("go",          cmd_go))
app.add_handler(CommandHandler("setfav",      cmd_setfav))
app.add_handler(CommandHandler("myfav",       cmd_myfav))
app.add_handler(CommandHandler("setalert",    cmd_setalert))
app.add_handler(CommandHandler("cancelalert", cmd_cancelalert))
app.add_handler(CommandHandler("mystatus",    cmd_mystatus))

logger.info("Bot running!")
app.run_polling(allowed_updates=["message", "inline_query"])
```

if **name** == “**main**”:
main()
