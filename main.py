import os, re, shutil, time, asyncio, random, subprocess, json
from datetime import datetime, timedelta
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

# --- STATE MANAGEMENT ---
DB = {"users": {}, "active": {}, "banned": set()}
CANCEL_GROUPS = set()

app = Client("dl_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, sleep_threshold=120)

# --- UTILS ---
def save_db():
    with open(DB_FILE, "w") as f:
        json.dump({"users": DB["users"], "active": DB["active"], "banned": list(DB["banned"])}, f, default=str)

def load_db():
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                data = json.load(f)
                DB["users"] = data.get("users", {})
                DB["active"] = data.get("active", {})
                DB["banned"] = set(data.get("banned", []))
        except: pass

def get_user(uid):
    uid = str(uid)
    if uid not in DB["users"]:
        DB["users"][uid] = {"used": 0, "warnings": 0, "is_paid": False, "thumb": None}
    return DB["users"][uid]

def check_nsfw(info):
    """Detects 18+ keywords and age limits"""
    trigger_words = ["porn", "xxx", "sex", "nude", "hentai", "xvideo", "erotic", "adult"]
    title = info.get("title", "").lower()
    desc = info.get("description", "").lower() or ""
    age_limit = info.get("age_limit", 0)
    if age_limit >= 18: return True
    return any(word in title or word in desc for word in trigger_words)

# --- KEYBOARDS ---
def get_main_btns(uid):
    btns = [[types.InlineKeyboardButton("❓ Help", callback_data="help"), types.InlineKeyboardButton("🆔 My ID", callback_data="my_id")]]
    if uid == OWNER_ID:
        btns.append([types.InlineKeyboardButton("📊 Reports", callback_data="adm_reports"), types.InlineKeyboardButton("📢 Broadcast", callback_data="adm_bc")])
    else:
        btns.append([types.InlineKeyboardButton("💎 Upgrade to Paid", url=CONTACT_URL)])
    return types.InlineKeyboardMarkup(btns)

# --- MAIN HANDLERS ---
@app.on_message(filters.command("start") & filters.private)
async def start_cmd(_, m):
    uid = m.from_user.id
    if uid in DB["banned"]: return await m.reply("🚫 You are permanently banned for NSFW violations.")
    msg = random.choice(ADMIN_GREETINGS if uid == OWNER_ID else USER_GREETINGS)
    await m.reply(msg, reply_markup=get_main_btns(uid))

@app.on_message(filters.text & ~filters.command(["start", "broadcast", "paid", "unban"]) & filters.private)
async def handle_text(client, m):
    uid = m.from_user.id
    uid_str = str(uid)
    if uid in DB["banned"]: return await m.reply("🚫 Your account is banned.")
    
    user = get_user(uid)

    if m.text.startswith("http"):
        status_msg = await m.reply("🔍 Analyzing Content...")
        
        ydl_opts = {'quiet': True, 'no_warnings': True}
        if os.path.exists("cookies.txt"): ydl_opts['cookiefile'] = "cookies.txt"

        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(m.text, download=False)
                
                # NSFW Strike System
                if uid != OWNER_ID and not user.get("is_paid", False):
                    if check_nsfw(info):
                        user["warnings"] += 1
                        save_db()
                        
                        if user["warnings"] >= 4:
                            DB["banned"].add(uid)
                            save_db()
                            return await status_msg.edit("🚫 **Final Strike.** You have been permanently banned for attempting to download 18+ content.")
                        
                        warn_msg = (
                            f"🔞 **NSFW Warning ({user['warnings']}/3)**\n\n"
                            "18+ content is strictly prohibited for free users. "
                            "Please do not attempt this again or you will be banned.\n\n"
                            "ℹ️ **Paid Members** have no restrictions and can download any content. "
                            "Contact @poocha to upgrade."
                        )
                        return await status_msg.edit(warn_msg)

                # Proceed if clean or paid
                size = info.get('filesize') or info.get('filesize_approx') or 0
                DB["active"][uid_str] = {"url": m.text, "status": "choosing", "size": size}
                save_db()
                btns = [[types.InlineKeyboardButton(f"Video ({size})", callback_data="dl_vid")],
                        [types.InlineKeyboardButton("Cancel", callback_data="cancel")]]
                await status_msg.edit("Choose format:", reply_markup=types.InlineKeyboardMarkup(btns))
        except Exception as e:
            await status_msg.edit(f"❌ Error: {str(e)[:50]}")

# --- ADMIN COMMANDS ---
@app.on_message(filters.command("paid") & filters.user(OWNER_ID))
async def set_paid(_, m):
    try:
        target = m.command[1]
        get_user(target)["is_paid"] = True
        save_db()
        await m.reply(f"💎 User `{target}` is now a Paid Member (No restrictions).")
    except: await m.reply("Use: `/paid ID`")

@app.on_message(filters.command("unban") & filters.user(OWNER_ID))
async def unban_user(_, m):
    try:
        target = int(m.command[1])
        if target in DB["banned"]: DB["banned"].remove(target)
        if str(target) in DB["users"]: DB["users"][str(target)]["warnings"] = 0
        save_db()
        await m.reply(f"✅ User `{target}` unbanned and warnings reset.")
    except: await m.reply("Use: `/unban ID`")

# --- STARTUP ---
async def main():
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    load_db()
    await app.start()
    # Auto-web-server for Koyeb
    server = web.Application(); server.add_routes([web.get('/', lambda r: web.Response(text="Bot Alive"))])
    runner = web.AppRunner(server); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', 8000).start()
    await idle()

if __name__ == "__main__":
    app.run(main())
