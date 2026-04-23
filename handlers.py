"""All command handlers, callback handlers, and JobQueue callbacks.

Kept in one file so job callbacks can freely call command helpers without
circular imports against scheduler.py (which stays pure / Telegram-free).
"""
from __future__ import annotations

import logging
import random
import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import ContextTypes

import messages
import scheduler

log = logging.getLogger(__name__)

SGT = ZoneInfo("Asia/Singapore")


# --- config is injected by bot.py at startup -----------------------------------
# Set by bot.py before handlers run so tests can also set them directly.
REMINDER_INTERVAL_MINUTES: float = 30
SLACKER_INTERVAL_HOURS: float = 2
SLOT_WARNING_MINUTES: int = 10
ROLLCALL_STATS_DELAY_MINUTES: int = 30
ROLLCALL_GRACE_MINUTES: int = 5


def configure(reminder_minutes: float, slacker_hours: float, slot_warning_minutes: int = 10) -> None:
    global REMINDER_INTERVAL_MINUTES, SLACKER_INTERVAL_HOURS, SLOT_WARNING_MINUTES
    REMINDER_INTERVAL_MINUTES = reminder_minutes
    SLACKER_INTERVAL_HOURS = slacker_hours
    SLOT_WARNING_MINUTES = slot_warning_minutes


# --- small helpers -------------------------------------------------------------

def _members(chat_data: dict) -> list[str]:
    return chat_data.get("members", [])


def _current_slots(chat_data: dict) -> list[dict] | None:
    name = chat_data.get("current_schedule")
    if not name:
        return None
    sched = chat_data.get("schedules", {}).get(name)
    return sched["slots"] if sched else None


async def _safe_edit(query, text: str, reply_markup=None) -> None:
    try:
        await query.edit_message_text(text, reply_markup=reply_markup)
    except BadRequest as e:
        if "Message is not modified" in str(e):
            return
        raise


# --- /start --------------------------------------------------------------------

HELP_TEXT = (
    # "📚 *Study Accountability Bot*\n\n"
    "I am the Ryan Fan Study Bot. I am here to supervise you until you stop slacking because I already finished watching 100 lectures and did 80 tutorials in the past 5 minutes you were scrolling tiktok.\n\n"
)

""" USAGE HELP
"*Setup*\n"
    "• `/setmembers Alice, Bob, Charlie` — set the roster\n"
    "• `/setschedule <name>` + lines of `HH:MM Topic` — save a schedule preset\n"
    "• `/load <name>` — load a saved preset\n"
    "• `/schedules` — list saved presets\n"
    "• `/delete <name>` — remove a preset\n"
    "• `/members`, `/schedule` — view current state\n\n"
    "*Session*\n"
    "• `/session` — start today's plan (reminders + slacker fire periodically)\n"
    "• `/end_session` — stop everything\n"
    "• `/slacker` — post a manual slacker alert"
"""

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.MARKDOWN)


# --- members -------------------------------------------------------------------

async def cmd_set_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    raw = update.message.text.partition(" ")[2].strip()
    if not raw:
        await update.message.reply_text("Usage: `/setmembers Alice, Bob, Charlie`", parse_mode=ParseMode.MARKDOWN)
        return
    names = [n.strip() for n in raw.split(",") if n.strip()]
    if not names:
        await update.message.reply_text("No names found. Use comma-separated names.")
        return
    context.chat_data["members"] = names
    await update.message.reply_text(f"Members: {', '.join(names)}")


async def cmd_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    names = _members(context.chat_data)
    if not names:
        await update.message.reply_text("No members set. Use /setmembers.")
        return
    await update.message.reply_text(f"Members: {', '.join(names)}")


# --- schedule presets ----------------------------------------------------------

async def cmd_set_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    # Strip the command token; first token of the body is the preset name.
    body = text.split(None, 1)[1] if " " in text or "\n" in text else ""
    # Split body into first-token (name) + remainder (slot lines).
    first_line, _, rest = body.partition("\n")
    parts = first_line.strip().split(None, 1)
    if not parts:
        await update.message.reply_text(
            "Usage:\n`/setschedule <name>`\n`09:00 Math`\n`13:00 Physics`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    name = parts[0]
    # If the name line had extra text after it (single-line usage), treat as a slot too.
    extra_first = parts[1] if len(parts) > 1 else ""
    raw_body = (extra_first + "\n" + rest).strip() if extra_first else rest

    try:
        slots, normalised = scheduler.parse_schedule(raw_body)
    except scheduler.ParseError as e:
        await update.message.reply_text(str(e))
        return

    schedules = context.chat_data.setdefault("schedules", {})
    existed = name in schedules
    schedules[name] = {"slots": slots, "raw": normalised}
    context.chat_data["current_schedule"] = name

    verb = "Updated" if existed else "Saved"
    await update.message.reply_text(
        f"{verb} `{name}` ({len(slots)} slot{'s' if len(slots) != 1 else ''}). Loaded as current.",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_load(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    arg = update.message.text.partition(" ")[2].strip()
    if not arg:
        await update.message.reply_text("Usage: `/load <name>`", parse_mode=ParseMode.MARKDOWN)
        return
    schedules = context.chat_data.get("schedules", {})
    if arg not in schedules:
        await update.message.reply_text(f"No schedule named `{arg}`.", parse_mode=ParseMode.MARKDOWN)
        return
    context.chat_data["current_schedule"] = arg
    await update.message.reply_text(f"Loaded `{arg}`.", parse_mode=ParseMode.MARKDOWN)


async def cmd_schedules(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    schedules = context.chat_data.get("schedules", {})
    if not schedules:
        await update.message.reply_text("No saved schedules. Use `/setschedule <name>`.", parse_mode=ParseMode.MARKDOWN)
        return
    current = context.chat_data.get("current_schedule")
    lines = [f"{'➤ ' if name == current else '   '}`{name}` ({len(s['slots'])} slots)" for name, s in schedules.items()]
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    arg = update.message.text.partition(" ")[2].strip()
    if not arg:
        await update.message.reply_text("Usage: `/delete <name>`", parse_mode=ParseMode.MARKDOWN)
        return
    schedules = context.chat_data.get("schedules", {})
    if arg not in schedules:
        await update.message.reply_text(f"No schedule named `{arg}`.", parse_mode=ParseMode.MARKDOWN)
        return
    del schedules[arg]
    if context.chat_data.get("current_schedule") == arg:
        context.chat_data["current_schedule"] = None
    await update.message.reply_text(f"Deleted `{arg}`.", parse_mode=ParseMode.MARKDOWN)


async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name = context.chat_data.get("current_schedule")
    if not name:
        await update.message.reply_text("No schedule loaded. Use `/setschedule <name>` or `/load <name>`.", parse_mode=ParseMode.MARKDOWN)
        return
    sched = context.chat_data["schedules"][name]
    await update.message.reply_text(
        f"*{name}*\n```\n{sched['raw']}\n```",
        parse_mode=ParseMode.MARKDOWN,
    )


# --- session lifecycle ---------------------------------------------------------

def _session_status_text(schedule_name: str, started_at: datetime, slots: list[dict], now: datetime) -> str:
    upcoming = scheduler.next_upcoming_slot(slots, now)
    head = f"📚 Session started {started_at.strftime('%H:%M')} ({schedule_name})"
    if upcoming is None:
        return f"{head}\nNo more slots today — periodic nudges still running."
    return f"{head}\nUpcoming: {scheduler.format_slot(upcoming)}"


def _session_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("🛑 End session", callback_data="se:end")]])


async def cmd_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    chat_data = context.chat_data

    name = chat_data.get("current_schedule")
    if not name or name not in chat_data.get("schedules", {}):
        await update.message.reply_text(
            "No schedule loaded. Use `/setschedule <name>` or `/load <name>` first.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if chat_data.get("active_session"):
        existing = chat_data["active_session"]["schedule_name"]
        await update.message.reply_text(
            f"Session already active (`{existing}`). Use /end_session first.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    slots = chat_data["schedules"][name]["slots"]
    now = datetime.now(SGT)

    # Register jobs.
    jq = context.application.job_queue
    reminder_name = f"reminder:{chat_id}"
    slacker_name = f"slacker:{chat_id}"
    auto_end_name = f"auto_end:{chat_id}"

    _cancel_jobs(jq, [reminder_name, slacker_name, auto_end_name])  # safety

    reminder_sec = REMINDER_INTERVAL_MINUTES * 60
    slacker_sec = SLACKER_INTERVAL_HOURS * 3600
    jq.run_repeating(fire_reminder, interval=reminder_sec, first=reminder_sec,
                     data={"chat_id": chat_id}, name=reminder_name, chat_id=chat_id)
    jq.run_repeating(fire_slacker, interval=slacker_sec, first=slacker_sec,
                     data={"chat_id": chat_id}, name=slacker_name, chat_id=chat_id)

    slot_warn_names = _schedule_slot_jobs(jq, chat_id, slots, now)

    midnight = datetime.combine(now.date(), time(23, 59), tzinfo=SGT)
    # If already past 23:59 (edge case), skip auto-end — manual /end_session still works.
    if midnight > now:
        jq.run_once(auto_end_session, when=midnight,
                    data={"chat_id": chat_id}, name=auto_end_name, chat_id=chat_id)

    status_text = _session_status_text(name, now, slots, now)
    sent = await update.message.reply_text(status_text, reply_markup=_session_keyboard())

    chat_data["active_session"] = {
        "message_id": sent.message_id,
        "started_at_iso": now.isoformat(timespec="seconds"),
        "schedule_name": name,
        "job_names": [reminder_name, slacker_name, auto_end_name, *slot_warn_names],
        "slacker_counts": {},
    }


async def cmd_end_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id
    if not context.chat_data.get("active_session"):
        await update.message.reply_text("No active session.")
        return
    await end_session(chat_id, context)


async def end_session(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_data = context.application.chat_data[chat_id]
    session = chat_data.get("active_session")
    if not session:
        return

    jq = context.application.job_queue
    _cancel_jobs(jq, session["job_names"])

    started = datetime.fromisoformat(session["started_at_iso"])
    if started.tzinfo is None:
        started = started.replace(tzinfo=SGT)
    now = datetime.now(SGT)
    dur = now - started
    dur_str = _format_duration(dur)

    text = f"📚 Session ended — {started.strftime('%H:%M')} → {now.strftime('%H:%M')} ({dur_str})"
    try:
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=session["message_id"], text=text, reply_markup=None,
        )
    except BadRequest as e:
        if "Message is not modified" not in str(e):
            log.warning("Failed to edit session message: %s", e)

    del chat_data["active_session"]


def _format_duration(delta: timedelta) -> str:
    total = int(delta.total_seconds())
    hours, rem = divmod(total, 3600)
    minutes, _ = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _cancel_jobs(job_queue, names: list[str]) -> None:
    for n in names:
        for j in job_queue.get_jobs_by_name(n):
            j.schedule_removal()


def _schedule_slot_jobs(jq, chat_id: int, slots: list[dict], now: datetime) -> list[str]:
    """Schedule a pre-slot warning and an at-slot start message for each future slot."""
    names: list[str] = []
    for i, slot in enumerate(slots):
        slot_dt = datetime.combine(now.date(), time(slot["hour"], slot["minute"]), tzinfo=SGT)
        hh_mm = f"{slot['hour']:02d}:{slot['minute']:02d}"
        payload = {"chat_id": chat_id, "topic": slot["topic"], "hh_mm": hh_mm}

        warn_dt = slot_dt - timedelta(minutes=SLOT_WARNING_MINUTES)
        if warn_dt > now:
            warn_name = f"slot_warn:{chat_id}:{i}"
            jq.run_once(
                fire_slot_warning, when=warn_dt,
                data={**payload, "minutes": SLOT_WARNING_MINUTES},
                name=warn_name, chat_id=chat_id,
            )
            names.append(warn_name)

        if slot_dt > now:
            start_name = f"slot_start:{chat_id}:{i}"
            jq.run_once(
                fire_slot_start, when=slot_dt,
                data=payload,
                name=start_name, chat_id=chat_id,
            )
            names.append(start_name)
    return names


# --- slacker message -----------------------------------------------------------

def _slacker_initial_text() -> str:
    return random.choice(messages.SLACKER_PROMPTS)


def _slacker_initial_markup(members: list[str]) -> InlineKeyboardMarkup:
    # 3 buttons per row keeps names readable on mobile.
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for i, name in enumerate(members):
        row.append(InlineKeyboardButton(name, callback_data=f"sl:{i}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


async def post_slacker(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_data = context.application.chat_data[chat_id]
    members = chat_data.get("members", [])
    if not members:
        await context.bot.send_message(chat_id, "😤 Slacker alert — but no members set. Use /setmembers.")
        return
    await context.bot.send_message(
        chat_id,
        _slacker_initial_text(),
        reply_markup=_slacker_initial_markup(members),
    )


async def cmd_slacker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await post_slacker(update.effective_chat.id, context)


async def cmd_slackercount(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    session = context.chat_data.get("active_session")
    if not session:
        await update.message.reply_text("No active session.")
        return
    members = context.chat_data.get("members", [])
    if not members:
        await update.message.reply_text("No members set.")
        return
    counts: dict[str, int] = session.get("slacker_counts", {})
    rows = sorted(((m, counts.get(m, 0)) for m in members), key=lambda r: (-r[1], r[0]))
    lines = ["😤 Slacker tally (this session):"] + [f"• {m} — {c}" for m, c in rows]
    await update.message.reply_text("\n".join(lines))


# --- rollcall ------------------------------------------------------------------

_ROLLCALL_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def _rollcall_markup(members: list[str], responses: dict[str, str]) -> InlineKeyboardMarkup | None:
    remaining = [(i, n) for i, n in enumerate(members) if n not in responses]
    if not remaining:
        return None
    rows: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for i, name in remaining:
        row.append(InlineKeyboardButton(name, callback_data=f"rc:{i}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    return InlineKeyboardMarkup(rows)


def _render_rollcall_text(active: dict) -> str:
    members: list[str] = active["members_snapshot"]
    responses: dict[str, str] = active.get("responses", {})
    post_dt = _parse_sgt(active["post_dt_iso"])
    head = f"🌅 Wake up nerds, day is gonna be over alr and you have done nothing ({post_dt.strftime('%H:%M')})"
    checked_parts: list[str] = []
    waiting: list[str] = []
    for name in members:
        iso = responses.get(name)
        if iso is None:
            waiting.append(name)
        else:
            t = _parse_sgt(iso).strftime("%H:%M")
            checked_parts.append(f"{name} ({t})")
    lines = [head]
    if checked_parts:
        lines.append("✅ " + ", ".join(checked_parts))
    if waiting:
        lines.append("⏳ " + ", ".join(waiting))
    return "\n".join(lines)


def _parse_sgt(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SGT)
    return dt


async def cmd_rollcall(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    arg = update.message.text.partition(" ")[2].strip()
    m = _ROLLCALL_RE.match(arg)
    if not m:
        await update.message.reply_text("Usage: `/rollcall HH:MM` (24h, fires next day)", parse_mode=ParseMode.MARKDOWN)
        return
    hour, minute = int(m.group(1)), int(m.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        await update.message.reply_text("Invalid time.")
        return

    chat_id = update.effective_chat.id
    now = datetime.now(SGT)
    post_dt = datetime.combine(now.date() + timedelta(days=1), time(hour, minute), tzinfo=SGT)

    jq = context.application.job_queue
    _cancel_jobs(jq, [f"rollcall_post:{chat_id}", f"rollcall_stats:{chat_id}"])
    context.chat_data.pop("active_rollcall", None)

    jq.run_once(
        fire_rollcall_post, when=post_dt,
        data={"chat_id": chat_id}, name=f"rollcall_post:{chat_id}", chat_id=chat_id,
    )
    context.chat_data["pending_rollcall"] = {"post_dt_iso": post_dt.isoformat(timespec="seconds")}
    await update.message.reply_text(f"Rollcall set for {post_dt.strftime('%a %Y-%m-%d %H:%M')}.")


async def fire_rollcall_post(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.data["chat_id"]
    chat_data = context.application.chat_data[chat_id]
    members = list(chat_data.get("members", []))
    chat_data.pop("pending_rollcall", None)

    if not members:
        await context.bot.send_message(chat_id, "🌅 Rollcall time — but no members set. Use /setmembers.")
        return

    now = datetime.now(SGT)
    active = {
        "post_dt_iso": now.isoformat(timespec="seconds"),
        "members_snapshot": members,
        "responses": {},
    }
    text = _render_rollcall_text(active)
    markup = _rollcall_markup(members, {})
    sent = await context.bot.send_message(chat_id, text, reply_markup=markup)
    active["message_id"] = sent.message_id
    chat_data["active_rollcall"] = active

    stats_dt = now + timedelta(minutes=ROLLCALL_STATS_DELAY_MINUTES)
    jq = context.application.job_queue
    jq.run_once(
        fire_rollcall_stats, when=stats_dt,
        data={"chat_id": chat_id}, name=f"rollcall_stats:{chat_id}", chat_id=chat_id,
    )


async def fire_rollcall_stats(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.data["chat_id"]
    chat_data = context.application.chat_data[chat_id]
    active = chat_data.get("active_rollcall")
    if not active:
        return

    post_dt = _parse_sgt(active["post_dt_iso"])
    members: list[str] = active.get("members_snapshot", [])
    responses: dict[str, str] = active.get("responses", {})
    grace = ROLLCALL_GRACE_MINUTES

    lines = [f"📊 Rollcall stats ({post_dt.strftime('%H:%M')} post, {grace} min grace)"]
    for name in members:
        iso = responses.get(name)
        if iso is None:
            lines.append(f"• {name} — did not respond")
            continue
        delay_min = (_parse_sgt(iso) - post_dt).total_seconds() / 60
        if delay_min <= grace:
            lines.append(f"• {name} — on time")
        else:
            lines.append(f"• {name} — {int(round(delay_min))} min late")

    await context.bot.send_message(chat_id, "\n".join(lines))

    try:
        await context.bot.edit_message_reply_markup(
            chat_id=chat_id, message_id=active["message_id"], reply_markup=None,
        )
    except BadRequest:
        pass

    chat_data.pop("active_rollcall", None)


# --- callback dispatcher -------------------------------------------------------

async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    if data.startswith("sl:"):
        await _handle_slacker_cb(query, context, data[3:])
    elif data.startswith("rc:"):
        await _handle_rollcall_cb(query, context, data[3:])
    elif data == "se:end":
        await _handle_session_end_cb(query, context)
    else:
        log.warning("Unknown callback data: %r", data)


async def _handle_rollcall_cb(query, context: ContextTypes.DEFAULT_TYPE, suffix: str) -> None:
    active = context.chat_data.get("active_rollcall")
    if not active:
        await _safe_edit(query, "🌅 Rollcall closed.")
        return
    members: list[str] = active.get("members_snapshot", [])
    try:
        idx = int(suffix)
    except ValueError:
        return
    if not (0 <= idx < len(members)):
        return
    name = members[idx]
    responses = active.setdefault("responses", {})
    if name in responses:
        return  # already checked in; silent
    responses[name] = datetime.now(SGT).isoformat(timespec="seconds")
    await _safe_edit(query, _render_rollcall_text(active), reply_markup=_rollcall_markup(members, responses))


async def _handle_slacker_cb(query, context: ContextTypes.DEFAULT_TYPE, suffix: str) -> None:
    chat_data = context.chat_data
    members = chat_data.get("members", [])

    if suffix == "r":
        if not members:
            await _safe_edit(query, "😤 No members set. Use /setmembers.")
            return
        await _safe_edit(query, _slacker_initial_text(), reply_markup=_slacker_initial_markup(members))
        return

    try:
        idx = int(suffix)
    except ValueError:
        return

    if not (0 <= idx < len(members)):
        await _safe_edit(query, "😤 Member no longer exists.")
        return

    name = members[idx]
    caller = query.from_user.first_name or "someone"
    session = chat_data.get("active_session")
    if session is not None:
        counts = session.setdefault("slacker_counts", {})
        counts[name] = counts.get(name, 0) + 1
    current = query.message.text or ""
    new_text = f"{current}\n{name} slacker - {caller}"
    await _safe_edit(query, new_text, reply_markup=_slacker_initial_markup(members))


async def _handle_session_end_cb(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = query.message.chat_id
    if not context.chat_data.get("active_session"):
        await _safe_edit(query, "📚 Session already ended.")
        return
    await end_session(chat_id, context)


# --- JobQueue callbacks --------------------------------------------------------

async def fire_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.data["chat_id"]
    chat_data = context.application.chat_data[chat_id]

    if not chat_data.get("active_session"):
        return  # safety — shouldn't fire after end, but cheap to guard

    members = chat_data.get("members", [])
    slots = _current_slots(chat_data) or []
    now = datetime.now(SGT)
    upcoming = scheduler.next_upcoming_slot(slots, now)

    if not messages.REMINDER_TEMPLATES:
        return
    template = random.choice(messages.REMINDER_TEMPLATES)
    text = template.format(
        next_slot=scheduler.format_slot(upcoming),
        topic=(upcoming["topic"] if upcoming else ""),
        members=(", ".join(members) if members else ""),
    ).strip()
    if not text:
        return  # user authored an empty template — stay silent
    await context.bot.send_message(chat_id, text)


async def fire_slot_warning(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data
    chat_id = data["chat_id"]
    chat_data = context.application.chat_data[chat_id]
    if not chat_data.get("active_session"):
        return
    if not messages.SLOT_WARNING_TEMPLATES:
        return
    template = random.choice(messages.SLOT_WARNING_TEMPLATES)
    text = template.format(topic=data["topic"], hh_mm=data["hh_mm"], minutes=data["minutes"]).strip()
    if not text:
        return
    await context.bot.send_message(chat_id, text)


async def fire_slot_start(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.job.data
    chat_id = data["chat_id"]
    chat_data = context.application.chat_data[chat_id]
    if not chat_data.get("active_session"):
        return
    if not messages.SLOT_START_TEMPLATES:
        return
    template = random.choice(messages.SLOT_START_TEMPLATES)
    text = template.format(topic=data["topic"], hh_mm=data["hh_mm"]).strip()
    if not text:
        return
    await context.bot.send_message(chat_id, text)


async def fire_slacker(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.data["chat_id"]
    chat_data = context.application.chat_data[chat_id]
    if not chat_data.get("active_session"):
        return
    await post_slacker(chat_id, context)


async def auto_end_session(context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = context.job.data["chat_id"]
    chat_data = context.application.chat_data[chat_id]
    if not chat_data.get("active_session"):
        return
    await end_session(chat_id, context)


# --- startup: re-register jobs for still-valid sessions ------------------------

async def restore_jobs(application) -> None:
    """Post-init hook: PicklePersistence restores chat_data but not JobQueue jobs."""
    jq = application.job_queue
    today = datetime.now(SGT).date()

    for chat_id, chat_data in list(application.chat_data.items()):
        _restore_session_jobs(jq, chat_id, chat_data, today)
        _restore_rollcall_jobs(jq, chat_id, chat_data)


def _restore_session_jobs(jq, chat_id: int, chat_data: dict, today) -> None:
    session = chat_data.get("active_session")
    if not session:
        return

    try:
        started = datetime.fromisoformat(session["started_at_iso"])
    except (KeyError, ValueError):
        del chat_data["active_session"]
        return

    if started.date() != today:
        log.info("Clearing stale session in chat %s (started %s)", chat_id, started)
        del chat_data["active_session"]
        return

    reminder_name = f"reminder:{chat_id}"
    slacker_name = f"slacker:{chat_id}"
    auto_end_name = f"auto_end:{chat_id}"

    reminder_sec = REMINDER_INTERVAL_MINUTES * 60
    slacker_sec = SLACKER_INTERVAL_HOURS * 3600

    jq.run_repeating(fire_reminder, interval=reminder_sec, first=reminder_sec,
                     data={"chat_id": chat_id}, name=reminder_name, chat_id=chat_id)
    jq.run_repeating(fire_slacker, interval=slacker_sec, first=slacker_sec,
                     data={"chat_id": chat_id}, name=slacker_name, chat_id=chat_id)

    now = datetime.now(SGT)
    sched_name = session.get("schedule_name")
    slots = chat_data.get("schedules", {}).get(sched_name, {}).get("slots", [])
    slot_warn_names = _schedule_slot_jobs(jq, chat_id, slots, now)

    midnight = datetime.combine(today, time(23, 59), tzinfo=SGT)
    if midnight > now:
        jq.run_once(auto_end_session, when=midnight,
                    data={"chat_id": chat_id}, name=auto_end_name, chat_id=chat_id)

    session["job_names"] = [reminder_name, slacker_name, auto_end_name, *slot_warn_names]
    log.info("Restored session jobs for chat %s", chat_id)


def _restore_rollcall_jobs(jq, chat_id: int, chat_data: dict) -> None:
    now = datetime.now(SGT)

    pending = chat_data.get("pending_rollcall")
    if pending:
        try:
            post_dt = _parse_sgt(pending["post_dt_iso"])
        except (KeyError, ValueError):
            del chat_data["pending_rollcall"]
        else:
            if post_dt > now:
                jq.run_once(
                    fire_rollcall_post, when=post_dt,
                    data={"chat_id": chat_id}, name=f"rollcall_post:{chat_id}", chat_id=chat_id,
                )
                log.info("Restored pending rollcall for chat %s at %s", chat_id, post_dt)
            else:
                del chat_data["pending_rollcall"]

    active = chat_data.get("active_rollcall")
    if active:
        try:
            post_dt = _parse_sgt(active["post_dt_iso"])
        except (KeyError, ValueError):
            del chat_data["active_rollcall"]
            return
        stats_dt = post_dt + timedelta(minutes=ROLLCALL_STATS_DELAY_MINUTES)
        fire_at = stats_dt if stats_dt > now else now + timedelta(seconds=2)
        jq.run_once(
            fire_rollcall_stats, when=fire_at,
            data={"chat_id": chat_id}, name=f"rollcall_stats:{chat_id}", chat_id=chat_id,
        )
        log.info("Restored active rollcall stats job for chat %s at %s", chat_id, fire_at)
