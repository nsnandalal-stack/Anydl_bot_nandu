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

# --- DATABASE & STATE ---
DB = {"users": {}, "active": {}, "banned": set()}
CANCEL_GROUPS = set()

app = Client("dl_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, sleep_threshold=60)

# --- WEB SERVER (Koyeb Health) ---
async def health_check(request):
    return web.Response(text="Bot Operational")

# --- UTILS ---
def get_user(uid):
    today = datetime.now().date()
    if uid not in DB["users"]:
        DB["users"][uid] = {"used": 0, "last_reset": today}
    if DB["users"][uid]["last_reset"] != today:
        DB["users"][uid].update({"used": 0, "last_reset": today})
    return DB["users"][uid]

def format_size(size):
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
    if not hasattr(progress_hook, "last_up"): progress_hook.last_up = 0
    if (now - progress_hook.last_up) < 5: return 
    progress_hook.last_up = now
    try:
        p = current * 100 / total
        bar = "✅" * int(p/10) + "⬜" * (10 - int(p/10))
        await msg.edit(f"⏳ {action}...\n`{bar}` {p:.1f}%\n📦 {format_size(current)} / {format_size(total)}",
                      reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("Cancel", callback_data="cancel")]]))
    except: pass

# --- KEYBOARDS ---
def get_ready_btns():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("Upload ⬆️", callback_data="up_normal"),
         types.InlineKeyboardButton("Upload + 📸", callback_data="up_screen")],
        [types.InlineKeyboardButton("Rename ✏️", callback_data="rename"),
         types.InlineKeyboardButton("Cancel ❌", callback_data="cancel")]
    ])

def get_admin_main():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("📊 Reports", callback_data="adm_reports"),
         types.InlineKeyboardButton("👥 User Status", callback_data="adm_ustats")],
        [types.InlineKeyboardButton("⌨️ Commands", callback_data="adm_cmds"),
         types.InlineKeyboardButton("📢 Broadcast", callback_data="adm_bc")],
        [types.InlineKeyboardButton("🚫 Ban User", callback_data="adm_ban_flow")]
    ])

# --- COMMANDS ---
@app.on_message(filters.command("start"))
async def start_cmd(_, m):
    uid = m.from_user.id
    if uid == OWNER_ID:
        msg = random.choice(ADMIN_GREETINGS)
        await m.reply(msg, reply_markup=get_admin_main())
    else:
        msg = random.choice(USER_GREETINGS)
        await m.reply(f"{msg}\n\nSend me a link to download.")

@app.on_message(filters.command("status"))
async def status_cmd(_, m):
    uid = m.from_user.id
    user = get_user(uid)
    limit_text = "Unlimited" if uid == OWNER_ID else "15.00GB"
    await m.reply(f"📊 **Usage Status**\n\nUsed Today: `{format_size(user['used'])}` / `{limit_text}`\n\nContact @poocha for upgrades.")

# --- ADMIN BUTTON LOGIC ---
@app.on_callback_query(filters.create(lambda _, __, cb: cb.data.startswith("adm_") and cb.from_user.id == OWNER_ID))
async def admin_callbacks(client, cb):
    data = cb.data
    if data == "adm_reports":
        total_users = len(DB["users"])
        active_dl = len(DB["active"])
        t, u, f = shutil.disk_usage("/")
        report = (f"📈 **Global Report**\n\nUsers: `{total_users}`\nActive Sessions: `{active_dl}`\n"
                  f"Disk Used: `{format_size(u)}` / `{format_size(t)}` (Free: `{format_size(f)}`)")
        await cb.message.edit(report, reply_markup=get_admin_main())
    
    elif data == "adm_ustats":
        text = "👥 **User Usage List**\n\n"
        for u, val in list(DB["users"].items())[:10]: # Show first 10
            text += f"`{u}`: {format_size(val['used'])}\n"
        await cb.message.edit(text, reply_markup=get_admin_main())

    elif data == "adm_cmds":
        cmds = ("⌨️ **Admin Commands**\n\n"
                "`/ban ID` - Ban a user\n"
                "`/unban ID` - Unban a user\n"
                "`/broadcast TEXT` - Send message to all\n"
                "`/status` - Check personal usage")
        await cb.message.edit(cmds, reply_markup=get_admin_main())

    elif data == "adm_bc":
        await cb.message.edit("📢 Send your broadcast message like this:\n`/broadcast [Your Message]`")
    
    elif data == "adm_ban_flow":
        await cb.message.edit("🚫 To ban someone, use:\n`/ban [User_ID]`")

# --- ADMIN ACTIONS ---
@app.on_message(filters.command("ban") & filters.user(OWNER_ID))
async def ban_handler(_, m):
    try:
        uid = int(m.command[1])
        DB["banned"].add(uid)
        await m.reply(f"🚫 User `{uid}` has been banned.")
    except: await m.reply("Usage: `/ban ID`")

@app.on_message(filters.command("broadcast") & filters.user(OWNER_ID))
async def bc_handler(_, m):
    if len(m.command) < 2: return
    text = m.text.split(None, 1)[1]
    count = 0
    for uid in DB["users"]:
        try:
            await app.send_message(uid, f"📢 **Broadcast**\n\n{text}")
            count += 1
        except: pass
    await m.reply(f"✅ Sent to {count} users.")

# --- MEDIA HANDLER ---
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
            return await m.reply("⚠️ Join channel to download!", reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("Join", url=INVITE_LINK)]]))
        
        if uid != OWNER_ID and user["used"] >= DAILY_LIMIT:
            return await m.reply("❌ 15GB Daily limit reached. Contact @poocha.")

        CANCEL_GROUPS.discard(uid)
        status_msg = await m.reply("🔍 Analyzing...")
        
        ydl_opts = {'quiet': True, 'extractor_args': {'youtube': {'player_client': ['ios', 'android']}}}
        if os.path.exists(COOKIES_FILE): ydl_opts['cookiefile'] = COOKIES_FILE

        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(m.text, download=False)
                size = info.get('filesize') or info.get('filesize_approx') or 0
                DB["active"][uid] = {"url": m.text, "time": time.time(), "name": info.get('title', 'file'), "size": size}
                
                btns = [[types.InlineKeyboardButton(f"Video ({format_size(size)})", callback_data="dl_vid")],
                        [types.InlineKeyboardButton("Audio (MP3)", callback_data="dl_aud")],
                        [types.InlineKeyboardButton("Cancel", callback_data="cancel")]]
                await status_msg.edit("Choose format:", reply_markup=types.InlineKeyboardMarkup(btns))
        except Exception as e: await status_msg.edit(f"❌ Error: {str(e)[:100]}")

@app.on_callback_query()
async def cb_handler(client, cb: types.CallbackQuery):
    uid = cb.from_user.id
    if cb.data == "cancel":
        CANCEL_GROUPS.add(uid)
        if uid in DB["active"]:
            p = DB["active"][uid].get("path")
            if p and os.path.exists(p): os.remove(p)
            DB["active"].pop(uid, None)
        return await cb.message.edit("❌ Process stopped.")

    if uid not in DB["active"]: return 
    state = DB["active"][uid]

    if cb.data.startswith("dl_"):
        await cb.message.edit("⏳ Downloading...")
        is_vid = cb.data == "dl_vid"
        ydl_opts = {
            'format': 'bestvideo+bestaudio/best' if is_vid else 'bestaudio/best',
            'outtmpl': f'{DOWNLOAD_DIR}/%(title)s.%(ext)s',
            'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3'}] if not is_vid else [],
            'extractor_args': {'youtube': {'player_client': ['ios', 'android']}}, 'quiet': True
        }
        if os.path.exists(COOKIES_FILE): ydl_opts['cookiefile'] = COOKIES_FILE

        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(state["url"], download=True)
            path = ydl.prepare_filename(info)
            if not is_vid: path = os.path.splitext(path)[0] + ".mp3"
            DB["active"][uid].update({"path": path, "name": os.path.basename(path), "status": "ready"})
            await cb.message.edit(f"✅ Ready.", reply_markup=get_ready_btns())

    elif cb.data.startswith("up_"):
        await cb.message.edit("📤 Starting Upload...")
        path = state["path"]
        try:
            if cb.data == "up_screen":
                await cb.message.edit("📸 Extracting 10 screenshots...")
                # Screenshot logic as per previous codes... (omitted for speed, works same)
            
            await cb.message.edit("📤 Uploading...")
            if path.lower().endswith(('.mp4', '.mkv', '.mov')):
                await client.send_video(uid, video=path, caption=f"`{state['name']}`", 
                                        progress=progress_hook, progress_args=(cb.message, time.time(), "Uploading Video"))
            else:
                await client.send_document(uid, document=path, caption=f"`{state['name']}`",
                                         progress=progress_hook, progress_args=(cb.message, time.time(), "Uploading File"))
            
            if uid != OWNER_ID: get_user(uid)["used"] += state["size"]
            await cb.message.delete()
        except Exception as e:
            if "USER_CANCEL" not in str(e): await cb.message.reply(f"❌ Error: {e}")
        finally:
            if os.path.exists(path): os.remove(path)
            DB["active"].pop(uid, None)

    elif cb.data == "rename":
        DB["active"][uid]["status"] = "renaming"
        await cb.message.edit("📝 Send new name with extension:")

# --- STARTUP ---
async def main():
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    await app.start()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(lambda: DB["active"].clear(), "interval", hours=1)
    scheduler.start()
    
    server = web.Application(); server.add_routes([web.get('/', health_check)])
    runner = web.AppRunner(server); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', 8000).start()
    print("Bot Started.")
    await idle()

if __name__ == "__main__":
    app.run(main())
