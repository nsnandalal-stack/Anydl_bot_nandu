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
DB = {"users": {}, "active": {}, "history": [], "temp_bc": None}
app = Client("dl_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, sleep_threshold=120)
scheduler = AsyncIOScheduler()

def save_db():
    with open(DB_FILE, "w") as f: json.dump(DB, f, default=str)

def load_db():
    global DB
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r") as f: DB = json.load(f)
        except: pass

def get_user(uid):
    uid = str(uid)
    today = str(datetime.now().date())
    if uid not in DB["users"]:
        DB["users"][uid] = {"used": 0, "last_reset": today, "warnings": 0, "is_pro": False, "is_banned": False, "thumb": None, "state": "none", "last_task": None}
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

async def notify_ready(uid):
    try: await app.send_message(uid, "✅ **Cooldown finished.** Send task now.")
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
    else: btns.append([types.InlineKeyboardButton("📊 Status", callback_data="menu_status"), types.InlineKeyboardButton("💎 Upgrade", url=CONTACT_URL)])
    btns.append([types.InlineKeyboardButton("🚪 Exit", callback_data="exit")])
    return types.InlineKeyboardMarkup(btns)

def get_admin_btns():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("📊 Reports", callback_data="adm_rep"), types.InlineKeyboardButton("📢 Broadcast", callback_data="adm_bc")],
        [types.InlineKeyboardButton("👥 Management", callback_data="adm_manage"), types.InlineKeyboardButton("🛠 Stability", callback_data="adm_stab")],
        [types.InlineKeyboardButton("🔙 Back", callback_data="back_main")]
    ])

def get_manage_btns():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("🚫 Ban", callback_data="mng_ban"), types.InlineKeyboardButton("✅ Unban", callback_data="mng_unban")],
        [types.InlineKeyboardButton("💎 Set Pro", callback_data="mng_pro"), types.InlineKeyboardButton("🔙 Back", callback_data="menu_admin")]
    ])

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
    user = get_user(uid)
    if user["is_banned"]: return await m.reply("🚫 You are banned.")
    msg = random.choice(ADMIN_GREETINGS if uid == OWNER_ID else USER_GREETINGS)
    await m.reply(msg, reply_markup=get_main_btns(uid))

@app.on_message(filters.text & ~filters.command(["start"]) & filters.private)
async def handle_text(client, m):
    uid, uid_str = m.from_user.id, str(m.from_user.id)
    user = get_user(uid)
    if user["is_banned"]: return

    # Admin Text States
    if uid == OWNER_ID:
        if user["state"] == "pending_bc":
            DB["temp_bc"] = m.text; user["state"] = "none"
            btns = [[types.InlineKeyboardButton("✅ Confirm", callback_data="bc_confirm"), types.InlineKeyboardButton("❌ Stop", callback_data="adm_bc")]]
            return await m.reply(f"📝 **Broadcast Preview:**\n\n{m.text}", reply_markup=types.InlineKeyboardMarkup(btns))
        
        if user["state"].startswith("mng_"):
            action = user["state"].split("_")[1]
            target_id = m.text.strip()
            user["state"] = "none"
            if target_id in DB["users"] or target_id.isdigit():
                t_user = get_user(target_id)
                if action == "ban": t_user["is_banned"] = True
                elif action == "unban": t_user["is_banned"] = False; t_user["warnings"] = 0
                elif action == "pro": t_user["is_pro"] = True
                save_db()
                return await m.reply(f"✅ Action `{action}` completed for ID `{target_id}`", reply_markup=get_manage_btns())
            return await m.reply("❌ Invalid ID.", reply_markup=get_manage_btns())

    # Rename Flow
    if uid_str in DB["active"] and DB["active"][uid_str].get("status") == "renaming":
        state = DB["active"][uid_str]
        ext = os.path.splitext(state["path"])[1]
        new_name = m.text if m.text.endswith(ext) else f"{m.text}{ext}"
        os.rename(state["path"], os.path.join(DOWNLOAD_DIR, new_name))
        DB["active"][uid_str].update({"path": os.path.join(DOWNLOAD_DIR, new_name), "name": new_name, "status": "ready"})
        return await m.reply("✅ Renamed.", reply_markup=get_ready_btns())

    if m.text.startswith("http"):
        if not await is_subscribed(uid): return await m.reply("⚠️ Join channel first.", reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("Join", url=INVITE_LINK)], [types.InlineKeyboardButton("🔄 Verify", callback_data="verify_sub")]]))
        status_msg = await m.reply("🔍 Analyzing...")
        try:
            with YoutubeDL({'quiet': True}) as ydl:
                info = ydl.extract_info(m.text, download=False)
                size = info.get('filesize_approx') or info.get('filesize') or 0
                if "youtube.com" in m.text or "youtu.be" in m.text:
                    DB["active"][uid_str] = {"url": m.text, "status": "choosing", "size": size}
                    btns = [[types.InlineKeyboardButton(f"Video ({format_size(size)})", callback_data="dl_vid")], [types.InlineKeyboardButton("Audio (MP3)", callback_data="dl_aud"), types.InlineKeyboardButton("Cancel", callback_data="cancel")]]
                    return await status_msg.edit("🎬 YouTube Detected:", reply_markup=types.InlineKeyboardMarkup(btns))
                ydl.download([m.text]); path = ydl.prepare_filename(info)
                DB["active"][uid_str] = {"path": path, "name": os.path.basename(path), "status": "ready", "size": size}
                DB["history"].append({"uid": uid, "name": os.path.basename(path), "size": size}); save_db()
                await status_msg.edit("✅ Ready.", reply_markup=get_ready_btns())
        except: await status_msg.edit("❌ Error.")

@app.on_callback_query()
async def cb_handler(client, cb: types.CallbackQuery):
    uid, uid_str = cb.from_user.id, str(cb.from_user.id)
    data, user = cb.data, get_user(uid)
    await cb.answer()

    # --- ADMIN MENUS ---
    if data == "menu_admin" and uid == OWNER_ID:
        return await cb.message.edit("🛠 **Admin Dashboard**", reply_markup=get_admin_btns())
    
    if data == "adm_bc" and uid == OWNER_ID:
        user["state"] = "pending_bc"
        return await cb.message.edit("📢 Send your broadcast message now.", reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("🔙 Back", callback_data="menu_admin")]]))

    if data == "bc_confirm" and uid == OWNER_ID:
        msg, count = DB.get("temp_bc"), 0
        for u in DB["users"]:
            try: await app.send_message(int(u), f"📢 **Broadcast**\n\n{msg}"); count += 1; await asyncio.sleep(0.1)
            except: pass
        DB["temp_bc"] = None; return await cb.message.edit(f"✅ Sent to {count} users.", reply_markup=get_admin_btns())

    if data == "adm_manage" and uid == OWNER_ID:
        return await cb.message.edit("👥 **User Management**\nSelect an action:", reply_markup=get_manage_btns())

    if data.startswith("mng_") and uid == OWNER_ID:
        action = data.split("_")[1]
        user["state"] = data
        return await cb.message.edit(f"📝 Send the User ID to **{action}**:")

    if data == "adm_stab" and uid == OWNER_ID:
        t, u, f = shutil.disk_usage("/")
        return await cb.message.edit(f"🛠 **Stability Report**\n\n💾 Disk: {format_size(u)} / {format_size(t)}\n✅ Health: OK\n🌐 Port: 8000", reply_markup=get_admin_btns())

    if data == "adm_rep" and uid == OWNER_ID:
        log = "".join([f"• `{e['uid']}` | {format_size(e['size'])}\n" for e in DB["history"][-10:]])
        return await cb.message.edit(f"📊 **Reports**\nUsers: {len(DB['users'])}\n\n**Activity:**\n{log}", reply_markup=get_admin_btns())

    # --- USER ACTIONS ---
    if data == "back_main": return await cb.message.edit("Main Menu", reply_markup=get_main_btns(uid))
    if data == "verify_sub":
        if await is_subscribed(uid): return await cb.message.edit("✅ Access Granted.", reply_markup=get_main_btns(uid))
        else: return await cb.answer("❌ Not joined!", show_alert=True)

    if uid_str not in DB["active"]: return
    state = DB["active"][uid_str]

    if data.startswith("dl_"):
        await cb.message.edit("⏳ Downloading YouTube..."); is_vid = data == "dl_vid"
        ydl_opts = {'format': 'bestvideo+bestaudio/best' if is_vid else 'bestaudio/best', 'outtmpl': f'{DOWNLOAD_DIR}/%(title)s.%(ext)s', 'quiet': True}
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(state["url"], download=True); path = ydl.prepare_filename(info)
            if not is_vid: path = os.path.splitext(path)[0] + ".mp3"
            DB["active"][uid_str].update({"path": path, "name": os.path.basename(path), "status": "ready"})
            await cb.message.edit("✅ Ready.", reply_markup=get_ready_btns())

    if data.startswith("up_"):
        await cb.message.edit("📤 Uploading..."); path = state["path"]
        wait = 120 if uid == OWNER_ID else 600
        user["last_task"] = str(datetime.now())
        scheduler.add_job(notify_ready, "date", run_date=datetime.now() + timedelta(seconds=wait), args=[uid])
        try:
            if data == "up_screen":
                screens = await take_screenshots(path, uid)
                if screens: await client.send_media_group(uid, screens)
            if data == "up_video": await client.send_video(uid, video=path, thumb=user["thumb"], caption=f"`{state['name']}`")
            else: await client.send_document(uid, document=path, thumb=user["thumb"], caption=f"`{state['name']}`")
            if uid != OWNER_ID: user["used"] += state["size"]
            save_db(); await cb.message.delete()
        finally:
            if os.path.exists(path): os.remove(path)
            DB["active"].pop(uid_str, None); save_db()

    if data == "rename":
        DB["active"][uid_str]["status"] = "renaming"
        await cb.message.edit("📝 Send new name with extension:")
    
    if data == "cancel":
        DB["active"].pop(uid_str, None); await cb.message.edit("❌ Cancelled.")

# --- STARTUP ---
async def main():
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    if not os.path.exists(THUMB_DIR): os.makedirs(THUMB_DIR)
    load_db(); await app.start()
    server = web.Application(); server.add_routes([web.get('/', lambda r: web.Response(text="OK"))])
    runner = web.AppRunner(server); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', 8000).start()
    scheduler.start(); await idle()

if __name__ == "__main__":
    app.run(main())
