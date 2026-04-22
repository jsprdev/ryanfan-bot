"""Template arrays for bot messages. Edit freely and restart the bot."""

# Placeholders available: {next_slot}, {topic}, {members}
# {next_slot} is formatted like "19:30 Review" or "—" if no slot is upcoming.
# {topic} is just the topic name or "" if no slot is upcoming.
# {members} is a comma-joined roster string, or "" if no members are set.
REMINDER_TEMPLATES = [
    # "⏰ Lock in, {members}. Next up: {next_slot}.",
    # "📚 Focus check — next block is {next_slot}. Don't slack.",
    # "🎯 {members}, stay on it. Coming up: {topic} at {next_slot}.",
    # "🔥 Heads down, {members}. {next_slot} is right around the corner.",
    # "⚡ Keep the momentum. Next: {next_slot}.",
    "Hey guys, its Ryan Fan here. Just a reminder, in the last 10 minutes, I've already studied 20 chapters across 3 mods, finished 5 tutorials and re-did them after going for 3 consultations with my prof, and also watched all of the supplementary videos provided by the course. Lock in dawg.",
    "What's up gang, it's Jeremy here. Keep on studying, I am a archi bum and I am probably still sleeping after getting drunk on the streets so you are doing a good job bro."
]

# No placeholders — plain strings used as the slacker message header.
SLACKER_PROMPTS = [
    "😤 Who's slacking?",
    "🚨 Slacker check — name and shame.",
    "👀 Who's not pulling their weight right now?",
    "📢 Call out the slacker in the group.",
    "🔎 Scanning for slackers… who is it?",
]
