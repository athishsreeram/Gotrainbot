# Gotrainbot



GO Train Union Station Departures — Full-Featured Telegram Bot

Features:

- Inline mode: @YourBot MO in any chat
- Saved favourite line per user
- Scheduled daily alerts before their train
- Works in personal chat & group chats

Setup:
pip install -r requirements.txt

In @BotFather:
/setinline      → enable inline mode
/setcommands    → paste the command list below

Commands to paste in BotFather /setcommands:
start - Welcome & setup guide
go - Get departures (e.g. /go MO)
setfav - Save your favourite line (e.g. /setfav MO)
myfav - Get your saved favourite line departures
setalert - Set a daily departure alert (e.g. /setalert MO 08:00)
cancelalert - Cancel your daily alert
mystatus - Show your saved preferences
lines - List all line codes
help - Help

Run:
BOT_TOKEN=<your_token> python go_train_bot.py

```
Deploy on Render (same as before)
Push these 2 files to GitHub, then on Render:
	∙	Type: Background Worker
	∙	Build: pip install -r requirements.txt
	∙	Start: python go_train_bot.py
	∙	Env var: BOT_TOKEN=your_token

```

```
@YourBot          → see all 7 lines to pick from
@YourBot MO       → Milton line departures
@YourBot LE       → Lakeshore East
@YourBot BR       → Barrie

```
