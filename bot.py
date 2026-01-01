import asyncio
import time
import os
import json
import aiosqlite
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command, CommandStart
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import FSInputFile
from dotenv import load_dotenv


# ─────────────────────────
# ENV
# ─────────────────────────
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_ID = int(os.getenv("GROUP_ID"))

bot = Bot(BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# ─────────────────────────
# QUESTION CONFIG (EDIT ONLY THIS)
# ─────────────────────────
QUESTIONS = [
    {
        "key": "experience",
        "text": "What is your experience level?",
        "options": ["Beginner", "Intermediate", "Advanced"]
    },
    {
        "key": "interest",
        "text": "What are you most interested in?",
        "options": ["Trading", "Investing", "Learning"]
    },
    {
        "key": "time",
        "text": "How much time can you dedicate weekly?",
        "options": ["< 5 hrs", "5–10 hrs", "10+ hrs"]
    },
    {
        "key": "rules",
        "text": "Do you agree to follow the group rules?",
        "options": ["Yes", "No"]
    }
]

# ─────────────────────────
# HELPERS
# ─────────────────────────
async def is_group_admin(user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(GROUP_ID, user_id)
        return member.status in ["administrator", "creator"]
    except:
        return False

# ─────────────────────────
# FSM
# ─────────────────────────
class Form(StatesGroup):
    index = State()

# ─────────────────────────
# USER FLOW
# ─────────────────────────
@dp.message(CommandStart())
async def start(message: types.Message, state: FSMContext):
    async with aiosqlite.connect("velto.db") as db:
        cur = await db.execute(
            "SELECT joined FROM users WHERE user_id = ?",
            (message.from_user.id,)
        )
        row = await cur.fetchone()

    if row:
        try:
            member = await bot.get_chat_member(GROUP_ID, message.from_user.id)
            if member.status in ["member", "administrator", "creator"]:
                await message.answer("✅ You are already in Velto.")
                return
        except:
            pass

    await state.set_state(Form.index)
    await state.update_data(index=0, answers={})
    await ask_question(message, state)

async def ask_question(message, state):
    data = await state.get_data()
    idx = data["index"]

    if idx >= len(QUESTIONS):
        await finish(message, state)
        return

    q = QUESTIONS[idx]
    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text=o, callback_data=f"ans:{o}")]
            for o in q["options"]
        ]
    )

    await message.answer(q["text"], reply_markup=keyboard)

@dp.callback_query(lambda c: c.data.startswith("ans:"))
async def answer(callback: types.CallbackQuery, state: FSMContext):
    choice = callback.data.replace("ans:", "")
    data = await state.get_data()

    idx = data["index"]
    answers = data["answers"]
    answers[QUESTIONS[idx]["key"]] = choice

    await state.update_data(index=idx + 1, answers=answers)
    await callback.answer()
    await ask_question(callback.message, state)

async def finish(message, state):
    data = await state.get_data()

    async with aiosqlite.connect("velto.db") as db:
        await db.execute("""
        INSERT OR REPLACE INTO users
        (user_id, username, answers, joined, created_at)
        VALUES (?, ?, ?, ?, ?)
        """, (
            message.from_user.id,
            message.from_user.username,
            json.dumps(data["answers"]),
            0,
            int(time.time())
        ))
        await db.commit()

    invite = await bot.create_chat_invite_link(
        chat_id=GROUP_ID,
        member_limit=1,
        expire_date=int(time.time()) + 600
    )

    await message.answer(
        "🎉 Approved!\n\n"
        "Here is your private invite link:\n\n"
        f"{invite.invite_link}"
    )

    await state.clear()

# ─────────────────────────
# JOIN / LEAVE TRACKING
# ─────────────────────────
@dp.chat_member()
async def join_leave(event: types.ChatMemberUpdated):
    if event.chat.id != GROUP_ID:
        return

    user = event.new_chat_member.user

    async with aiosqlite.connect("velto.db") as db:
        if event.new_chat_member.status == "member":
            await db.execute(
                "UPDATE users SET joined = 1 WHERE user_id = ?",
                (user.id,)
            )
            await db.commit()

            await bot.send_message(
                GROUP_ID,
                f"🎉 Welcome {user.mention_html()} to Velto!",
                parse_mode="HTML"
            )

        elif event.new_chat_member.status in ["left", "kicked"]:
            await db.execute(
                "UPDATE users SET joined = 0 WHERE user_id = ?",
                (user.id,)
            )
            await db.commit()

# ─────────────────────────
# ADMIN DASHBOARD
# ─────────────────────────
@dp.message(Command("admin"))
async def admin_menu(message: types.Message):
    if not await is_group_admin(message.from_user.id):
        return

    keyboard = types.InlineKeyboardMarkup(
        inline_keyboard=[
            [types.InlineKeyboardButton(text="📊 Stats", callback_data="admin:stats")],
            [types.InlineKeyboardButton(text="📁 Export CSV", callback_data="admin:export")]
        ]
    )
    await message.answer("🛠 Admin Dashboard", reply_markup=keyboard)

@dp.callback_query(lambda c: c.data == "admin:stats")
async def admin_stats(callback: types.CallbackQuery):
    if not await is_group_admin(callback.from_user.id):
        return

    async with aiosqlite.connect("velto.db") as db:
        c1 = await db.execute("SELECT COUNT(*) FROM users")
        total = (await c1.fetchone())[0]
        c2 = await db.execute("SELECT COUNT(*) FROM users WHERE joined = 1")
        joined = (await c2.fetchone())[0]

    await callback.message.answer(
        f"📊 Stats\n\nTotal users: {total}\nJoined: {joined}"
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "admin:export")
async def admin_export(callback: types.CallbackQuery):
    if not await is_group_admin(callback.from_user.id):
        return

    async with aiosqlite.connect("velto.db") as db:
        cursor = await db.execute(
            "SELECT user_id, username, answers, joined, created_at FROM users"
        )
        rows = await cursor.fetchall()

    file_name = "velto_users.csv"

    with open(file_name, "w", encoding="utf-8") as f:
        f.write("user_id,username,answers,joined,created_at\n")
        for r in rows:
            f.write(",".join(map(str, r)) + "\n")

    await callback.message.answer_document(
        types.FSInputFile(file_name)
    )
    await callback.answer()


# ─────────────────────────
# MAIN
# ─────────────────────────
async def main():
    async with aiosqlite.connect("velto.db") as db:
        await db.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            answers TEXT,
            joined BOOLEAN,
            created_at INTEGER
        )
        """)
        await db.commit()

    await dp.start_polling(bot)
# ─────────────────────────
# RENDER FREE TIER KEEP-ALIVE (HTTP DUMMY SERVER)
# ─────────────────────────
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Velto bot is running")

def start_dummy_server():
    port = int(os.getenv("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), DummyHandler)
    server.serve_forever()

threading.Thread(target=start_dummy_server, daemon=True).start()

if __name__ == "__main__":
    asyncio.run(main())

