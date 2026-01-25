import os, re, shutil, time, asyncio, random, subprocess, json
from datetime import datetime
from pyrogram import Client, filters, types, enums, idle, errors
from yt_dlp import YoutubeDL
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiohttp import web

# --- CONFIG ---
OWNER_ID = 519459195 
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", 0)) 
INVITE_LINK = "https://t.me/+eooytvOAwjc0NTI1"
CONTACT_URL = "https://t.me/poocha"
DOWNLOAD_DIR = "downloads"
THUMB_DIR = "thumbnails"
DB_FILE = "tasks.json"

USER_GREETINGS = ["Thanks for chatting with me.", "Glad you’re here.", "Appreciate you using this bot."]
ADMIN_GREETINGS = ["Chief, systems are ready.", "Ready when you are, chief.", "Standing by."]

DB = {"users": {}, "active": {}, "banned": set()}
app = Client("dl_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, sleep_threshold=120)

# --- UTILS ---
def get_user(uid):
    uid = str(uid)
    if uid not in DB["users"]:
        DB["users"][uid] = {"used": 0, "is_paid": False, "warnings": 0, "thumb": None}
    return DB["users"][uid]

def is_pro(uid):
    user = get_user(uid)
    return uid == OWNER_ID or user.get("is_paid", False)

def format_size(size):
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024: return f"{size:.2f}{unit}"
        size /= 1024
    return f"{size:.2f}TB"

async def is_subscribed(uid):
    if uid == OWNER_ID: return True
    try:
        m = await app.get_chat_member(CHANNEL_ID, uid)
        return m.status in [enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]
    except: return False

# --- KEYBOARDS ---
def get_main_btns(uid):
    btns = [[types.InlineKeyboardButton("❓ Help", callback_data="help"), 
             types.InlineKeyboardButton("🆔 My ID", callback_data="my_id")]]
    if not is_pro(uid):
        btns.append([types.InlineKeyboardButton("💎 Upgrade to Pro", url=CONTACT_URL)])
    if uid == OWNER_ID:
        btns.append([types.InlineKeyboardButton("📊 Reports", callback_data="adm_reports")])
    return types.InlineKeyboardMarkup(btns)

def get_download_menu(uid, is_playlist=False):
    """Dynamically restricts buttons based on Pro status"""
    pro = is_pro(uid)
    btns = [[types.InlineKeyboardButton("Video 🎥", callback_data="dl_vid"),
             types.InlineKeyboardButton("Audio 🎵", callback_data="dl_aud")]]
    
    if pro:
        # Paid Features
        btns.append([types.InlineKeyboardButton("Convert MP4 📱", callback_data="op_convert"),
                     types.InlineKeyboardButton("Watermark 🏷", callback_data="op_brand")])
        btns.append([types.InlineKeyboardButton("Trim Clip ✂️", callback_data="op_trim")])
        if is_playlist:
            btns.append([types.InlineKeyboardButton("📦 ZIP Playlist", callback_data="op_zip")])
    
    btns.append([types.InlineKeyboardButton("Rename ✏️", callback_data="rename"),
                 types.InlineKeyboardButton("Cancel ❌", callback_data="cancel")])
    return types.InlineKeyboardMarkup(btns)

# --- COMMANDS ---
@app.on_message(filters.command("start") & filters.private)
async def start_cmd(_, m):
    uid = m.from_user.id
    msg = random.choice(ADMIN_GREETINGS if uid == OWNER_ID else USER_GREETINGS)
    await m.reply(msg, reply_markup=get_main_btns(uid))

@app.on_message(filters.command("status") & filters.private)
async def status_cmd(_, m):
    uid = m.from_user.id
    user = get_user(uid)
    status = "💎 PRO Member" if is_pro(uid) else "🆓 Free User"
    
    pro_list = (
        "✅ Auto-Split (Files > 2GB)\n"
        "✅ Custom Watermarking\n"
        "✅ High-Speed MP4 Conversion\n"
        "✅ Smart Video Trimming\n"
        "✅ Playlist to ZIP Support"
    ) if is_pro(uid) else (
        "❌ Auto-Split (Files > 2GB)\n"
        "❌ Custom Watermarking\n"
        "❌ High-Speed MP4 Conversion\n"
        "❌ Smart Video Trimming\n"
        "❌ Playlist to ZIP Support"
    )
    
    text = (f"📊 **Account: {status}**\n\n"
            f"Daily Usage: `{format_size(user['used'])}` / 15GB\n"
            f"NSFW Strikes: `{user.get('warnings', 0)}/3`\n\n"
            f"✨ **Pro Feature Access:**\n{pro_list}\n\n"
            "Contact @poocha to unlock Pro features.")
    await m.reply(text)

@app.on_message(filters.text & ~filters.command(["start", "status"]) & filters.private)
async def handle_text(client, m):
    uid = m.from_user.id
    if not await is_subscribed(uid):
        return await m.reply("⚠️ Join channel first.", reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("Join", url=INVITE_LINK)]]))

    if m.text.startswith("http"):
        status_msg = await m.reply("🔍 Analyzing...")
        try:
            with YoutubeDL({'quiet': True}) as ydl:
                info = ydl.extract_info(m.text, download=False)
                is_list = 'entries' in info
                DB["active"][str(uid)] = {"url": m.text, "status": "choosing"}
                await status_msg.edit(f"✅ Found: {info.get('title')[:50]}", 
                                    reply_markup=get_download_menu(uid, is_list))
        except: await status_msg.edit("❌ Link Error or YouTube Block.")

@app.on_callback_query()
async def cb_handler(client, cb: types.CallbackQuery):
    uid = cb.from_user.id
    uid_str = str(uid)
    data = cb.data
    
    if data == "help":
        help_msg = (
            "📖 **How to use:**\n"
            "1. Send any media link.\n"
            "2. Choose format.\n"
            "3. Rename or Upload.\n\n"
            "💡 **Pro Users** get access to Watermarking, Converting, and Trimming buttons in the download menu!"
        )
        return await cb.message.edit(help_msg, reply_markup=get_main_btns(uid))

    if data == "my_id": return await cb.answer(f"ID: {uid}", show_alert=True)

    if data.startswith("op_"):
        if not is_pro(uid):
            return await cb.answer("💎 Upgrade to Pro to use this feature!", show_alert=True)
        # Process Pro commands (FFmpeg) here...
        await cb.answer("Processing Pro Request...")

    elif data.startswith("dl_"):
        await cb.message.edit("⏳ Downloading...")
        # Download logic here...
        await cb.message.edit("✅ Ready.", reply_markup=get_download_menu(uid))

    elif data == "cancel":
        DB["active"].pop(uid_str, None)
        await cb.message.edit("❌ Cancelled.")

# --- STARTUP ---
async def main():
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    await app.start()
    # Koyeb health check server
    server = web.Application(); server.add_routes([web.get('/', lambda r: web.Response(text="Bot Alive"))])
    runner = web.AppRunner(server); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', 8000).start()
    print("Bot is Started Successfully")
    await idle()

if __name__ == "__main__":
    app.run(main())
