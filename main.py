import os, re, shutil, time, asyncio, random, subprocess
from datetime import datetime
from pyrogram import Client, filters, types, enums, idle
from yt_dlp import YoutubeDL
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiohttp import web

# --- CONFIG ---
API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", 0)) 
OWNER_ID = 519459195
CONTACT_URL = "https://t.me/poocha"
INVITE_LINK = "https://t.me/+eooytvOAwjc0NTI1"
DOWNLOAD_DIR = "downloads"
DAILY_LIMIT = 15 * 1024 * 1024 * 1024 

# --- STATICS ---
BOT_START_TIME = datetime.now()
DB = {"users": {}, "active": {}}
CANCEL_GROUPS = set()

app = Client("dl_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- WEB SERVER FOR KOYEB HEALTH CHECKS ---
async def health_check(request):
    return web.Response(text="Bot is running perfectly!")

# --- HELPERS ---
def get_user(uid):
    today = datetime.now().date()
    if uid not in DB["users"]:
        DB["users"][uid] = {"used": 0, "pro": False, "banned": False, "last_reset": today}
    if DB["users"][uid]["last_reset"] != today:
        DB["users"][uid].update({"used": 0, "last_reset": today})
    return DB["users"][uid]

def format_size(size):
    if not size: return "0B"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024: return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"

async def is_subscribed(uid):
    if uid == OWNER_ID: return True
    try:
        member = await app.get_chat_member(CHANNEL_ID, uid)
        return member.status in [enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]
    except: return False

async def progress_hook(current, total, msg, start_time, action):
    if msg.chat.id in CANCEL_GROUPS: raise Exception("USER_CANCELLED")
    now = time.time()
    if (now - start_time) < 3: return
    try:
        percentage = current * 100 / total
        bar = "✅" * int(percentage/10) + "⬜" * (10 - int(percentage/10))
        await msg.edit(f"⏳ {action}...\n{bar} {percentage:.1f}%\n📦 {format_size(current)} / {format_size(total)}",
                      reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("Stop / Cancel ❌", callback_data="cancel")]]))
    except: pass

async def take_screenshots(video_path, uid):
    output_dir = os.path.join(DOWNLOAD_DIR, f"screens_{uid}")
    if not os.path.exists(output_dir): os.makedirs(output_dir)
    cmd = f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{video_path}"'
    duration = float(subprocess.check_output(cmd, shell=True))
    screens = []
    for i in range(1, 11):
        time_pos = (duration / 11) * i
        out_path = os.path.join(output_dir, f"thumb_{i}.jpg")
        subprocess.call(['ffmpeg', '-ss', str(time_pos), '-i', video_path, '-vframes', '1', '-q:v', '2', out_path, '-y'], stderr=subprocess.DEVNULL)
        if os.path.exists(out_path): screens.append(types.InputMediaPhoto(out_path))
    return screens

# --- KEYBOARDS ---
def get_ready_btns():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("Upload ⬆️", callback_data="up_normal"),
         types.InlineKeyboardButton("Upload + 📸", callback_data="up_screen")],
        [types.InlineKeyboardButton("Rename ✏️", callback_data="rename"),
         types.InlineKeyboardButton("Cancel ❌", callback_data="cancel")]
    ])

# --- HANDLERS ---
@app.on_message(filters.command("start"))
async def start_cmd(_, m):
    days = (datetime.now() - BOT_START_TIME).days + 1
    text = (f"👋 **Media Downloader**\n\n🚀 Running on **Free Server**\n📅 **Trial Status:** Day {days}\n\n💖 Donate to keep service alive!")
    await m.reply(text, reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("💖 Donate", url=CONTACT_URL)]]))

@app.on_message(filters.command("admin") & filters.user(OWNER_ID))
async def admin_panel(_, m):
    btns = types.InlineKeyboardMarkup([[types.InlineKeyboardButton("📊 Stats", callback_data="adm_stats"), types.InlineKeyboardButton("💾 Disk", callback_data="adm_disk")]])
    await m.reply("🛠 Admin Dashboard", reply_markup=btns)

@app.on_message(filters.text)
async def handle_text(client, m):
    uid = m.from_user.id
    user = get_user(uid)
    if user["banned"]: return
    if not await is_subscribed(uid):
        return await m.reply("⚠️ Join channel!", reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("Join Channel", url=INVITE_LINK)]]))

    if uid in DB["active"] and DB["active"][uid].get("status") == "renaming":
        state = DB["active"][uid]
        ext = os.path.splitext(state["path"])[1]
        new_name = m.text if m.text.endswith(ext) else f"{m.text}{ext}"
        new_path = os.path.join(DOWNLOAD_DIR, new_name)
        os.rename(state["path"], new_path)
        DB["active"][uid].update({"path": new_path, "name": new_name, "status": "ready"})
        return await m.reply(f"✅ Renamed.", reply_markup=get_ready_btns())

    if m.text.startswith("http"):
        CANCEL_GROUPS.discard(uid)
        status_msg = await m.reply("🔍 Analyzing...", reply_markup=types.InlineKeyboardMarkup([[types.InlineKeyboardButton("Cancel", callback_data="cancel")]]))
        try:
            with YoutubeDL({'quiet': True}) as ydl:
                info = ydl.extract_info(m.text, download=False)
                size = info.get('filesize') or info.get('filesize_approx') or 0
                if uid != OWNER_ID:
                    await app.send_message(OWNER_ID, f"👁️ New: `{info.get('title')}` ({format_size(size)})")

                if "youtube.com" in m.text or "youtu.be" in m.text:
                    DB["active"][uid] = {"url": m.text, "time": time.time()}
                    btns = [[types.InlineKeyboardButton(f"Video ({format_size(size)})", callback_data="dl_vid")],
                            [types.InlineKeyboardButton("Audio (MP3)", callback_data="dl_aud")]]
                    return await status_msg.edit("Select Format:", reply_markup=types.InlineKeyboardMarkup(btns))
                
                await status_msg.edit("⏳ Downloading...")
                path = ydl.prepare_filename(info)
                ydl.download([m.text]) 
                DB["active"][uid] = {"path": path, "name": os.path.basename(path), "status": "ready", "time": time.time()}
                await status_msg.edit(f"✅ Ready.", reply_markup=get_ready_btns())
        except Exception as e: await status_msg.edit(f"❌ Error: {str(e)[:50]}")

@app.on_callback_query()
async def cb_handler(client, cb: types.CallbackQuery):
    uid = cb.from_user.id
    if cb.data == "cancel":
        CANCEL_GROUPS.add(uid)
        if uid in DB["active"]:
            p = DB["active"][uid].get("path")
            if p and os.path.exists(p): os.remove(p)
            DB["active"].pop(uid, None)
        return await cb.message.edit("❌ Cancelled.")

    if uid not in DB["active"]: return await cb.answer("Expired.")
    state = DB["active"][uid]

    if cb.data.startswith("dl_"):
        await cb.message.edit("⏳ Downloading...")
        is_vid = cb.data == "dl_vid"
        opts = {'format': 'bestvideo+bestaudio/best' if is_vid else 'bestaudio/best', 'outtmpl': f'{DOWNLOAD_DIR}/%(title)s.%(ext)s',
                'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3'}] if not is_vid else [], 'quiet': True}
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(state["url"], download=True)
            path = ydl.prepare_filename(info)
            if not is_vid: path = os.path.splitext(path)[0] + ".mp3"
            DB["active"][uid].update({"path": path, "name": os.path.basename(path), "status": "ready"})
            await cb.message.edit(f"✅ Complete.", reply_markup=get_ready_btns())

    elif cb.data.startswith("up_"):
        path = state["path"]
        try:
            if cb.data == "up_screen":
                await cb.message.edit("📸 Generating screenshots...")
                screens = await take_screenshots(path, uid)
                await client.send_media_group(uid, screens)
                shutil.rmtree(os.path.join(DOWNLOAD_DIR, f"screens_{uid}"), ignore_errors=True)

            await cb.message.edit("📤 Uploading...")
            if path.lower().endswith(('.mp4', '.mkv', '.mov')):
                await client.send_video(uid, video=path, caption=f"`{state['name']}`", progress=progress_hook, progress_args=(cb.message, time.time(), "Uploading Video"))
            else:
                await client.send_document(uid, document=path, caption=f"`{state['name']}`", progress=progress_hook, progress_args=(cb.message, time.time(), "Uploading File"))
            await cb.message.delete()
        except Exception as e:
            if "USER_CANCELLED" not in str(e): await cb.message.reply(f"❌ Error: {e}")
        finally:
            if os.path.exists(path): os.remove(path)
            DB["active"].pop(uid, None)

    elif cb.data == "rename":
        DB["active"][uid]["status"] = "renaming"
        await cb.message.edit("📝 Send new name:")

# --- STARTUP ---
async def main():
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    
    # Start Bot
    await app.start()
    
    # Start Scheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(lambda: DB["active"].clear(), "interval", hours=1)
    scheduler.start()
    
    # Start Web Server for Koyeb
    server = web.Application()
    server.add_routes([web.get('/', health_check)])
    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8000)
    await site.start()
    
    print("Bot started successfully with Health Check!")
    await idle()

if __name__ == "__main__":
    app.run(main())
