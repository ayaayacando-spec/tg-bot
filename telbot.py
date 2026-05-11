# file: botcastle_v18_final.py

import os
import sqlite3
import secrets
import urllib.parse
import logging
import aiohttp
import asyncio
from datetime import datetime, timedelta
from aiohttp import web

from aiohttp import web  # ✅ NEW
from dotenv import load_dotenv
load_dotenv()

from aiogram import Bot, Dispatcher, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, BotCommand

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID"))

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot)

db = sqlite3.connect("bot.db")
db.row_factory = sqlite3.Row
cur = db.cursor()

# ───────── STATE SYSTEM ─────────
user_state = {}
active_uploads = {}

def now():
    return datetime.utcnow()

# ───────── AUTO DELETE ─────────
async def auto_delete(chat_id, message_ids, delay=1800):
    await asyncio.sleep(delay)
    for mid in message_ids:
        try:
            await bot.delete_message(chat_id, mid)
        except:
            continue

# ───────── WEB SERVER (NEW) ─────────
async def handle(request):
    return web.Response(text="Bot running")

async def start_web():
    app = web.Application()
    app.router.add_get("/", handle)

    runner = web.AppRunner(app)
    await runner.setup()

    port = int(os.getenv("PORT", 10000))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()

# ───────── ANTI SLEEP (NEW) ─────────
async def anti_sleep():
    url = f"http://localhost:{os.getenv('PORT', 10000)}"
    while True:
        try:
            async with aiohttp.ClientSession() as s:
                await s.get(url)
        except:
            pass
        await asyncio.sleep(300)

# ───────── DB ─────────
cur.executescript("""
CREATE TABLE IF NOT EXISTS contents (
    content_id TEXT PRIMARY KEY,
    created_at TEXT,
    clicks INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content_id TEXT,
    file_id TEXT
);

CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS tokens (
    token TEXT PRIMARY KEY,
    user_id INTEGER,
    used INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS channels (
    channel_id INTEGER,
    invite_link TEXT
);

CREATE TABLE IF NOT EXISTS global_verification (
    user_id INTEGER PRIMARY KEY,
    verified_at TEXT
);

CREATE TABLE IF NOT EXISTS shorteners (
    name TEXT PRIMARY KEY,
    api_key TEXT,
    active INTEGER DEFAULT 0
);
""")
db.commit()

# ───────── COMMAND MENU ─────────
async def set_commands():
    await bot.set_my_commands([
        BotCommand("start", "Start bot")
    ])

# ───────── ADMIN PANEL ─────────
def admin_panel():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("📤 Upload", callback_data="admin_upload"),
        InlineKeyboardButton("📊 Stats", callback_data="admin_stats"),
        InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"),
        InlineKeyboardButton("➕ Add Channel", callback_data="admin_add_channel"),
        InlineKeyboardButton("❌ Remove Channel", callback_data="admin_remove_channel"),
        InlineKeyboardButton("🔗 Shortener", callback_data="admin_shortener"),
    )
    return kb

# ───────── SHORTENER COMMANDS ─────────
@dp.message_handler(commands=["addshort"])
async def add_short(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("❌ Not allowed")

    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        return await message.answer("Usage: /addshort name API_KEY")

    cur.execute("INSERT OR REPLACE INTO shorteners VALUES (?, ?, 0)", (parts[1], parts[2]))
    db.commit()
    await message.answer("✅ Added")

@dp.message_handler(commands=["setshort"])
async def set_short(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("❌ Not allowed")

    name = message.get_args().strip()
    if not name:
        return await message.answer("Usage: /setshort name")

    cur.execute("UPDATE shorteners SET active=0")
    cur.execute("UPDATE shorteners SET active=1 WHERE name=?", (name,))
    db.commit()
    await message.answer("✅ Active")

# ───────── STATS COMMAND ─────────
@dp.message_handler(commands=["stats"])
async def stats(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return await message.answer("❌ Not allowed")

    users = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    contents = cur.execute("SELECT COUNT(*) FROM contents").fetchone()[0]
    verified = cur.execute("SELECT COUNT(*) FROM global_verification").fetchone()[0]
    total_short = cur.execute("SELECT COUNT(*) FROM shorteners").fetchone()[0]

    active = cur.execute("SELECT name FROM shorteners WHERE active=1").fetchone()
    active_name = active["name"] if active else "None"

    await message.answer(
        f"📊 STATS\n\n"
        f"👤 Users: {users}\n"
        f"📦 Contents: {contents}\n"
        f"✅ Verified: {verified}\n\n"
        f"🔗 Shorteners: {total_short}\n"
        f"⚡ Active: {active_name}"
    )

# ───────── SHORTENER ─────────
async def generate_short_link(url):
    row = cur.execute("SELECT * FROM shorteners WHERE active=1").fetchone()
    if not row:
        return None

    api = row["api_key"]
    short_url = f"https://shrinkme.io/api?api={api}&url={urllib.parse.quote_plus(url)}"

    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(short_url) as r:
                data = await r.json()
                if data.get("status") == "success":
                    return data["shortenedUrl"]
    except:
        return None

# ───────── VERIFY ─────────
def is_verified(uid):
    row = cur.execute("SELECT verified_at FROM global_verification WHERE user_id=?", (uid,)).fetchone()
    if not row:
        return False
    return now() <= datetime.fromisoformat(row["verified_at"]) + timedelta(hours=12)

def verify_token(token, uid):
    row = cur.execute("SELECT * FROM tokens WHERE token=? AND user_id=?", (token, uid)).fetchone()
    if not row or row["used"]:
        return False

    cur.execute("UPDATE tokens SET used=1 WHERE token=?", (token,))
    cur.execute("REPLACE INTO global_verification VALUES (?, ?)", (uid, now().isoformat()))
    db.commit()
    return True

# ───────── CHANNEL CHECK ─────────
async def check_all_channels(uid):
    channels = cur.execute("SELECT * FROM channels").fetchall()
    missing = []

    for ch in channels:
        try:
            m = await bot.get_chat_member(ch["channel_id"], uid)
            if m.status not in ("member", "creator", "administrator"):
                missing.append(ch["invite_link"])
        except:
            missing.append(ch["invite_link"])

    if missing:
        kb = InlineKeyboardMarkup()
        for link in missing:
            kb.add(InlineKeyboardButton("📢 Join Channel", url=link))
        return False, kb

    return True, None

# ───────── SEND CONTENT ─────────
async def send_content(uid, cid):
    files = cur.execute("SELECT file_id FROM files WHERE content_id=?", (cid,)).fetchall()
    sent_ids = []

    for f in files:
        for fn in [
            bot.send_document,
            bot.send_photo,
            bot.send_video,
            bot.send_audio,
            bot.send_voice,
            bot.send_animation,
        ]:
            try:
                msg = await fn(uid, f["file_id"])
                sent_ids.append(msg.message_id)
                break
            except:
                continue

    if sent_ids:
        asyncio.create_task(auto_delete(uid, sent_ids, 1800))

# ───────── START ─────────
@dp.message_handler(commands=["start"])
async def start(message: types.Message):
    uid = message.from_user.id

    cur.execute("INSERT OR IGNORE INTO users VALUES (?, ?)", (uid, now().isoformat()))
    db.commit()

    args = message.get_args()

    if not args:
        if uid == ADMIN_ID:
            return await message.answer("👑 Admin Panel", reply_markup=admin_panel())
        return await message.answer("Bot active ✅")

    if args.startswith("content_"):
        cid = args.split("_")[1]

        ok, kb = await check_all_channels(uid)
        if not ok:
            return await message.answer("Join channels:", reply_markup=kb)

        if not is_verified(uid):
            token = secrets.token_hex(8)
            cur.execute("DELETE FROM tokens WHERE user_id=?", (uid,))
            cur.execute("INSERT INTO tokens VALUES (?, ?, 0)", (token, uid))
            db.commit()

            bot_info = await bot.get_me()
            target = f"https://t.me/{bot_info.username}?start=verify_{token}"
            short = await generate_short_link(target)

            if not short:
                return await message.answer("⚠️ Shortener error")

            kb = InlineKeyboardMarkup().add(
                InlineKeyboardButton("🔓 Unlock Content", url=short)
            )
            return await message.answer("🔒 Unlock content", reply_markup=kb)

        return await send_content(uid, cid)

    elif args.startswith("verify_"):
        token = args.split("_")[1]
        if verify_token(token, uid):
            await message.answer("✅ Verified (12h)")
        else:
            await message.answer("❌ Invalid")

# ───────── ADMIN PANEL CALLBACK ─────────
@dp.callback_query_handler(lambda c: c.data.startswith("admin_"))
async def admin_cb(callback: types.CallbackQuery):
    await callback.answer()  # ✅ FIX: respond instantly

    uid = callback.from_user.id
    if uid != ADMIN_ID:
        return

    data = callback.data

    if data == "admin_upload":
        user_state[uid] = "upload"
        active_uploads[uid] = []
        await callback.message.answer("📤 Send files then /done")

    elif data == "admin_broadcast":
        user_state[uid] = "broadcast"
        await callback.message.answer("📢 Send message/media")

    elif data == "admin_add_channel":
        user_state[uid] = "add_channel"
        await callback.message.answer("Send @channel")

    elif data == "admin_remove_channel":
        user_state[uid] = "remove_channel"
        rows = cur.execute("SELECT * FROM channels").fetchall()
        txt = "\n".join([str(r["channel_id"]) for r in rows]) or "No channels"
        await callback.message.answer(txt + "\nSend ID")

    elif data == "admin_stats":
        users = cur.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        contents = cur.execute("SELECT COUNT(*) FROM contents").fetchone()[0]
        verified = cur.execute("SELECT COUNT(*) FROM global_verification").fetchone()[0]
        total_short = cur.execute("SELECT COUNT(*) FROM shorteners").fetchone()[0]

        active = cur.execute("SELECT name FROM shorteners WHERE active=1").fetchone()
        active_name = active["name"] if active else "None"

        await callback.message.answer(
            f"📊 STATS\n\n"
            f"👤 Users: {users}\n"
            f"📦 Contents: {contents}\n"
            f"✅ Verified: {verified}\n\n"
            f"🔗 Shorteners: {total_short}\n"
            f"⚡ Active: {active_name}"
        )

    elif data == "admin_shortener":
        await callback.message.answer("/addshort name api\n/setshort name")
# ───────── STATE HANDLER ─────────
@dp.message_handler(lambda m: not m.text or not m.text.startswith("/"), content_types=types.ContentTypes.ANY)
async def state_handler(message: types.Message):
    uid = message.from_user.id
    if uid != ADMIN_ID:
        return

    state = user_state.get(uid)

    if state == "upload":
        file_id = None
        if message.document: file_id = message.document.file_id
        elif message.photo: file_id = message.photo[-1].file_id
        elif message.video: file_id = message.video.file_id
        elif message.animation: file_id = message.animation.file_id

        if file_id:
            active_uploads[uid].append(file_id)
            await message.answer("➕ Added")
        return

    if state == "broadcast":
        users = cur.execute("SELECT user_id FROM users").fetchall()
        for u in users:
            try:
                await message.copy_to(u["user_id"])
            except:
                pass
        user_state.pop(uid)
        await message.answer("✅ Broadcast sent")
        return

    if state == "add_channel":
        try:
            chat = await bot.get_chat(message.text.strip())
            cur.execute("INSERT INTO channels VALUES (?, ?)", (chat.id, f"https://t.me/{chat.username}"))
            db.commit()
            await message.answer("✅ Channel added")
        except:
            await message.answer("❌ Failed (bot must be admin)")
        user_state.pop(uid)
        return

    if state == "remove_channel":
        try:
            cur.execute("DELETE FROM channels WHERE channel_id=?", (int(message.text),))
            db.commit()
            await message.answer("✅ Removed")
        except:
            await message.answer("❌ Invalid")
        user_state.pop(uid)
        return

# ───────── DONE ─────────
@dp.message_handler(commands=["done"])
async def done(message: types.Message):
    uid = message.from_user.id
    if uid != ADMIN_ID:
        return

    files = active_uploads.get(uid, [])
    if not files:
        return await message.answer("❌ No files")

    user_state.pop(uid, None)

    cid = secrets.token_hex(6)
    cur.execute("INSERT INTO contents VALUES (?, ?, 0)", (cid, now().isoformat()))

    for f in files:
        cur.execute("INSERT INTO files (content_id, file_id) VALUES (?, ?)", (cid, f))

    db.commit()

    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=content_{cid}"

    await message.answer(f"✅ Upload complete\n\n{link}")

# ───────── RUN (UPDATED) ─────────
async def main():
    await set_commands()
    await start_web()
    asyncio.create_task(anti_sleep())
    await dp.start_polling()

if __name__ == "__main__":
    asyncio.run(main())
