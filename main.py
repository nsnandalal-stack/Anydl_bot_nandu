import os
import re
import time
import json
import shutil
import asyncio
import base64
from datetime import date, datetime
from aiohttp import web, ClientSession, ClientTimeout
from pyrogram import Client, filters, types, enums, idle
from yt_dlp import YoutubeDL

# =======================
# ENVIRONMENT VARIABLES
# =======================
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_ID = os.getenv("CHANNEL_ID")
OWNER_ID = os.getenv("OWNER_ID")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
COOKIES = os.getenv("COOKIES")
COOKIES_BASE64 = os.getenv("COOKIES_BASE64")
INVITE_LINK = os.getenv("INVITE_LINK", "https://t.me/+eooytvOAwjc0NTI1")

if COOKIES_BASE64 and not COOKIES:
    try:
        COOKIES = base64.b64decode(COOKIES_BASE64).decode()
    except:
        pass

# Validate
missing = [v for v in ["API_ID","API_HASH","BOT_TOKEN","CHANNEL_ID","OWNER_ID"] if not os.getenv(v)]
if missing:
    print(f"Missing: {missing}")
    exit(1)

API_ID = int(API_ID)
CHANNEL_ID = int(CHANNEL_ID)
OWNER_ID = int(OWNER_ID)

print(f"Owner: {OWNER_ID}")

# Config
DOWNLOAD_DIR = "/tmp/downloads"
THUMB_DIR = "/tmp/thumbnails"
DB_FILE = "/tmp/bot_db.json"
DAILY_LIMIT = 5 * 1024 * 1024 * 1024
COOKIES_PATH = None

# =======================
# COOKIES SETUP
# =======================
def setup_cookies():
    global COOKIES_PATH
    if not COOKIES:
        print("No cookies")
        return
    try:
        content = COOKIES.replace('\\n', '\n').replace('\\t', '\t')
        lines = []
        for line in content.split('\n'):
            line = line.strip()
            if not line:
                continue
            if line.startswith('#'):
                lines.append(line)
                continue
            parts = line.split()
            if len(parts) >= 7:
                lines.append('\t'.join(parts))
            elif '\t' in line:
                lines.append(line)
        
        if not any(l for l in lines if not l.startswith('#')):
            print("No valid cookie entries")
            return
            
        result = '# Netscape HTTP Cookie File\n\n' + '\n'.join(lines)
        COOKIES_PATH = "/tmp/cookies.txt"
        with open(COOKIES_PATH, 'w') as f:
            f.write(result)
        
        count = len([l for l in lines if l and not l.startswith('#')])
        print(f"Cookies: {count} entries")
    except Exception as e:
        print(f"Cookie error: {e}")

# =======================
# DATABASE
# =======================
DB = {"users": {}, "sessions": {}}

def db_load():
    global DB
    try:
        if os.path.exists(DB_FILE):
            with open(DB_FILE) as f:
                DB = json.load(f)
    except:
        pass

def db_save():
    try:
        with open(DB_FILE, 'w') as f:
            json.dump(DB, f)
    except:
        pass

def user_get(uid):
    k = str(uid)
    if k not in DB["users"]:
        DB["users"][k] = {"thumb": None, "state": "none", "used": 0, "reset": date.today().isoformat(), "is_pro": uid == OWNER_ID, "is_banned": False}
    u = DB["users"][k]
    if u.get("reset") != date.today().isoformat():
        u["reset"] = date.today().isoformat()
        u["used"] = 0
    return u

def session_get(uid):
    return DB["sessions"].get(str(uid))

def session_set(uid, data):
    DB["sessions"][str(uid)] = data
    db_save()

def session_clear(uid):
    DB["sessions"].pop(str(uid), None)
    user_get(uid)["state"] = "none"
    db_save()

# =======================
# HELPERS
# =======================
def safe_name(n):
    return re.sub(r'[\\/*?:"<>|]', "", str(n).strip())[:150] or "file"

def get_ext(n):
    return os.path.splitext(n)[1]

def is_yt(url):
    return any(x in url.lower() for x in ["youtube.com", "youtu.be"])

def is_ig(url):
    return "instagram.com" in url.lower()

def get_yt_id(url):
    if "youtu.be/" in url:
        return url.split("youtu.be/")[1].split("?")[0]
    if "v=" in url:
        return url.split("v=")[1].split("&")[0]
    if "/shorts/" in url:
        return url.split("/shorts/")[1].split("?")[0]
    return ""

def human_size(n):
    for u in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}TB"

def prog_bar(p):
    return "█" * int(p/10) + "░" * (10 - int(p/10))

def check_limit(uid, size=0):
    if uid == OWNER_ID:
        return True, float('inf')
    u = user_get(uid)
    if u.get("is_pro"):
        return True, float('inf')
    used = u.get("used", 0)
    rem = DAILY_LIMIT - used
    if size:
        return used + size <= DAILY_LIMIT, rem
    return used < DAILY_LIMIT, rem

async def safe_edit(msg, text, kb=None):
    try:
        return await msg.edit_text(text, reply_markup=kb)
    except:
        return msg

async def is_member(uid):
    if uid == OWNER_ID:
        return True
    try:
        m = await app.get_chat_member(CHANNEL_ID, uid)
        return m.status in [enums.ChatMemberStatus.MEMBER, enums.ChatMemberStatus.ADMINISTRATOR, enums.ChatMemberStatus.OWNER]
    except:
        return False

# =======================
# KEYBOARDS
# =======================
def kb_join():
    return types.InlineKeyboardMarkup([[types.InlineKeyboardButton("📢 Join", url=INVITE_LINK)], [types.InlineKeyboardButton("✅ Verify", callback_data="verify")]])

def kb_cancel():
    return types.InlineKeyboardMarkup([[types.InlineKeyboardButton("❌ Cancel", callback_data="cancel")]])

def kb_menu(uid):
    kb = [[types.InlineKeyboardButton("🖼️ Thumb", callback_data="thumb"), types.InlineKeyboardButton("📊 Stats", callback_data="stats")], [types.InlineKeyboardButton("❓ Help", callback_data="help")]]
    if uid == OWNER_ID:
        kb.append([types.InlineKeyboardButton("⚙️ Admin", callback_data="admin")])
    kb.append([types.InlineKeyboardButton("✖️ Close", callback_data="close")])
    return types.InlineKeyboardMarkup(kb)

def kb_thumb():
    return types.InlineKeyboardMarkup([[types.InlineKeyboardButton("👁️ View", callback_data="thumb_view"), types.InlineKeyboardButton("🗑️ Del", callback_data="thumb_del")], [types.InlineKeyboardButton("🔙", callback_data="back")]])

def kb_upload():
    return types.InlineKeyboardMarkup([[types.InlineKeyboardButton("✏️ Rename", callback_data="rename"), types.InlineKeyboardButton("📄 File", callback_data="up_file"), types.InlineKeyboardButton("🎬 Video", callback_data="up_video")], [types.InlineKeyboardButton("❌", callback_data="cancel")]])

def kb_rename():
    return types.InlineKeyboardMarkup([[types.InlineKeyboardButton("📝 Keep", callback_data="ren_keep"), types.InlineKeyboardButton("✏️ Custom", callback_data="ren_custom")], [types.InlineKeyboardButton("🔙", callback_data="back_up")]])

def kb_quality():
    return types.InlineKeyboardMarkup([[types.InlineKeyboardButton("1080p", callback_data="q_1080"), types.InlineKeyboardButton("720p", callback_data="q_720"), types.InlineKeyboardButton("480p", callback_data="q_480")], [types.InlineKeyboardButton("360p", callback_data="q_360"), types.InlineKeyboardButton("MP3", callback_data="q_mp3"), types.InlineKeyboardButton("MP3 HD", callback_data="q_mp3_320")], [types.InlineKeyboardButton("❌", callback_data="cancel")]])

def kb_admin():
    return types.InlineKeyboardMarkup([[types.InlineKeyboardButton("📊", callback_data="adm_stats"), types.InlineKeyboardButton("📢", callback_data="adm_bc")], [types.InlineKeyboardButton("👑", callback_data="adm_pro"), types.InlineKeyboardButton("🚫", callback_data="adm_ban"), types.InlineKeyboardButton("✅", callback_data="adm_unban")], [types.InlineKeyboardButton("🔙", callback_data="back")]])

def kb_bc():
    return types.InlineKeyboardMarkup([[types.InlineKeyboardButton("✅ Send", callback_data="bc_yes"), types.InlineKeyboardButton("❌", callback_data="bc_no")]])

# =======================
# DOWNLOAD
# =======================
async def dl_url(uid, url, msg, name=None, quality="720"):
    async with ClientSession(timeout=ClientTimeout(total=600)) as s:
        async with s.get(url) as r:
            if r.status != 200:
                raise Exception(f"HTTP {r.status}")
            if not name:
                name = f"video_{int(time.time())}" + (".mp3" if "mp3" in quality else ".mp4")
            name = safe_name(name)
            path = os.path.join(DOWNLOAD_DIR, name)
            total = int(r.headers.get("Content-Length", 0))
            ok, rem = check_limit(uid, total)
            if not ok:
                raise Exception(f"Limit! Left: {human_size(rem)}")
            done, last, start = 0, 0, time.time()
            with open(path, 'wb') as f:
                async for chunk in r.content.iter_chunked(524288):
                    if session_get(uid) and session_get(uid).get("cancel"):
                        raise Exception("CANCELLED")
                    f.write(chunk)
                    done += len(chunk)
                    if time.time() - last >= 2:
                        last = time.time()
                        pct = (done/total*100) if total else 0
                        await safe_edit(msg, f"⬇️ `[{prog_bar(pct)}]` {pct:.0f}%\n{human_size(done)}/{human_size(total)}", kb_cancel())
            return path, os.path.splitext(name)[0]

async def dl_ytdlp(uid, url, msg, quality="720"):
    await safe_edit(msg, "🔄 Downloading...", kb_cancel())
    last = {"t": 0}
    def hook(d):
        if session_get(uid) and session_get(uid).get("cancel"):
            raise Exception("CANCELLED")
        if d["status"] == "downloading" and time.time() - last["t"] >= 2:
            last["t"] = time.time()
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done = d.get("downloaded_bytes", 0)
            pct = (done/total*100) if total else 0
            asyncio.get_event_loop().create_task(safe_edit(msg, f"⬇️ `[{prog_bar(pct)}]` {pct:.0f}%", kb_cancel()))
    
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "outtmpl": f"{DOWNLOAD_DIR}/%(title).70s.%(ext)s", "progress_hooks": [hook], "retries": 5, "socket_timeout": 30, "nocheckcertificate": True, "geo_bypass": True, "extractor_args": {"youtube": {"player_client": ["android", "web"]}}, "http_headers": {"User-Agent": "com.google.android.youtube/17.36.4 (Linux; U; Android 12) gzip"}}
    
    if COOKIES_PATH and os.path.exists(COOKIES_PATH):
        opts["cookiefile"] = COOKIES_PATH
    
    if quality.startswith("mp3"):
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "320" if quality == "mp3_320" else "192"}]
    else:
        h = int(quality) if quality.isdigit() else 720
        opts["format"] = f"bestvideo[height<={h}]+bestaudio/best[height<={h}]/best"
        opts["merge_output_format"] = "mp4"
    
    def do():
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)
            if quality.startswith("mp3"):
                path = os.path.splitext(path)[0] + ".mp3"
            return path, info.get("title", "video")
    return await asyncio.get_event_loop().run_in_executor(None, do)

async def dl_invidious(uid, url, msg, quality="720"):
    vid = get_yt_id(url)
    if not vid:
        raise Exception("Invalid URL")
    await safe_edit(msg, "🔄 Invidious...", kb_cancel())
    for inst in ["https://inv.nadeko.net", "https://invidious.nerdvpn.de"]:
        try:
            async with ClientSession(timeout=ClientTimeout(total=30)) as s:
                async with s.get(f"{inst}/api/v1/videos/{vid}") as r:
                    if r.status != 200:
                        continue
                    data = await r.json()
                    title = data.get("title", "video")
                    if quality.startswith("mp3"):
                        streams = [x for x in data.get("adaptiveFormats", []) if x.get("type", "").startswith("audio")]
                        if streams:
                            streams.sort(key=lambda x: x.get("bitrate", 0), reverse=True)
                            return await dl_url(uid, streams[0]["url"], msg, f"{safe_name(title)}.mp3", quality)
                    else:
                        h = int(quality) if quality.isdigit() else 720
                        for st in data.get("formatStreams", []):
                            res = st.get("resolution", "")
                            if "p" in res and int(res.replace("p","")) <= h:
                                return await dl_url(uid, st["url"], msg, f"{safe_name(title)}.mp4", quality)
        except:
            continue
    raise Exception("Invidious failed")

async def dl_cobalt(uid, url, msg, quality="720"):
    await safe_edit(msg, "🔄 Cobalt...", kb_cancel())
    is_audio = quality.startswith("mp3")
    try:
        async with ClientSession(timeout=ClientTimeout(total=60)) as s:
            async with s.post("https://co.wuk.sh/api/json", json={"url": url, "vCodec": "h264", "vQuality": quality if quality.isdigit() else "720", "isAudioOnly": is_audio}) as r:
                if r.status == 200:
                    data = await r.json()
                    if data.get("url"):
                        return await dl_url(uid, data["url"], msg, f"video_{int(time.time())}" + (".mp3" if is_audio else ".mp4"), quality)
    except:
        pass
    raise Exception("Cobalt failed")

async def download(uid, url, msg, quality="720"):
    ok, rem = check_limit(uid)
    if not ok:
        raise Exception(f"Limit exceeded! Left: {human_size(rem)}")
    errors = []
    if is_yt(url):
        try:
            return await dl_invidious(uid, url, msg, quality)
        except Exception as e:
            if "CANCELLED" in str(e): raise
            errors.append(str(e)[:30])
    try:
        return await dl_cobalt(uid, url, msg, quality)
    except Exception as e:
        if "CANCELLED" in str(e): raise
        errors.append(str(e)[:30])
    try:
        return await dl_ytdlp(uid, url, msg, quality)
    except Exception as e:
        if "CANCELLED" in str(e): raise
        errors.append(str(e)[:30])
    raise Exception("Failed: " + ", ".join(errors))

# =======================
# SCREENSHOTS
# =======================
async def screenshots(path, count=5):
    out = os.path.join(DOWNLOAD_DIR, f"ss_{int(time.time())}")
    os.makedirs(out, exist_ok=True)
    screens = []
    try:
        proc = await asyncio.create_subprocess_shell(f'ffprobe -v error -show_entries format=duration -of csv=p=0 "{path}"', stdout=asyncio.subprocess.PIPE)
        stdout, _ = await proc.communicate()
        dur = float(stdout.decode().strip() or 0)
        if dur > 0:
            for i in range(1, count + 1):
                ss = os.path.join(out, f"{i}.jpg")
                await (await asyncio.create_subprocess_shell(f'ffmpeg -ss {dur/(count+1)*i} -i "{path}" -vframes 1 -q:v 5 -y "{ss}" 2>/dev/null')).wait()
                if os.path.exists(ss):
                    screens.append(ss)
    except:
        pass
    return screens, out

# =======================
# UPLOAD
# =======================
async def upload(uid, msg, path, name, as_video):
    user = user_get(uid)
    thumb = user.get("thumb") if user.get("thumb") and os.path.exists(user.get("thumb")) else None
    size = os.path.getsize(path)
    ok, rem = check_limit(uid, size)
    if not ok:
        raise Exception(f"Limit! Left: {human_size(rem)}")
    last = {"t": 0}
    async def prog(done, total):
        if session_get(uid) and session_get(uid).get("cancel"):
            raise Exception("CANCELLED")
        if time.time() - last["t"] >= 2:
            last["t"] = time.time()
            await safe_edit(msg, f"📤 `[{prog_bar(done/total*100 if total else 0)}]` {done/total*100:.0f}%", kb_cancel())
    
    if as_video:
        await app.send_video(uid, path, caption=f"🎬 `{name}`", file_name=name, supports_streaming=True, thumb=thumb, progress=prog)
        await safe_edit(msg, "📸 Screenshots...", None)
        ss, ss_dir = await screenshots(path)
        if ss:
            try:
                await app.send_media_group(uid, [types.InputMediaPhoto(s) for s in ss])
            except:
                pass
        shutil.rmtree(ss_dir, ignore_errors=True)
    else:
        await app.send_document(uid, path, caption=f"📄 `{name}`", file_name=name, thumb=thumb, progress=prog)
    
    if uid != OWNER_ID and not user.get("is_pro"):
        user["used"] = user.get("used", 0) + size
        db_save()

# =======================
# BOT
# =======================
app = Client("bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, workdir="/tmp")

@app.on_message(filters.command("start") & filters.private)
async def cmd_start(_, m):
    uid = m.from_user.id
    user_get(uid)
    db_save()
    if user_get(uid).get("is_banned"):
        return await m.reply_text("🚫 Banned")
    if uid == OWNER_ID:
        return await m.reply_text(f"👑 Welcome Boss!\n\n🍪 Cookies: {'✅' if COOKIES_PATH else '❌'}", reply_markup=kb_menu(uid))
    if not await is_member(uid):
        return await m.reply_text("⚠️ Join channel first!", reply_markup=kb_join())
    rem = DAILY_LIMIT - user_get(uid).get("used", 0)
    await m.reply_text(f"👋 Hi {m.from_user.first_name}!\n📊 {human_size(rem)} left", reply_markup=kb_menu(uid))

@app.on_message(filters.text & filters.private & ~filters.command(["start"]))
async def on_text(_, m):
    uid = m.from_user.id
    user = user_get(uid)
    txt = m.text.strip()
    if user.get("is_banned"):
        return
    if uid != OWNER_ID and not await is_member(uid):
        return await m.reply_text("⚠️ Join first!", reply_markup=kb_join())
    
    state = user.get("state", "none")
    if state == "rename":
        sess = session_get(uid)
        if sess:
            sess["name"] = safe_name(txt) + sess.get("ext", "")
            session_set(uid, sess)
        user["state"] = "none"
        db_save()
        return await m.reply_text(f"✅ `{sess['name']}`", reply_markup=kb_upload())
    if state == "bc" and uid == OWNER_ID:
        user["state"] = "none"
        user["bc_text"] = txt
        db_save()
        return await m.reply_text(f"Preview:\n\n{txt}", reply_markup=kb_bc())
    if state in ["pro", "ban", "unban"] and uid == OWNER_ID:
        user["state"] = "none"
        db_save()
        try:
            target = user_get(int(txt))
            if state == "pro":
                target["is_pro"] = True
            elif state == "ban":
                target["is_banned"] = True
            else:
                target["is_banned"] = False
            db_save()
            return await m.reply_text("✅ Done!", reply_markup=kb_admin())
        except:
            return await m.reply_text("❌ Invalid!", reply_markup=kb_admin())
    
    if not txt.startswith("http"):
        return
    ok, rem = check_limit(uid)
    if not ok:
        return await m.reply_text(f"❌ Limit! Left: {human_size(rem)}")
    
    status = await m.reply_text("🔍 Analyzing...", reply_markup=kb_cancel())
    session_set(uid, {"url": txt, "cancel": False})
    if is_yt(txt) or is_ig(txt):
        return await safe_edit(status, "Choose quality:", kb_quality())
    try:
        path, title = await dl_url(uid, txt, status)
        name = os.path.basename(path)
        session_set(uid, {"url": txt, "path": path, "name": name, "ext": get_ext(name), "size": os.path.getsize(path), "cancel": False})
        await safe_edit(status, f"✅ `{name}`\n📦 {human_size(os.path.getsize(path))}", kb_upload())
    except Exception as e:
        session_clear(uid)
        await safe_edit(status, "❌ Cancelled!" if "CANCELLED" in str(e) else f"❌ {str(e)[:80]}", None)

@app.on_message((filters.video | filters.document | filters.audio) & filters.private)
async def on_file(_, m):
    uid = m.from_user.id
    if user_get(uid).get("is_banned"):
        return
    if uid != OWNER_ID and not await is_member(uid):
        return await m.reply_text("⚠️ Join!", reply_markup=kb_join())
    media = m.video or m.document or m.audio
    status = await m.reply_text("⬇️...", reply_markup=kb_cancel())
    session_set(uid, {"cancel": False})
    try:
        name = safe_name(getattr(media, "file_name", None) or f"file_{int(time.time())}")
        path = os.path.join(DOWNLOAD_DIR, name)
        await m.download(path)
        session_set(uid, {"path": path, "name": name, "ext": get_ext(name), "size": os.path.getsize(path), "cancel": False})
        await safe_edit(status, f"✅ `{name}`", kb_upload())
    except Exception as e:
        session_clear(uid)
        await safe_edit(status, f"❌ {str(e)[:50]}", None)

@app.on_message(filters.photo & filters.private)
async def on_photo(_, m):
    uid = m.from_user.id
    if user_get(uid).get("is_banned"):
        return
    if uid != OWNER_ID and not await is_member(uid):
        return await m.reply_text("⚠️ Join!", reply_markup=kb_join())
    path = os.path.join(THUMB_DIR, f"{uid}.jpg")
    await m.download(path)
    user_get(uid)["thumb"] = path
    db_save()
    await m.reply_text("✅ Thumbnail saved!")

@app.on_callback_query()
async def on_cb(_, q):
    uid = q.from_user.id
    data = q.data
    user = user_get(uid)
    sess = session_get(uid)
    await q.answer()
    
    if user.get("is_banned"):
        return
    if data == "verify":
        if uid == OWNER_ID or await is_member(uid):
            return await safe_edit(q.message, "✅ Welcome!", kb_menu(uid))
        return await q.answer("❌ Join first!", show_alert=True)
    if data not in ["close"] and uid != OWNER_ID and not await is_member(uid):
        return await safe_edit(q.message, "⚠️ Join!", kb_join())
    if data == "close":
        try:
            await q.message.delete()
        except:
            pass
        return
    if data == "cancel":
        if sess:
            sess["cancel"] = True
            session_set(uid, sess)
            if sess.get("path") and os.path.exists(sess["path"]):
                try:
                    os.remove(sess["path"])
                except:
                    pass
        session_clear(uid)
        user["state"] = "none"
        db_save()
        return await safe_edit(q.message, "❌ Cancelled!", None)
    if data == "back":
        user["state"] = "none"
        db_save()
        return await safe_edit(q.message, "Menu", kb_menu(uid))
    if data == "thumb":
        return await safe_edit(q.message, "🖼️ Send photo", kb_thumb())
    if data == "thumb_view":
        t = user.get("thumb")
        if t and os.path.exists(t):
            await q.message.reply_photo(t)
        return
    if data == "thumb_del":
        t = user.get("thumb")
        if t and os.path.exists(t):
            os.remove(t)
        user["thumb"] = None
        db_save()
        return await safe_edit(q.message, "✅ Deleted!", kb_thumb())
    if data == "stats":
        if uid == OWNER_ID:
            return await safe_edit(q.message, f"👥 {len(DB['users'])} users\n🍪 {'✅' if COOKIES_PATH else '❌'}", kb_menu(uid))
        return await safe_edit(q.message, f"📊 {human_size(user.get('used',0))}/5GB\n{'👑 PRO' if user.get('is_pro') else ''}", kb_menu(uid))
    if data == "help":
        return await safe_edit(q.message, "Send link → Quality → Upload\n\n📊 5GB/day", kb_menu(uid))
    if data == "admin" and uid == OWNER_ID:
        return await safe_edit(q.message, "Admin", kb_admin())
    if data == "adm_stats" and uid == OWNER_ID:
        return await safe_edit(q.message, f"👥 {len(DB['users'])}\n🍪 {'✅' if COOKIES_PATH else '❌'}", kb_admin())
    if data == "adm_bc" and uid == OWNER_ID:
        user["state"] = "bc"
        db_save()
        return await safe_edit(q.message, "Send message:", kb_cancel())
    if data == "bc_yes" and uid == OWNER_ID:
        txt = user.get("bc_text", "")
        if txt:
            sent = 0
            for u in DB["users"]:
                try:
                    await app.send_message(int(u), txt)
                    sent += 1
                except:
                    pass
            user["bc_text"] = ""
            db_save()
            return await safe_edit(q.message, f"✅ Sent: {sent}", kb_admin())
    if data == "bc_no":
        user["state"] = "none"
        user["bc_text"] = ""
        db_save()
        return await safe_edit(q.message, "❌", kb_admin())
    if data == "adm_pro" and uid == OWNER_ID:
        user["state"] = "pro"
        db_save()
        return await safe_edit(q.message, "Send ID:", kb_cancel())
    if data == "adm_ban" and uid == OWNER_ID:
        user["state"] = "ban"
        db_save()
        return await safe_edit(q.message, "Send ID:", kb_cancel())
    if data == "adm_unban" and uid == OWNER_ID:
        user["state"] = "unban"
        db_save()
        return await safe_edit(q.message, "Send ID:", kb_cancel())
    if data.startswith("q_"):
        if not sess or not sess.get("url"):
            return await safe_edit(q.message, "❌ Expired!", None)
        quality = data[2:]
        try:
            path, title = await download(uid, sess["url"], q.message, quality)
            name = os.path.basename(path)
            session_set(uid, {"url": sess["url"], "path": path, "name": name, "ext": get_ext(name), "size": os.path.getsize(path), "cancel": False})
            await safe_edit(q.message, f"✅ `{name}`\n📦 {human_size(os.path.getsize(path))}", kb_upload())
        except Exception as e:
            session_clear(uid)
            await safe_edit(q.message, "❌ Cancelled!" if "CANCELLED" in str(e) else f"❌ {str(e)[:80]}", None)
        return
    if data == "rename" and sess:
        return await safe_edit(q.message, f"`{sess['name']}`", kb_rename())
    if data == "ren_keep" and sess:
        return await safe_edit(q.message, f"`{sess['name']}`", kb_upload())
    if data == "ren_custom" and sess:
        user["state"] = "rename"
        db_save()
        return await safe_edit(q.message, "Send name:", kb_cancel())
    if data == "back_up" and sess:
        return await safe_edit(q.message, f"`{sess['name']}`", kb_upload())
    if data in ["up_file", "up_video"]:
        if not sess or not sess.get("path") or not os.path.exists(sess["path"]):
            session_clear(uid)
            return await safe_edit(q.message, "❌ Not found!", None)
        try:
            await upload(uid, q.message, sess["path"], sess["name"], data == "up_video")
            try:
                os.remove(sess["path"])
            except:
                pass
            session_clear(uid)
            await safe_edit(q.message, "✅ Done!", kb_menu(uid))
        except Exception as e:
            await safe_edit(q.message, "❌ Cancelled!" if "CANCELLED" in str(e) else f"❌ {str(e)[:50]}", None)

# =======================
# MAIN
# =======================
async def health(_):
    return web.Response(text="OK")

async def main():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(THUMB_DIR, exist_ok=True)
    db_load()
    setup_cookies()
    await app.start()
    print(f"BOT STARTED! Owner: {OWNER_ID}")
    srv = web.Application()
    srv.add_routes([web.get("/", health)])
    runner = web.AppRunner(srv)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8000).start()
    await idle()
    await app.stop()

if __name__ == "__main__":
    app.run(main())
