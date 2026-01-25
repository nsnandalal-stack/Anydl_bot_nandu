import os, re, shutil, time, asyncio, random, subprocess
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
DAILY_LIMIT = 15 * 1024 * 1024 * 1024 
COOKIES_FILE = "cookies.txt"

# --- ROTATING MESSAGES ---
USER_GREETINGS = [
    "Thanks for chatting with me.", "Glad you’re here.", 
    "Appreciate you using this bot.", "Happy to help you today.", 
    "Let me know how I can assist."
]
ADMIN_GREETINGS = [
    "Chief, systems are ready.", "Ready when you are, chief.", 
    "All set. What’s the move?", "Standing by for instructions.", 
    "Let’s begin, chief."
]

# --- DB & STATE ---
DB = {"users": {}, "active": {}, "banned": set()}
CANCEL_GROUPS = set()

app = Client("dl_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, sleep_threshold=120)

# --- WEB SERVER (Koyeb Health) ---
async def health_check(request):
    return web.Response(text="Bot Alive")

# --- UTILS ---
def get_user(uid):
    today = datetime.now().date()
    if uid not in DB["users"]:
        DB["users"][uid] = {"used": 0, "last_reset": today}
    if DB["users"][uid]["last_reset"] != today:
        DB["users"][uid].update({"used": 0, "last_reset": today})
    return DB["users"][uid]

def format_size(size):
    if not size: return "0B"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024: return f"{size:.2f}{unit}"
        size /= 1024
    return f"{size:.2f}TB"

async def is_subscribed(uid):
    if uid == OWNER_ID: return True
    try:
        member = await app.get_chat_member(CHANNEL_ID, uid)
        return member.status in [enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]
    except: return False

async def progress_hook(current, total, msg, start_time, action):
    if msg.chat.id in CANCEL_GROUPS: raise Exception("USER_CANCEL")
    now = time.time()
    if not hasattr(msg, "last_up"): msg.last_up = 0
    if (now - msg.last_up) < 10: return 
    msg.last_up = now
    try:
        p = current * 100 / total
        bar = "✅" * int(p/10) + "⬜" * (10 - int(p/10))
        await msg.edit(f"⏳ {action}...\n`{bar}` {p:.1f}%\n📦 {format_size(current)} / {format_size(total)}")
    except: pass

async def take_screenshots(video_path, uid):
    output_dir = os.path.join(DOWNLOAD_DIR, f"screens_{uid}")
    if not os.path.exists(output_dir): os.makedirs(output_dir)
    try:
        cmd = f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{video_path}"'
        duration = float(subprocess.check_output(cmd, shell=True))
        screens = []
        for i in range(1, 11):
            time_pos = (duration / 11) * i
            out_path = os.path.join(output_dir, f"thumb_{i}.jpg")
            subprocess.call(['ffmpeg', '-ss', str(time_pos), '-i', video_path, '-vframes', '1', '-q:v', '2', out_path, '-y'], stderr=subprocess.DEVNULL)
            if os.path.exists(out_path): screens.append(types.InputMediaPhoto(out_path))
        return screens
    except: return []

# --- KEYBOARDS ---
def get_admin_main():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("📊 Reports", callback_data="adm_reports"),
         types.InlineKeyboardButton("💾 Disk Usage", callback_data="adm_disk")],
        [types.InlineKeyboardButton("⌨️ Commands", callback_data="adm_cmds"),
         types.InlineKeyboardButton("📢 Broadcast", callback_data="adm_bc")]
    ])

def get_ready_btns():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("Upload ⬆️", callback_data="up_normal"),
         types.InlineKeyboardButton("Upload + 📸", callback_data="up_screen")],
        [types.InlineKeyboardButton("Rename ✏️", callback_data="rename"),
         types.InlineKeyboardButton("Cancel ❌", callback_data="cancel")]
    ])

# --- COMMANDS ---
@app.on_message(filters.command("start"))
async def start_cmd(_, m):
    uid = m.from_user.id
    if uid == OWNER_ID:
        await m.reply(random.choice(ADMIN_GREETINGS), reply_markup=get_admin_main())
    else:
        btn = types.InlineKeyboardMarkup([[types.InlineKeyboardButton("💖 Donate / Contact", url=CONTACT_URL)]])
        await m.reply(random.choice(USER_GREETINGS), reply_markup=btn)

@app.on_message(filters.command("status"))
async def status_cmd(_, m):
    uid = m.from_user.id
    user = get_user(uid)
    limit = "Unlimited" if uid == OWNER_ID else "15.00GB"
    await m.reply(f"📊 **Memory Usage**\n\nUsed Today: `{format_size(user['used'])}` / `{limit}`\n\nTo upgrade, contact @poocha")

@app.on_message(filters.command("admin") & filters.user(OWNER_ID))
async def admin_cmd(_, m):
    await m.reply("🛠 **Admin Control Center**", reply_markup=get_admin_main())

@app.on_message(filters.command("ban") & filters.user(OWNER_ID))
async def ban_handler(_, m):
    try:
        target = int(m.command[1])
        DB["banned"].add(target)
        await m.reply(f"🚫 User `{target}` banned.")
    except: await m.reply("Use: `/ban ID`")

@app.on_message(filters.command("broadcast") & filters.user(OWNER_ID))
async def bc_handler(_, m):
    if len(m.command) < 2: return
    text = m.text.split(None, 1)[1]
    count = 0
    for u in DB["users"]:
        try:
            await app.send_message(u, f"📢 **Announcement**\n\n{text}")
            count += 1
        except: pass
    await m.reply(f"✅ Sent to {count} users.")

# --- MAIN LOGIC ---
@app.on_message(filters.text)
async def handle_text(client, m):
    uid = m.from_user.id
    if uid in DB["banned"]: return
    user = get_user(uid)

    if uid in DB["active"] and DB["active"][uid].get("status") == "renaming":
        state = DB["active"][uid]
        ext = os.path.splitext(state["path"])[1]
        new_name = m.text if m.text.endswith(ext) else f"{m.text}{ext}"
        new_path = os.path.join(DOWNLOAD_DIR, new_name)
        os.rename(state["path"], new_path)
        DB["active"][uid].update({"path": new_path, "name": new_name, "status": "ready"})
        return await m.reply(f"✅ Renamed.", reply_markup=get_ready_btns())

    if m.text.startswith("http"):
        if not await is_subscribed(uid):
            btn = types.InlineKeyboardMarkup([[types.InlineKeyboardButton("Join Channel", url=INVITE_LINK)]])
            return await m.reply("⚠️ You must join the channel to use this bot.", reply_markup=btn)
        
        if uid != OWNER_ID and user["used"] >= DAILY_LIMIT:
            btn = types.InlineKeyboardMarkup([[types.InlineKeyboardButton("💖 Upgrade / Donate", url=CONTACT_URL)]])
            return await m.reply("❌ Daily 15GB limit reached.", reply_markup=btn)

        CANCEL_GROUPS.discard(uid)
        status_msg = await m.reply("🔍 Analyzing...")
        ydl_opts = {'quiet': True, 'extractor_args': {'youtube': {'player_client': ['ios', 'android']}}}
        if os.path.exists(COOKIES_FILE): ydl_opts['cookiefile'] = COOKIES_FILE

        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(m.text, download=False)
                size = info.get('filesize') or info.get('filesize_approx') or 0
                DB["active"][uid] = {"url": m.text, "time": time.time(), "name": info.get('title', 'file'), "size": size}
                
                if uid != OWNER_ID:
                    await app.send_message(OWNER_ID, f"👁️ **New Download:**\nUID: `{uid}`\nFile: `{info.get('title')}`\nSize: {format_size(size)}")

                btns = [[types.InlineKeyboardButton(f"Video ({format_size(size)})", callback_data="dl_vid")],
                        [types.InlineKeyboardButton("Audio (MP3)", callback_data="dl_aud")],
                        [types.InlineKeyboardButton("Cancel", callback_data="cancel")]]
                await status_msg.edit("Choose format:", reply_markup=types.InlineKeyboardMarkup(btns))
        except Exception as e: await status_msg.edit(f"❌ Error: {str(e)[:50]}")

@app.on_callback_query()
async def cb_handler(client, cb: types.CallbackQuery):
    uid = cb.from_user.id
    data = cb.data

    if data.startswith("adm_") and uid == OWNER_ID:
        if data == "adm_reports":
            t, u, f = shutil.disk_usage("/")
            await cb.message.edit(f"📊 **Reports**\n\nTotal Users: {len(DB['users'])}\nDisk: {format_size(u)}/{format_size(t)}", reply_markup=get_admin_main())
        elif data == "adm_disk":
            await cb.answer(f"Free Space: {format_size(shutil.disk_usage('/').free)}", show_alert=True)
        elif data == "adm_cmds":
            await cb.message.edit("⌨️ `/ban ID`\n`/broadcast TEXT`\n`/status`", reply_markup=get_admin_main())
        return

    if data == "cancel":
        CANCEL_GROUPS.add(uid)
        DB["active"].pop(uid, None)
        return await cb.message.edit("❌ Session Cancelled.")

    if uid not in DB["active"]: return await cb.answer("Expired.")
    state = DB["active"][uid]

    if data.startswith("dl_"):
        await cb.message.edit("⏳ Downloading...")
        is_vid 
