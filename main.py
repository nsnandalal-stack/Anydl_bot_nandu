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

# Rotating Messages
USER_GREETINGS = ["Thanks for chatting with me.", "Glad you’re here.", "Appreciate you using this bot.", "Happy to help you today.", "Let me know how I can assist."]
ADMIN_GREETINGS = ["Chief, systems are ready.", "Ready when you are, chief.", "All set. What’s the move?", "Standing by for instructions.", "Let’s begin, chief."]

DB = {"users": {}, "active": {}, "banned": set()}
CANCEL_GROUPS = set()

# Added sleep_threshold to handle FloodWaits automatically
app = Client("dl_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, sleep_threshold=120)

async def health_check(request):
    return web.Response(text="Bot Alive")

def get_user(uid):
    today = datetime.now().date()
    if uid not in DB["users"]: DB["users"][uid] = {"used": 0, "last_reset": today}
    if DB["users"][uid]["last_reset"] != today: DB["users"][uid].update({"used": 0, "last_reset": today})
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

# --- PROGRESS HOOK (Safety: 10s delay) ---
async def progress_hook(current, total, msg, start_time, action):
    if msg.chat.id in CANCEL_GROUPS: raise Exception("USER_CANCEL")
    now = time.time()
    
    # Store last update time in the message object to be safe
    if not hasattr(msg, "last_up"): msg.last_up = 0
    if (now - msg.last_up) < 10: return 
    
    msg.last_up = now
    try:
        p = current * 100 / total
        bar = "✅" * int(p/10) + "⬜" * (10 - int(p/10))
        await msg.edit(f"⏳ {action}...\n`{bar}` {p:.1f}%\n📦 {format_size(current)} / {format_size(total)}")
    except errors.FloodWait as e:
        await asyncio.sleep(e.value)
    except: pass

def get_ready_btns():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("Upload ⬆️", callback_data="up_normal"),
         types.InlineKeyboardButton("Upload + 📸", callback_data="up_screen")],
        [types.InlineKeyboardButton("Rename ✏️", callback_data="rename"),
         types.InlineKeyboardButton("Cancel ❌", callback_data="cancel")]
    ])

@app.on_message(filters.command("start"))
async def start_cmd(_, m):
    uid = m.from_user.id
    msg = random.choice(ADMIN_GREETINGS if uid == OWNER_ID else USER_GREETINGS)
    await m.reply(msg)

@app.on_message(filters.command("status"))
async def status_cmd(_, m):
    uid = m.from_user.id
    user = get_user(uid)
    limit = "Unlimited" if uid == OWNER_ID else "15.00GB"
    await m.reply(f"📊 **Usage:** `{format_size(user['used'])}` / `{limit}`")

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
            return await m.reply("❌ Limit reached.")

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
                        [types.InlineKeyboardButton("Audio (MP3)", callback_data="dl_aud")]]
                await status_msg.edit("Choose format:", reply_markup=types.InlineKeyboardMarkup(btns))
        except Exception as e: await status_msg.edit(f"❌ Error: Link is blocked or invalid.")

@app.on_callback_query()
async def cb_handler(client, cb: types.CallbackQuery):
    uid = cb.from_user.id
    if cb.data == "cancel":
        CANCEL_GROUPS.add(uid)
        DB["active"].pop(uid, None)
        return await cb.message.edit("❌ Cancelled.")

    if uid not in DB["active"]: return await cb.answer("Expired.")
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

        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(state["url"], download=True)
                path = ydl.prepare_filename(info)
                if not is_vid: path = os.path.splitext(path)[0] + ".mp3"
                DB["active"][uid].update({"path": path, "name": os.path.basename(path), "status": "ready"})
                await cb.message.edit(f"✅ Ready.", reply_markup=get_ready_btns())
        except Exception as e: await cb.message.edit(f"❌ Error during download.")

    elif cb.data.startswith("up_"):
        await cb.message.edit("📤 Uploading...")
        path = state["path"]
        try:
            if path.lower().endswith(('.mp4', '.mkv', '.mov')):
                await client.send_video(uid, video=path, caption=f"`{state['name']}`", 
                                        progress=progress_hook, progress_args=(cb.message, time.time(), "Uploading Video"))
            else:
                await client.send_document(uid, document=path, caption=f"`{state['name']}`",
                                         progress=progress_hook, progress_args=(cb.message, time.time(), "Uploading File"))
            
            if uid != OWNER_ID: get_user(uid)["used"] += state["size"]
            await cb.message.delete()
        except Exception as e:
            if "USER_CANCEL" not in str(e): await cb.message.reply(f"❌ Upload Error.")
        finally:
            if os.path.exists(path): os.remove(path)
            DB["active"].pop(uid, None)

    elif cb.data == "rename":
        DB["active"][uid]["status"] = "renaming"
        await cb.message.edit("📝 Send new name:")

async def main():
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    await app.start()
    scheduler = AsyncIOScheduler()
    scheduler.add_job(lambda: DB["active"].clear(), "interval", hours=1)
    scheduler.start()
    
    server = web.Application(); server.add_routes([web.get('/', health_check)])
    runner = web.AppRunner(server); await runner.setup()
    await web.TCPSite(runner, '0.0.0.0', 8000).start()
    await idle()

if __name__ == "__main__":
    app.run(main())
