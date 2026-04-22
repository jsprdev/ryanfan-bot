"""Entry point. Edit the two constants below to tune how often the bot nags."""
from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    PicklePersistence,
)

import handlers

load_dotenv()  # reads ./.env into os.environ (no-op on hosts like Render where env vars are already set)


# ---- Tune these, then restart -------------------------------------------------
REMINDER_INTERVAL_MINUTES = 120      # how often the periodic reminder fires during a session
SLACKER_INTERVAL_HOURS = 2          # how often the auto slacker alert fires during a session
# -------------------------------------------------------------------------------


logging.basicConfig(
    format="%(asctime)s %(name)s %(levelname)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("bot")


def main() -> None:
    token = os.environ.get("BOT_TOKEN")
    if not token:
        raise SystemExit("BOT_TOKEN env var is required.")

    handlers.configure(REMINDER_INTERVAL_MINUTES, SLACKER_INTERVAL_HOURS)

    persistence = PicklePersistence(filepath=os.environ.get("STATE_FILE", "bot_state.pkl"))

    app = (
        ApplicationBuilder()
        .token(token)
        .persistence(persistence)
        .post_init(handlers.restore_jobs)
        .build()
    )

    app.add_handler(CommandHandler("start", handlers.cmd_start))
    app.add_handler(CommandHandler("help", handlers.cmd_start))
    app.add_handler(CommandHandler("setmembers", handlers.cmd_set_members))
    app.add_handler(CommandHandler("members", handlers.cmd_members))
    app.add_handler(CommandHandler("setschedule", handlers.cmd_set_schedule))
    app.add_handler(CommandHandler("load", handlers.cmd_load))
    app.add_handler(CommandHandler("schedules", handlers.cmd_schedules))
    app.add_handler(CommandHandler("delete", handlers.cmd_delete))
    app.add_handler(CommandHandler("schedule", handlers.cmd_schedule))
    app.add_handler(CommandHandler("session", handlers.cmd_session))
    app.add_handler(CommandHandler("end_session", handlers.cmd_end_session))
    app.add_handler(CommandHandler("slacker", handlers.cmd_slacker))

    app.add_handler(CallbackQueryHandler(handlers.on_callback))

    log.info("Starting bot (polling)…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
