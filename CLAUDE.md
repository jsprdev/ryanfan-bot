# ryanfan-bot — Minimal Study Accountability Telegram Bot

## Context

The repo is empty (only `README.md` + `.gitignore`). We're building a Telegram bot for a study group chat with this mental model:

- **Schedule** = a list of time-slots for the day (`HH:MM Topic`, no day-of-week).
- **Members** = plain strings (names only, not linked to Telegram accounts).
- **Session** = *activating* today's schedule. While a session is active, the bot fires reminders at each upcoming slot time AND auto-posts a "slacker alert" every 2 hours.

Design principle: **minimal messages**. The bot never posts when editing an existing message works, and it never confirms commands with extra chatter. Every feature has exactly one message per logical event, mutated in place via `edit_message_text` when users tap buttons.

Key clarifications from your answers:
1. Members are plain strings — `/setmembers Alice, Bob, Charlie`. No username tracking, no auto-detection.
2. Schedule is **daily** (just `HH:MM Topic` per line), and schedules are **saved under names** (e.g. `CLB-FOCUS`, `EXAM-WEEK`) so you can load a preset later instead of re-typing.
3. `/session` runs the **currently-loaded** named schedule. It skips any slots already in the past and only schedules reminders for slots **later than now**.
4. Auto-slacker posts a **new** message every 2h while the session is active (keeps historical call-outs in scrollback).
5. Deployment: **webhook** (needs public HTTPS URL / tunnel). Persistence: **`PicklePersistence`** (zero-setup).

## Stack

- `python-telegram-bot[job-queue]` v21+
- Python 3.11+
- No database, no other deps

## Project structure

```
ryanfan-bot/
├── bot.py              # entry: Application + handlers + run_webhook()
├── handlers.py         # command + CallbackQuery handlers
├── scheduler.py        # parse schedule text, register/cancel JobQueue jobs
├── messages.py         # template arrays for reminder + slacker texts (user-editable)
├── requirements.txt    # python-telegram-bot[job-queue]
├── .env.example
└── README.md           # updated with run instructions
```

Four Python files total. `messages.py` is the only "content" file — the user edits it to change the bot's voice; no command needed.

## Per-chat state (`context.chat_data`, auto-persisted)

```python
{
  "members": ["Alice", "Bob", "Charlie"],

  "schedules": {                              # named presets, saved across sessions
      "CLB-FOCUS": {
          "slots": [
              {"hour": 9,  "minute": 0,  "topic": "Math"},
              {"hour": 13, "minute": 0,  "topic": "Physics"},
              {"hour": 19, "minute": 30, "topic": "Review"},
          ],
          "raw": "09:00 Math\n13:00 Physics\n19:30 Review",
      },
      "EXAM-WEEK": { ... },
  },
  "current_schedule": "CLB-FOCUS",            # name of the loaded preset, or None

  "active_session": {                         # present only while session is live
      "message_id": 123,                       # the /session status message (edited on end)
      "started_at_iso": "2026-04-23T14:05:00",
      "schedule_name": "CLB-FOCUS",            # which preset this session is running
      "job_names": [                           # so /end_session can cancel them all
          "reminder:<chat_id>:1",              # only slots whose time > session start
          "reminder:<chat_id>:2",
          "slacker:<chat_id>",
          "auto_end:<chat_id>",
      ],
  },
}
```

Slacker messages don't need persistent state — each one's state (which name was tapped, who called out) is encoded in its rendered text and button layout; the reset button reconstructs the initial state from `chat_data["members"]`.

## Commands

| Command | Effect |
|---|---|
| `/start` | One-time help. Single message. |
| `/setmembers Alice, Bob, Charlie` | Overwrites roster. One ack. |
| `/members` | Shows roster. One message. |
| `/setschedule <name>` (multi-line body) | Parses `HH:MM Topic` per line, **saves** under `<name>`, and sets it as current. One ack. |
| `/load <name>` | Makes `<name>` the current schedule. One ack. |
| `/schedules` | Lists all saved schedule names; marks the current one with ➤. One message. |
| `/delete <name>` | Removes a saved schedule. Clears `current_schedule` if it was current. One ack. |
| `/schedule` | Shows the current loaded schedule (name + slots). One message. |
| `/session` | **Starts** today's plan using the current schedule. Blocks if already active. Posts one status message. |
| `/end_session` | Cancels all session jobs. Edits the session message to terminal state. No new message. |
| `/slacker` | Manual slacker alert. Posts a new slacker message. |

### Schedule format

```
/setschedule CLB-FOCUS
09:00 Math
13:00 Physics
19:30 Review
```

Parser in `scheduler.py`:
- First token after `/setschedule` is the preset name (required; reject if missing).
- Remaining lines: trim, skip blanks, regex `^(\d{1,2}):(\d{2})\s+(.+)$`.
- Store as `chat_data["schedules"][name] = {"slots": [...], "raw": "..."}` and set `chat_data["current_schedule"] = name`.
- Overwriting an existing name is allowed (ack says "Updated `CLB-FOCUS` (3 slots)" vs "Saved `CLB-FOCUS` (3 slots)").
- On parse error, edit-reply "Could not parse line N: `<line>`" (no partial save).
- **Does not** register JobQueue jobs by itself — only `/session` does that.

### `/load <name>`

Sets `chat_data["current_schedule"] = name` if the name exists; errors otherwise. Does not auto-start a session — you still need `/session` to begin.

## `/session` behavior

1. Require `chat_data["current_schedule"]` to be set AND present in `chat_data["schedules"]`; else reply "No schedule loaded. Use `/setschedule <name>` or `/load <name>` first."
2. If `active_session` already present → reply "Session already active (`<schedule_name>`). Use /end_session first." (one message).
3. Read slots from `chat_data["schedules"][current_schedule]["slots"]`.
4. Build list of **future-today** slots: for each slot, compute today's datetime at `HH:MM`; keep only those `> now`.
5. For each future slot idx `i`: `job_queue.run_once(fire_slot_reminder, when=slot_dt, data={...}, name=f"reminder:{chat_id}:{i}")`.
6. `job_queue.run_repeating(fire_auto_slacker, interval=2*3600, first=2*3600, data={"chat_id": chat_id}, name=f"slacker:{chat_id}")` — first tick 2h after session start.
7. `job_queue.run_once(auto_end_session, when=<today at 23:59>, data={"chat_id": chat_id}, name=f"auto_end:{chat_id}")` — safety net so a forgotten session doesn't leak slacker pings into the small hours.
8. Post the **session status message**:
   ```
   📚 Session started 14:05 (CLB-FOCUS)
   Upcoming today: 19:30 Review
   [🛑 End session]
   ```
   (If no future slots exist — e.g. user started after the last slot — post "No slots left today, but slacker alerts will still run. [🛑 End session]".)
9. Save `message_id` + `job_names` + `started_at_iso` + `schedule_name` into `chat_data["active_session"]`.

### `/end_session` / auto-end / `[🛑 End session]` button

All three paths call the same `end_session(chat_id, context)` helper:
1. Cancel every job in `active_session["job_names"]` (via `job_queue.get_jobs_by_name(...)` + `.schedule_removal()`).
2. Edit the session status message in place:
   ```
   📚 Session ended — 14:05 → 20:43 (6h 38m)
   ```
   Remove the inline keyboard (`reply_markup=None`).
3. Delete `chat_data["active_session"]`.

No new messages are sent in the end path.

## Slacker message (manual `/slacker` AND auto every 2h)

Both entry points call the same `post_slacker(chat_id, context)` helper that sends:
```
😤 Who's slacking?
[Alice] [Bob] [Charlie]
```
Callback data:
- `sl:<idx>` — name at index idx was called out
- `sl:r` — reset to initial state

Tapping `sl:2` → edit the **same** message to:
```
😤 Charlie is slacking!
Called out by Ryan
[↩ Reset]
```
Caller name = `update.callback_query.from_user.first_name`.

Tapping `sl:r` → edit back to the initial "Who's slacking?" state (rebuilt from `chat_data["members"]` at the time of the tap — so if members changed, the reset reflects that).

Auto-slacker (every 2h while session active) just calls `post_slacker` and creates a NEW message — prior slacker messages stay as scrollback history.

Edit guard: wrap `edit_message_text` in `try/except BadRequest` and swallow `"Message is not modified"`.

## Scheduled reminder (one per slot)

Fires at slot time. Single message:
```
📚 Reminder: Math starts now.
Members: Alice, Bob, Charlie
```
No inline keyboard, no follow-up edits. Pure notification.

## Restart recovery

`PicklePersistence` restores `chat_data` on startup but **not** JobQueue jobs — known PTB gotcha. In `bot.py`'s startup hook:
- Iterate `app.chat_data` items.
- For any chat with `active_session`, re-register remaining jobs:
  - Reminders for slots whose datetime is still in the future.
  - The 2h slacker repeater (aligned to the original session start where possible).
  - The 23:59 auto-end job.
- If `active_session.started_at_iso` is from a previous day, treat the session as stale and clear it (don't resume across days).

## Minimal-messages audit

Every bot-originated message enumerated:
1. `/start` → 1 greet (single use)
2. `/setmembers`, `/members`, `/setschedule`, `/schedule` → 1 ack each
3. `/session` → **1** persistent status message (edited on end)
4. `/end_session` → **0** new messages (edits the session message)
5. `/slacker` → 1 new message per invocation (manual OR auto)
6. Scheduled reminder → 1 per slot at slot time

No per-command confirmations, no ephemeral "done!" replies, no duplicated state across messages. All button taps edit the message they came from.

## File-level specifics

### `bot.py`
- Load env (`BOT_TOKEN`, `WEBHOOK_URL`, `PORT`, `WEBHOOK_SECRET`).
- `persistence = PicklePersistence(filepath="bot_state.pkl")`.
- `app = ApplicationBuilder().token(...).persistence(persistence).post_init(restore_jobs).build()`.
- Register command handlers + one `CallbackQueryHandler` routed by prefix (`sl:...`, `se:end` for session-end button).
- `app.run_webhook(listen="0.0.0.0", port=PORT, url_path=WEBHOOK_SECRET, webhook_url=f"{WEBHOOK_URL}/{WEBHOOK_SECRET}", secret_token=WEBHOOK_SECRET)`.

### `handlers.py`
- `cmd_start`, `cmd_set_members`, `cmd_members`, `cmd_set_schedule`, `cmd_schedule`, `cmd_session`, `cmd_end_session`, `cmd_slacker`.
- `on_callback(update, context)` — dispatcher by prefix.
- Helpers: `_render_slacker_initial(members)`, `_render_slacker_called(name, caller)`, `post_slacker(chat_id, context)`, `end_session(chat_id, context)`.

### `scheduler.py`
- `parse_schedule(text) -> list[Slot] | ParseError`.
- `fire_slot_reminder(context)` — reads `context.job.data`, sends one reminder message.
- `fire_auto_slacker(context)` — calls `handlers.post_slacker`.
- `auto_end_session(context)` — calls `handlers.end_session`.
- `restore_jobs(app)` — post-init hook, re-registers jobs for any active session.

### `requirements.txt`
```
python-telegram-bot[job-queue]>=21.0
```

### `.env.example`
```
BOT_TOKEN=replace-me
WEBHOOK_URL=https://your.public.host
PORT=8443
WEBHOOK_SECRET=a-random-string
```

## Webhook prereqs (reminder)

You'll need a public HTTPS URL — options: VPS + reverse proxy, Fly.io / Render / Railway, or `cloudflared tunnel --url http://localhost:8443` / `ngrok http 8443` for dev.

## Verification

End-to-end test list:
1. `pip install -r requirements.txt`; fill `.env`; start a tunnel.
2. `python bot.py` — confirm "Application started" log.
3. Add bot to a Telegram group. Run, in order:
   - `/setmembers Alice, Bob, Charlie` → single ack.
   - `/setschedule` + multi-line body (include a slot a couple of minutes from now, and one already passed) → ack with parsed table.
   - `/session` → status message lists only the **future** slot. Past slot is silently skipped ✓.
   - Wait for the near-future slot — confirm reminder fires once.
   - `/slacker` → new message appears. Tap a name → same message edits to "X is slacking! Called out by <you>". Tap Reset → back to initial.
   - For the 2h auto test: temporarily lower the interval to 60s in `scheduler.py`, confirm a new slacker message appears, then revert to 2h.
   - Tap `[🛑 End session]` → session message edits to "ended" with duration; keyboard gone.
4. Restart the bot mid-session (after a fresh `/session`) — confirm `active_session` persists and future reminders still fire (job restoration works).
5. Confirm no extraneous messages anywhere in the transcript.

## Non-goals (explicitly out of scope)

- @-mentioning real Telegram users (members are strings).
- Stats, streaks, leaderboards.
- DMs, per-user state, cross-chat features.
- Weekly recurring schedules (daily only for MVP).
- Natural-language schedule parsing beyond `HH:MM Topic`.
