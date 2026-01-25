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
DB_FILE = "database.json"
QUOTA_LIMIT = 5 * 1024 * 1024 * 1024 

USER_GREETINGS = ["Thanks for chatting with me.", "Glad you’re here.", "Appreciate you using this bot."]
ADMIN_GREETINGS = ["Chief, systems are ready.", "Ready when you are, chief.", "Standing by."]

# --- DATABASE ---
DB = {"users": {}, "active": {}, "banned": [], "history": [], "temp_bc": None}
app = Client("dl_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, sleep_threshold=120)
scheduler = AsyncIOScheduler()

def save_db():
    with open(DB_FILE, "w") as f: json.dump(DB, f, default=str)

def load_db():
    global DB
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f:
                DB = json.load(f)
                if "banned" not in DB: DB["banned"] = []
                if "history" not in DB: DB["history"] = []
        except: pass

def get_user(uid):
    uid = str(uid)
    today = str(datetime.now().date())
    if uid not in DB["users"]:
        DB["users"][uid] = {"used": 0, "last_reset": today, "warnings": 0, "is_pro": False, "thumb": None, "state": "none", "last_task": None}
    if DB["users"][uid].get("last_reset") != today:
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
        m = await app.get_chat_member(CHANNEL_ID, uid)
        return m.status in [enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]
    except: return False

def check_cooldown(uid):
    user = get_user(uid)
    if not user.get("last_task"): return True, 0
    wait_time = 120 if uid == OWNER_ID else 600
    last_t = datetime.fromisoformat(user["last_task"]) if isinstance(user["last_task"], str) else user["last_task"]
    elapsed = (datetime.now() - last_t).total_seconds()
    if elapsed < wait_time: return False, int(wait_time - elapsed)
    return True, 0

async def notify_ready(uid):
    try: await app.send_message(uid, "✅ **Cooldown finished.** Send task now.")
    except: pass

# --- MEDIA LOGIC ---
async def progress_hook(current, total, msg, start_time, action):
    now = time.time()
    if not hasattr(msg, "last_up"): msg.last_up = 0
    if (now - msg.last_up) < 10: return 
    msg.last_up = now
    try:
        p = (current * 100 / total) if total > 0 else 0
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
def get_main_btns(uid):
    user = get_user(uid)
    btns = [[types.InlineKeyboardButton("❓ Help", callback_data="menu_help"), types.InlineKeyboardButton("🆔 My ID", callback_data="menu_id")],
            [types.InlineKeyboardButton("🖼 Thumbnail Manager", callback_data="menu_thumb")]]
    if uid == OWNER_ID: btns.append([types.InlineKeyboardButton("⚙️ Admin Dashboard", callback_data="menu_admin")])
    else: btns.append([types.InlineKeyboardButton("📊 My Status", callback_data="menu_status"), types.InlineKeyboardButton("💎 Upgrade", url=CONTACT_URL)])
    btns.append([types.InlineKeyboardButton("🚪 Exit", callback_data="exit")])
    return types.InlineKeyboardMarkup(btns)

def get_ready_btns():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("Video 🎥", callback_data="up_video"), types.InlineKeyboardButton("File 📄", callback_data="up_file")],
        [types.InlineKeyboardButton("Upload + 📸", callback_data="up_screen"), types.InlineKeyboardButton("Rename ✏️", callback_data="rename")],
        [types.InlineKeyboardButton("Cancel ❌", callback_data="cancel")]
    ])

# --- HANDLERS ---
@app.on_message(filters.command("start") & filters.private)
async def start_cmd(_, m):
    uid = m.from_user.id
    msg = random.choice(ADMIN_GREETINGS if uid == OWNER_ID else USER_GREETINGS)
    await m.reply(msg, reply_markup=get_main_btns(uid))

@app.on_message(filters.text & ~filters.command(["start"]) & filters.private)
async def handle_text(client, m):
    uid, uid_str = m.from_user.id, str(m.from_user.id)
    user = get_user(uid)

    if user["state"] == "pending_bc" and uid == OWNER_ID:
        DB["temp_bc"] = m.text; user["state"] = "none"
        btns = [[types.InlineKeyboardButton("✅ Confirm", callback_data="bc_confirm"), types.InlineKeyboardButton("❌ Stop", callback_data="bc_stop")]]
        return await m.reply(f"📝 **Broadcast Preview:**\n\n{m.text}", reply_markup=types.InlineKeyboardMarkup(btns))

    if uid_str in DB["active"] and DB["active"][uid_str].get("status") == "renaming":
        state = DB["active"][uid_str]
        ext = os.path.splitext(state["path"])[1]
        new_name = m.text if m.text.endswith(ext) else f"{m.text}{ext}"
        new_path = os.path.join(DOWNLOAD_DIR, new_name)
        os.rename(state["path"], new_path); DB["active"][uid_str].update({"path": new_path, "name": new_name, "status": "ready"})
        return await m.reply("✅ Renamed.", reply_markup=get_ready_btns())

    if m.text.startswith("http"):
        if not await is_subscribed(uid): return await m.reply("⚠️ Join channel first.", reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("Join", url=INVITE_LINK)], [types.InlineKeyboardButton("🔄 Verify", callback_data="verify_sub")]]))
        can_run, wait = check_cooldown(uid)
        if not can_run: return await m.reply(f"⏳ Cooldown: Wait {wait}s.")
        if user["used"] >= QUOTA_LIMIT and uid != OWNER_ID: return await m.reply("❌ 5GB Quota Finished.", reply_markup=get_main_btns(uid))

        status_msg = await m.reply("🔍 Analyzing...")
        try:
            with YoutubeDL({'quiet': True}) as ydl:
                info = ydl.extract_info(m.text, download=False)
                size = info.get('filesize_approx') or info.get('filesize') or 0
                if "youtube.com" in m.text or "youtu.be" in m.text:
                    DB["active"][uid_str] = {"url": m.text, "status": "choosing", "size": size}
                    btns = [[types.InlineKeyboardButton(f"Video ({format_size(size)})", callback_data="dl_vid")],
                            [types.InlineKeyboardButton("Audio (MP3)", callback_data="dl_aud"), types.InlineKeyboardButton("Cancel", callback_data="cancel")]]
                    return await status_msg.edit("🎬 YouTube: Select Format", reply_markup=types.InlineKeyboardMarkup(btns))
                
                # Non-YouTube Logic (Downloads immediately as per workflow)
                await status_msg.edit("⏳ Downloading...")
                path = ydl.prepare_filename(info); ydl.download([m.text])
                DB["active"][uid_str] = {"path": path, "name": os.path.basename(path), "status": "ready", "size": size}
                DB["history"].append({"uid": uid, "name": info.get('title', 'file')[:30], "size": size}); save_db()
                await status_msg.edit("✅ Ready.", reply_markup=get_ready_btns())
        except: await status_msg.edit("❌ Error.")

@app.on_callback_query()
async def cb_handler(client, cb: types.CallbackQuery):
    uid, uid_str = cb.from_user.id, str(cb.from_user.id)
    data, user = cb.data, get_user(cb.from_user.id)
    await cb.answer()

    if data == "verify_sub":
        if await is_subscribed(uid): return await cb.message.edit("✅ Verified!", reply_markup=get_main_btns(uid))
        else: return await cb.answer("❌ Not joined yet!", show_alert=True)

    if data == "bc_confirm" and uid == OWNER_ID:
        msg, count = DB.get("temp_bc"), 0
        for u in DB["users"]:
            try: await app.send_message(int(u), f"📢 **Broadcast**\n\n{msg}"); count += 1
            except: pass
        DB["temp_bc"] = None; return await cb.message.edit(f"✅ Sent to {count} users.")

    if data == "menu_admin" and uid == OWNER_ID:
        btns = [[types.InlineKeyboardButton("📊 Reports", callback_data="adm_rep"), types.InlineKeyboardButton("📢 Broadcast", callback_data="adm_bc")], [types.InlineKeyboardButton("🔙 Back", callback_data="back_main")]]
        return await cb.message.edit("🛠 Admin Dashboard", reply_markup=types.InlineKeyboardMarkup(btns))

    if data == "adm_rep" and uid == OWNER_ID:
        log = "".join([f"• `{e['uid']}` | {format_size(e['size'])}\n" for e in DB["history"][-10:]])
        return await cb.message.edit(f"📈 **Stats**\nUsers: {len(DB['users'])}\n\n**Log:**\n{log}", reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("🔙 Back", callback_data="menu_admin")]]))

    if data == "back_main": return await cb.message.edit("Main Menu", reply_markup=get_main_btns(uid))
    if data == "menu_thumb": return await cb.message.edit("🖼 Thumbnails", reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("👁 View", callback_data="view_t"), types.InlineKeyboardButton("🗑 Del", callback_data="del_t")], [types.InlineKeyboardButton("🔙 Back", callback_data="back_main")]] if user["thumb"] else [[types.InlineKeyboardButton("✨ Set (Send Photo)", callback_data="back_main")], [types.InlineKeyboardButton("🔙 Back", callback_data="back_main")]]))

    if data.startswith("dl_"):
        await cb.message.edit("⏳ Downloading YouTube Media..."); is_vid = data == "dl_vid"
        ydl_opts = {'format': 'bestvideo+bestaudio/best' if is_vid else 'bestaudio/best', 'outtmpl': f'{DOWNLOAD_DIR}/%(title)s.%(ext)s', 'quiet': True}
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(DB["active"][uid_str]["url"], download=True); path = ydl.prepare_filename(info)
            if not is_vid: path = os.path.splitext(path)[0] + ".mp3"
            DB["active"][uid_str].update({"path": path, "name": os.path.basename(path), "status": "ready"})
            await cb.message.edit("✅ Ready.", reply_markup=get_ready_btns())

    if data.startswith("up_"):
        await cb.message.edit("📤 Uploading..."); state = DB["active"][uid_str]
        path, thumb = state["path"], user["thumb"]
        user["last_task"] = str(datetime.now())
        scheduler.add_job(notify_ready, "date", run_date=datetime.now() + timedelta(seconds=(120 if uid==OWNER_ID else 600)), args=[uid])
        try:
            if data == "up_screen":
                screens = await take_screenshots(path, uid)
                if screens: await client.send_media_group(uid, screens)
            
            if data == "up_video": await client.send_video(uid, video=path, thumb=thumb, caption=f"`{state['name']}`", progress=progress_hook, progress_args=(cb.message, time.time(), "Uploading"))
            else: await client.send_document(uid, document=path, thumb=thumb, caption=f"`{state['name']}`", progress=progress_hook, progress_args=(cb.message, time.time(), "Uploading"))
            
            if uid != OWNER_ID: user["used"] += state["size"]
            save_db(); await cb.message.delete()
        finally:
            if os.path.exists(path): os.remove(path)
            DB["active"].pop(uid_str, None); save_db()

    if data == "rename":
        DB["active"][uid_str]["status"] = "renaming"
        await cb.message.edit("📝 Send new name with extension:")
    
    if data == "cancel":
        DB["active"].pop(uid_str, None); await cb.message.edit("❌ Session Cancelled.")

# --- STARTUP ---
async def main():
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    if not os.path.exists(THUMB_DIR): os.makedirs(THUMB_DIR)
    load_db(); await app.start()
    server = web.Application(); server.add_routes([web.get('/', lambda r: web.Response(text="Bot Alive"))])
    runner = web.AppRunner(server); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', 8000).start()
    scheduler.start(); await idle()

if __name__ == "__main__":
    app.run(main())
