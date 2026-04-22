# ryanfan-bot

Minimal study accountability Telegram bot for a group chat.

## Features

- **Named schedule presets** — save one as `CLB-FOCUS`, another as `EXAM-WEEK`, swap between them.
- **Session mode** — `/session` activates the loaded schedule; the bot sends periodic reminders + slacker alerts until `/end_session`. Silent otherwise.
- **Slacker alert** — one message with an inline button per member; tapping a name edits the *same* message to "X is slacking! Called out by Y". Auto-fires every N hours during a session.
- **Periodic reminders** — random template from `messages.py`, showing the next upcoming slot.
- **Minimal messaging** — every interaction edits the existing message in place when possible.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# open .env and paste your BOT_TOKEN from @BotFather
python bot.py
```

Add the bot to a group chat. In the group, disable privacy mode on the bot (message @BotFather → `/setprivacy` → disable) so it can see commands, or make it an admin.

## Tuning

Edit the two constants at the top of [bot.py](bot.py):

```python
REMINDER_INTERVAL_MINUTES = 30     # how often periodic reminders fire
SLACKER_INTERVAL_HOURS = 2         # how often slacker alerts auto-pop
```

Edit the template arrays in [messages.py](messages.py) to change the bot's voice. Placeholders in `REMINDER_TEMPLATES`: `{next_slot}`, `{topic}`, `{members}`.

## Commands

| Command | Effect |
|---|---|
| `/start` or `/help` | Show help |
| `/setmembers Alice, Bob, Charlie` | Set the roster |
| `/members` | Show roster |
| `/setschedule <name>` + body | Save & load a schedule preset |
| `/load <name>` | Load a saved preset |
| `/schedules` | List saved presets |
| `/delete <name>` | Remove a preset |
| `/schedule` | Show the loaded schedule |
| `/session` | Start today's plan (reminders + slacker start firing) |
| `/end_session` | Stop everything |
| `/slacker` | Post a manual slacker alert |

Schedule body format — one slot per line, `HH:MM Topic`:

```
/setschedule CLB-FOCUS
09:00 Math
13:00 Physics
19:30 Review
```

## Deployment on Render

- Service type: **Background Worker** (not Web Service — no HTTP port needed for polling).
- Build command: `pip install -r requirements.txt`
- Start command: `python bot.py`
- Env var: `BOT_TOKEN=<your token>`
- Optional: mount a persistent disk and set `STATE_FILE=/var/data/bot_state.pkl` so member/schedule state survives deploys.

Long polling keeps a single outbound connection to Telegram — no public URL or webhook setup required.

## Files

- [bot.py](bot.py) — entry point, Application builder, polling
- [handlers.py](handlers.py) — all command + callback + job handlers
- [scheduler.py](scheduler.py) — pure schedule parser + slot helpers
- [messages.py](messages.py) — template arrays (edit-and-restart)
