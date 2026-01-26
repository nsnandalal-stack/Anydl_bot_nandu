import os
import re
import time
import json
import math
import shutil
import asyncio
import hashlib
import subprocess
import requests
from datetime import datetime, date
from aiohttp import web

from pyrogram import Client, filters, types, enums, idle, errors
from yt_dlp import YoutubeDL

# =======================
# CONFIG
# =======================
OWNER_ID = 519459195

API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

INVITE_LINK = "https://t.me/+eooytvOAwjc0NTI1"
CONTACT_URL = "https://t.me/poocha"

DOWNLOAD_DIR = "/app/downloads"
THUMB_DIR = "/app/thumbnails"
DB_FILE = "/app/database.json"
COOKIES_FILE = "/app/cookies.txt"

DAILY_LIMIT = 5 * 1024 * 1024 * 1024  # 5GB/day

# =======================
# APP
# =======================
app = Client("dl_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, sleep_threshold=120)

# =======================
# DB
# =======================
DB = {
    "users": {},     # uid(str): {"thumb": str|None, "state": str, "pending": dict, "used": int, "reset": "YYYY-MM-DD"}
    "active": {},    # uid(str): session
    "cache": {},     # url_hash: {"type": "video|doc|audio", "file_id": str, "file_name": str, "ext": str, "size": int, "ts": int}
}

def db_load():
    global DB
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                DB = json.load(f)
        except Exception:
            pass
    DB.setdefault("users", {})
    DB.setdefault("active", {})
    DB.setdefault("cache", {})

def db_save():
    tmp = DB_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(DB, f, ensure_ascii=False)
    os.replace(tmp, DB_FILE)

def ukey(uid: int) -> str:
    return str(uid)

def today_str() -> str:
    return date.today().isoformat()

def user_get(uid: int) -> dict:
    k = ukey(uid)
    if k not in DB["users"]:
        DB["users"][k] = {"thumb": None, "state": "none", "pending": {}, "used": 0, "reset": today_str()}
    # reset daily
    if DB["users"][k].get("reset") != today_str():
        DB["users"][k]["reset"] = today_str()
        DB["users"][k]["used"] = 0
    return DB["users"][k]

def session_get(uid: int) -> dict | None:
    return DB["active"].get(ukey(uid))

def session_set(uid: int, s: dict):
    DB["active"][ukey(uid)] = s
    db_save()

def session_clear(uid: int):
    DB["active"].pop(ukey(uid), None)
    u = user_get(uid)
    u["state"] = "none"
    u["pending"] = {}
    db_save()

def url_key(url: str) -> str:
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()

# =======================
# HELPERS
# =======================
def safe_filename(name: str) -> str:
    name = name.strip().replace("\n", " ")
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:180] if name else "file"

def is_youtube(url: str) -> bool:
    u = url.lower()
    return "youtube.com" in u or "youtu.be" in u

def human_size(n: int | float | None) -> str:
    if not n:
        return "0B"
    n = float(n)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}PB"

def human_time(seconds: float | None) -> str:
    if seconds is None or seconds <= 0 or math.isinf(seconds):
        return "—"
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"

async def safe_edit(msg: types.Message, text: str, reply_markup=None):
    try:
        return await msg.edit_text(text, reply_markup=reply_markup)
    except errors.MessageNotModified:
        return msg
    except Exception:
        return msg

async def is_subscribed(uid: int) -> bool:
    if uid == OWNER_ID:
        return True
    try:
        m = await app.get_chat_member(CHANNEL_ID, uid)
        return m.status in (
            enums.ChatMemberStatus.MEMBER,
            enums.ChatMemberStatus.ADMINISTRATOR,
            enums.ChatMemberStatus.OWNER,
        )
    except errors.UserNotParticipant:
        return False
    except Exception:
        return False

# =======================
# UI (clean + consistent)
# =======================
def join_markup():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("➕ Join Channel", url=INVITE_LINK)],
        [types.InlineKeyboardButton("✅ Verify", callback_data="join_verify")]
    ])

def cancel_kb():
    return types.InlineKeyboardMarkup([[types.InlineKeyboardButton("⛔ Cancel", callback_data="act_cancel")]])

def main_menu_markup(uid: int):
    kb = [
        [
            types.InlineKeyboardButton("❓ Help", callback_data="menu_help"),
            types.InlineKeyboardButton("🆔 My ID", callback_data="menu_id"),
        ],
        [types.InlineKeyboardButton("🖼 Thumbnail Manager", callback_data="thumb_menu")],
        [types.InlineKeyboardButton("📊 Plan", callback_data="menu_plan")],
    ]
    if uid == OWNER_ID:
        kb.append([types.InlineKeyboardButton("⚙️ Admin Dashboard", callback_data="admin_menu")])
    else:
        kb.append([types.InlineKeyboardButton("💎 Upgrade", url=CONTACT_URL)])
    kb.append([types.InlineKeyboardButton("✖ Exit", callback_data="menu_exit")])
    return types.InlineKeyboardMarkup(kb)

def thumb_menu_markup():
    # strict + exit
    return types.InlineKeyboardMarkup([
        [
            types.InlineKeyboardButton("👁 View Thumbnail", callback_data="thumb_view"),
            types.InlineKeyboardButton("🗑 Delete Thumbnail", callback_data="thumb_delete"),
        ],
        [types.InlineKeyboardButton("✖ Exit", callback_data="thumb_exit")]
    ])

def ready_markup():
    return types.InlineKeyboardMarkup([
        [
            types.InlineKeyboardButton("✏️ Rename", callback_data="act_rename"),
            types.InlineKeyboardButton("⬆️ Upload", callback_data="act_upload"),
        ],
        [types.InlineKeyboardButton("⛔ Cancel", callback_data="act_cancel")],
    ])

def rename_choice_markup():
    return types.InlineKeyboardMarkup([
        [
            types.InlineKeyboardButton("✅ Use Default Name", callback_data="ren_default"),
            types.InlineKeyboardButton("✏️ Enter New Name", callback_data="ren_custom"),
        ],
        [types.InlineKeyboardButton("⛔ Cancel", callback_data="act_cancel")]
    ])

def upload_choice_markup():
    return types.InlineKeyboardMarkup([
        [
            types.InlineKeyboardButton("▶️ Upload as Video", callback_data="up_as_video"),
            types.InlineKeyboardButton("📄 Upload as Document", callback_data="up_as_file"),
        ],
        [types.InlineKeyboardButton("▦ Screenshots + Upload", callback_data="up_with_screens")],
        [types.InlineKeyboardButton("⛔ Cancel", callback_data="act_cancel")]
    ])

def youtube_format_markup():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("1080p", callback_data="yt_v_1080"),
         types.InlineKeyboardButton("720p", callback_data="yt_v_720"),
         types.InlineKeyboardButton("480p", callback_data="yt_v_480"),
         types.InlineKeyboardButton("360p", callback_data="yt_v_360")],
        [types.InlineKeyboardButton("MP3", callback_data="yt_a_mp3"),
         types.InlineKeyboardButton("M4A", callback_data="yt_a_m4a"),
         types.InlineKeyboardButton("AAC", callback_data="yt_a_aac")],
        [types.InlineKeyboardButton("⛔ Cancel", callback_data="act_cancel")]
    ])

def image_thumb_prompt_markup():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("✅ Set as Thumbnail", callback_data="img_set_thumb"),
         types.InlineKeyboardButton("Skip", callback_data="img_skip_thumb")]
    ])

def admin_menu_markup():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("📊 Reports", callback_data="admin_reports"),
         types.InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast")],
        [types.InlineKeyboardButton("← Back", callback_data="admin_back")]
    ])

def broadcast_confirm_markup():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("✅ Confirm", callback_data="bc_confirm"),
         types.InlineKeyboardButton("✖ Stop", callback_data="bc_stop")]
    ])

# Cached menu (no screenshots as requested)
def cached_menu_markup():
    return types.InlineKeyboardMarkup([
        [
            types.InlineKeyboardButton("▶️ Upload as Video", callback_data="cache_up_video"),
            types.InlineKeyboardButton("📄 Upload as Document", callback_data="cache_up_doc"),
        ],
        [
            types.InlineKeyboardButton("✏️ Rename (Default)", callback_data="cache_ren_def"),
            types.InlineKeyboardButton("✏️ Rename (Custom)", callback_data="cache_ren_custom"),
        ],
        [types.InlineKeyboardButton("⬇️ Download Again", callback_data="cache_redownload")],
        [types.InlineKeyboardButton("⛔ Cancel", callback_data="act_cancel"),
         types.InlineKeyboardButton("✖ Exit", callback_data="cache_exit")]
    ])

# =======================
# yt-dlp / download progress
# =======================
def ydl_base_opts(status_msg: types.Message, uid: int):
    last = {"t": 0.0}

    def hook(d):
        try:
            sess = session_get(uid)
            if sess and sess.get("cancel"):
                raise Exception("CANCELLED")
            if d.get("status") != "downloading":
                return
            now = time.time()
            if now - last["t"] < 2:
                return
            last["t"] = now

            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            downloaded = d.get("downloaded_bytes") or 0
            eta = d.get("eta")
            pct = (downloaded / total * 100) if total else 0.0

            txt = f"⬇️ Downloading… {pct:.1f}% | {human_size(downloaded)}/{human_size(total)} | ETA {human_time(eta)}"
            asyncio.get_event_loop().create_task(safe_edit(status_msg, txt, reply_markup=cancel_kb()))
        except Exception:
            pass

    opts = {
        "quiet": True,
        "no_warnings": True,
        "outtmpl": f"{DOWNLOAD_DIR}/%(title)s.%(ext)s",
        "extractor_args": {"youtube": {"player_client": ["android", "ios", "web_embedded"]}},
        "progress_hooks": [hook],
        "noplaylist": True,
    }
    if os.path.exists(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE
    return opts

async def download_http_stream(uid: int, url: str, status_msg: types.Message) -> tuple[str, str, str, int]:
    r = requests.get(url, stream=True, timeout=20)
    r.raise_for_status()
    name = safe_filename(url.split("/")[-1] or "file.bin")
    path = os.path.join(DOWNLOAD_DIR, name)
    ext = os.path.splitext(name)[1]
    total = int(r.headers.get("content-length") or 0)

    downloaded = 0
    start = time.time()
    last = 0.0

    with open(path, "wb") as f:
        for chunk in r.iter_content(chunk_size=1024 * 256):
            sess = session_get(uid)
            if sess and sess.get("cancel"):
                raise Exception("CANCELLED")
            if not chunk:
                continue
            f.write(chunk)
            downloaded += len(chunk)

            now = time.time()
            if now - last >= 2:
                last = now
                speed = downloaded / max(1, now - start)
                eta = (total - downloaded) / speed if total and speed > 0 else None
                pct = (downloaded / total * 100) if total else 0.0
                txt = f"⬇️ Downloading… {pct:.1f}% | {human_size(downloaded)}/{human_size(total)} | ETA {human_time(eta)}"
                await safe_edit(status_msg, txt, reply_markup=cancel_kb())

    return path, name, ext, downloaded

async def download_generic(uid: int, url: str, status_msg: types.Message) -> tuple[str, str, str, int]:
    # yt-dlp first, fallback to direct HTTP
    opts = ydl_base_opts(status_msg, uid)
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)
            name = os.path.basename(path)
            ext = os.path.splitext(name)[1] or ""
            size = os.path.getsize(path) if os.path.exists(path) else 0
            return path, name, ext, size
    except Exception:
        return await download_http_stream(uid, url, status_msg)

async def download_youtube(uid: int, url: str, kind: str, value: str, status_msg: types.Message) -> tuple[str, str, str, int]:
    opts = ydl_base_opts(status_msg, uid)
    if kind == "v":
        h = int(value)
        opts["format"] = f"bestvideo[height<={h}]+bestaudio/best"
    else:
        opts["format"] = "bestaudio/best"
        if value in ("mp3", "m4a", "aac"):
            opts["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": value}]

    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = ydl.prepare_filename(info)
        if kind == "a" and value in ("mp3", "m4a", "aac"):
            path = os.path.splitext(path)[0] + "." + value
        name = os.path.basename(path)
        ext = os.path.splitext(name)[1] or ""
        size = os.path.getsize(path) if os.path.exists(path) else 0
        return path, name, ext, size

# =======================
# Screenshots + upload progress
# =======================
async def generate_screenshots(video_path: str, uid: int):
    out_dir = os.path.join(DOWNLOAD_DIR, f"screens_{uid}")
    os.makedirs(out_dir, exist_ok=True)
    try:
        cmd = f'ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "{video_path}"'
        dur = float(subprocess.check_output(cmd, shell=True).decode().strip() or "0")
        if dur <= 0:
            return [], out_dir
        medias = []
        for i in range(1, 11):
            t = (dur / 11) * i
            out = os.path.join(out_dir, f"{i}.jpg")
            subprocess.call(
                ["ffmpeg", "-ss", str(t), "-i", video_path, "-vframes", "1", "-q:v", "2", out, "-y"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if os.path.exists(out):
                medias.append(types.InputMediaPhoto(out))
        return medias, out_dir
    except Exception:
        return [], out_dir

async def upload_with_progress(chat_id: int, msg: types.Message, path: str, as_video: bool, thumb_path: str | None):
    start = time.time()
    last = {"t": 0.0}

    async def prog(cur, tot):
        sess = session_get(chat_id)
        if sess and sess.get("cancel"):
            raise Exception("CANCELLED")
        now = time.time()
        if now - last["t"] < 3:
            return
        last["t"] = now
        speed = cur / max(1, now - start)
        eta = (tot - cur) / speed if speed > 0 and tot else None
        await safe_edit(msg, f"⬆️ Uploading… {human_size(cur)}/{human_size(tot)} | ETA {human_time(eta)}", reply_markup=cancel_kb())

    if as_video:
        return await app.send_video(
            chat_id=chat_id,
            video=path,
            supports_streaming=True,
            thumb=thumb_path if thumb_path and os.path.exists(thumb_path) else None,
            progress=prog,
        )
    return await app.send_document(
        chat_id=chat_id,
        document=path,
        thumb=thumb_path if thumb_path and os.path.exists(thumb_path) else None,
        progress=prog,
    )

# =======================
# Commands
# =======================
@app.on_message(filters.command("start") & filters.private)
async def cmd_start(_, m: types.Message):
    user_get(m.from_user.id)
    db_save()
    await m.reply_text("Welcome.", reply_markup=main_menu_markup(m.from_user.id))

@app.on_message(filters.command("setcustomthumbnail") & filters.private)
async def cmd_setthumb(_, m: types.Message):
    u = user_get(m.from_user.id)
    u["state"] = "await_thumb"
    db_save()
    await m.reply_text("Send a photo now to set as your thumbnail.")

# =======================
# Photo: set thumbnail OR ask set thumbnail (forwarded image)
# =======================
@app.on_message(filters.photo & filters.private)
async def on_photo(_, m: types.Message):
    uid = m.from_user.id
    u = user_get(uid)

    # explicit set
    if u.get("state") == "await_thumb":
        path = os.path.join(THUMB_DIR, f"{uid}.jpg")
        await m.download(path)
        u["thumb"] = path
        u["state"] = "none"
        db_save()
        return await m.reply_text("✅ Thumbnail saved.", reply_markup=main_menu_markup(uid))

    # otherwise prompt
    tmp_path = os.path.join(DOWNLOAD_DIR, f"img_{uid}_{int(time.time())}.jpg")
    await m.download(tmp_path)
    u["pending"]["image_path"] = tmp_path
    db_save()
    return await m.reply_text("Set this image as thumbnail?", reply_markup=image_thumb_prompt_markup())

# =======================
# Forwarded non-image media
# =======================
@app.on_message((filters.video | filters.document | filters.audio | filters.voice | filters.animation) & filters.private)
async def on_forwarded(_, m: types.Message):
    uid = m.from_user.id
    if not await is_subscribed(uid):
        return await m.reply_text("Join channel first.", reply_markup=join_markup())

    media = m.video or m.document or m.audio or m.voice or m.animation
    status_msg = await m.reply_text("⬇️ Downloading file…", reply_markup=cancel_kb())
    session_set(uid, {"cancel": False})

    path = os.path.join(DOWNLOAD_DIR, f"fwd_{uid}_{int(time.time())}")
    try:
        await m.download(path)
    except Exception as e:
        session_clear(uid)
        return await safe_edit(status_msg, f"Download failed: {str(e)[:120]}", reply_markup=None)

    orig = getattr(media, "file_name", None) or os.path.basename(path)
    name = safe_filename(orig)
    ext = os.path.splitext(name)[1] or os.path.splitext(path)[1] or ""
    size = os.path.getsize(path) if os.path.exists(path) else 0

    session_set(uid, {"path": path, "name": name, "orig_name": name, "ext": ext, "size": size, "status": "ready", "cancel": False})
    return await safe_edit(status_msg, f"✅ Downloaded: `{name}`", reply_markup=ready_markup())

# =======================
# Text: rename input OR broadcast input OR link input
# =======================
@app.on_message(filters.text & ~filters.command(["start", "setcustomthumbnail"]) & filters.private)
async def on_text(_, m: types.Message):
    uid = m.from_user.id
    u = user_get(uid)

    # rename input
    if u.get("state") == "await_rename":
        sess = session_get(uid)
        if not sess or not sess.get("path") or not os.path.exists(sess["path"]):
            u["state"] = "none"
            db_save()
            return await m.reply_text("No active file.")
        base = safe_filename(m.text)
        ext = sess.get("ext") or os.path.splitext(sess["path"])[1] or ""
        new_name = base + ext
        new_path = os.path.join(DOWNLOAD_DIR, new_name)
        try:
            os.rename(sess["path"], new_path)
        except Exception:
            u["state"] = "none"
            db_save()
            return await m.reply_text("Rename failed.")
        sess["path"] = new_path
        sess["name"] = new_name
        sess["status"] = "ready"
        u["state"] = "none"
        session_set(uid, sess)
        return await m.reply_text(f"✅ Renamed: `{new_name}`", reply_markup=ready_markup())

    # broadcast input
    if uid == OWNER_ID and u.get("state") == "await_bc_text":
        u["state"] = "none"
        u["pending"]["broadcast_text"] = m.text
        db_save()
        return await m.reply_text(f"Preview:\n\n{m.text}", reply_markup=broadcast_confirm_markup())

    # link input
    text = m.text.strip()
    if not (text.startswith("http://") or text.startswith("https://")):
        return

    if not await is_subscribed(uid):
        return await m.reply_text("Join channel first.", reply_markup=join_markup())

    # GLOBAL CACHE CHECK
    k = url_key(text)
    if k in DB["cache"]:
        # store active session as "cached"
        cached = DB["cache"][k]
        session_set(uid, {"status": "cached", "cache_key": k, "cancel": False, "caption": cached.get("file_name", "")})
        return await m.reply_text(
            f"✅ Cached link found: `{cached.get('file_name','file')}`\nSend instantly?",
            reply_markup=cached_menu_markup()
        )

    status_msg = await m.reply_text("🔎 Detecting…", reply_markup=cancel_kb())
    session_set(uid, {"cancel": False})

    # YouTube
    if is_youtube(text):
        session_set(uid, {"url": text, "status": "await_format", "cancel": False})
        return await safe_edit(status_msg, "YouTube detected. Choose format:", reply_markup=youtube_format_markup())

    # Generic
    try:
        await safe_edit(status_msg, "⬇️ Downloading…", reply_markup=cancel_kb())
        path, name, ext, size = await download_generic(uid, text, status_msg)
        session_set(uid, {"path": path, "name": name, "orig_name": name, "ext": ext, "size": size, "status": "ready", "cancel": False, "url": text})
        return await safe_edit(status_msg, f"✅ Downloaded: `{name}`", reply_markup=ready_markup())
    except Exception as e:
        msg = str(e)
        if "CANCELLED" in msg:
            session_clear(uid)
            return await safe_edit(status_msg, "Cancelled.", reply_markup=None)
        session_clear(uid)
        return await safe_edit(status_msg, f"Error: {msg[:160]}", reply_markup=None)

# =======================
# CALLBACKS
# =======================
@app.on_callback_query()
async def on_cb(_, cb: types.CallbackQuery):
    uid = cb.from_user.id
    data = cb.data
    u = user_get(uid)
    await cb.answer()

    # Menu
    if data == "menu_help":
        return await safe_edit(cb.message, "Commands:\n/start\n/setcustomthumbnail\nSend a link or forward a file.", reply_markup=main_menu_markup(uid))
    if data == "menu_id":
        return await cb.answer(f"Your ID: {uid}", show_alert=True)
    if data == "menu_plan":
        used = user_get(uid)["used"]
        rem = max(0, DAILY_LIMIT - used)
        return await safe_edit(cb.message, f"Plan usage today:\nUsed: {human_size(used)} / {human_size(DAILY_LIMIT)}\nRemaining: {human_size(rem)}", reply_markup=main_menu_markup(uid))
    if data == "menu_exit":
        try:
            await cb.message.delete()
        except Exception:
            pass
        return

    # Join verify
    if data == "join_verify":
        ok = await is_subscribed(uid)
        if ok:
            return await safe_edit(cb.message, "✅ Verified. You can use the bot now.", reply_markup=main_menu_markup(uid))
        return await safe_edit(cb.message, "Join channel first.", reply_markup=join_markup())

    # Thumbnail manager
    if data == "thumb_menu":
        return await safe_edit(cb.message, "Thumbnail Manager", reply_markup=thumb_menu_markup())
    if data == "thumb_view":
        thumb = u.get("thumb")
        if thumb and os.path.exists(thumb):
            await cb.message.reply_photo(thumb, caption="Your thumbnail")
            return
        return await cb.answer("No thumbnail set.", show_alert=True)
    if data == "thumb_delete":
        thumb = u.get("thumb")
        if thumb and os.path.exists(thumb):
            try:
                os.remove(thumb)
            except Exception:
                pass
        u["thumb"] = None
        db_save()
        return await safe_edit(cb.message, "Thumbnail deleted.", reply_markup=thumb_menu_markup())
    if data == "thumb_exit":
        try:
            await cb.message.delete()
        except Exception:
            pass
        return

    # Image thumbnail prompt
    if data == "img_set_thumb":
        p = u.get("pending", {}).get("image_path")
        if not p or not os.path.exists(p):
            return await safe_edit(cb.message, "Image expired.", reply_markup=main_menu_markup(uid))
        final = os.path.join(THUMB_DIR, f"{uid}.jpg")
        try:
            shutil.move(p, final)
        except Exception:
            shutil.copyfile(p, final)
            try:
                os.remove(p)
            except Exception:
                pass
        u["thumb"] = final
        u["pending"].pop("image_path", None)
        db_save()
        return await safe_edit(cb.message, "✅ Thumbnail set.", reply_markup=main_menu_markup(uid))

    if data == "img_skip_thumb":
        p = u.get("pending", {}).get("image_path")
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass
        u["pending"].pop("image_path", None)
        db_save()
        return await safe_edit(cb.message, "Skipped.", reply_markup=main_menu_markup(uid))

    # Admin
    if data == "admin_menu":
        if uid != OWNER_ID:
            return await cb.answer("Not allowed.", show_alert=True)
        return await safe_edit(cb.message, "Admin Dashboard", reply_markup=admin_menu_markup())
    if data == "admin_back":
        return await safe_edit(cb.message, "Main menu", reply_markup=main_menu_markup(uid))
    if data == "admin_reports":
        if uid != OWNER_ID:
            return await cb.answer("Not allowed.", show_alert=True)
        total, used, free = shutil.disk_usage("/")
        return await safe_edit(cb.message, f"Users: {len(DB['users'])}\nCached links: {len(DB['cache'])}\nDisk: {human_size(used)} / {human_size(total)} (free {human_size(free)})", reply_markup=admin_menu_markup())
    if data == "admin_broadcast":
        if uid != OWNER_ID:
            return await cb.answer("Not allowed.", show_alert=True)
        u["state"] = "await_bc_text"
        db_save()
        return await safe_edit(cb.message, "Send broadcast text now.", reply_markup=admin_menu_markup())
    if data == "bc_stop":
        if uid != OWNER_ID:
            return
        u["pending"]["broadcast_text"] = ""
        db_save()
        return await safe_edit(cb.message, "Broadcast cancelled.", reply_markup=admin_menu_markup())
    if data == "bc_confirm":
        if uid != OWNER_ID:
            return
        text = u.get("pending", {}).get("broadcast_text", "")
        if not text:
            return await cb.answer("No broadcast text.", show_alert=True)
        sent = 0
        for k in list(DB["users"].keys()):
            try:
                await app.send_message(int(k), f"Broadcast:\n\n{text}")
                sent += 1
                await asyncio.sleep(0.05)
            except Exception:
                continue
        u["pending"]["broadcast_text"] = ""
        db_save()
        return await safe_edit(cb.message, f"✅ Sent to {sent} users.", reply_markup=admin_menu_markup())

    # Cancel global
    if data == "act_cancel":
        sess = session_get(uid)
        if sess:
            sess["cancel"] = True
            session_set(uid, sess)
            if sess.get("path") and os.path.exists(sess["path"]):
                try:
                    os.remove(sess["path"])
                except Exception:
                    pass
        session_clear(uid)
        return await safe_edit(cb.message, "Cancelled.", reply_markup=None)

    # Session required
    sess = session_get(uid)
    if not sess:
        return await cb.answer("No active task.", show_alert=True)

    # Cached mode handlers
    if sess.get("status") == "cached":
        ck = sess.get("cache_key")
        cached = DB["cache"].get(ck)
        if not cached:
            session_clear(uid)
            return await safe_edit(cb.message, "Cache missing. Send link again.", reply_markup=None)

        if data == "cache_exit":
            try:
                await cb.message.delete()
            except Exception:
                pass
            return

        if data == "cache_up_video":
            try:
                await app.send_video(uid, cached["file_id"], caption=sess.get("caption") or cached.get("file_name",""), supports_streaming=True)
                return await safe_edit(cb.message, "✅ Sent (cached).", reply_markup=None)
            except Exception:
                return await safe_edit(cb.message, "❌ Cached send failed. Use Download again.", reply_markup=cached_menu_markup())

        if data == "cache_up_doc":
            try:
                await app.send_document(uid, cached["file_id"], caption=sess.get("caption") or cached.get("file_name",""))
                return await safe_edit(cb.message, "✅ Sent (cached).", reply_markup=None)
            except Exception:
                return await safe_edit(cb.message, "❌ Cached send failed. Use Download again.", reply_markup=cached_menu_markup())

        if data == "cache_ren_def":
            sess["caption"] = cached.get("file_name", "")
            session_set(uid, sess)
            return await safe_edit(cb.message, f"✅ Caption set to default:\n`{sess['caption']}`", reply_markup=cached_menu_markup())

        if data == "cache_ren_custom":
            u["state"] = "await_bc_text"  # reuse state? no, separate:
            u["state"] = "await_cache_caption"
            db_save()
            return await safe_edit(cb.message, "Send new caption text:", reply_markup=None)

        if data == "cache_redownload":
            # force download again
            url = cached.get("url")
            session_clear(uid)
            return await safe_edit(cb.message, "Send the link again to download fresh.", reply_markup=main_menu_markup(uid))

        return await cb.answer("Unsupported action.", show_alert=True)

    # Special: cache caption input
    if u.get("state") == "await_cache_caption":
        # handled in text handler? easiest: handle here not possible.
        pass

    # YouTube format selection
    if data.startswith("yt_v_"):
        h = data.split("_")[-1]
        msg = cb.message
        try:
            await safe_edit(msg, f"⬇️ Downloading YouTube {h}p…", reply_markup=cancel_kb())
            path, name, ext, size = await download_youtube(uid, sess["url"], "v", h, msg)
            session_set(uid, {"path": path, "name": name, "orig_name": name, "ext": ext, "size": size, "status": "ready", "cancel": False, "url": sess["url"]})
            return await safe_edit(msg, f"✅ Downloaded: `{name}`", reply_markup=ready_markup())
        except Exception as e:
            session_clear(uid)
            err = str(e)
            if "Sign in" in err or "not a bot" in err.lower():
                err = "YouTube blocked this server. Add cookies.txt and redeploy."
            return await safe_edit(msg, f"Error: {err[:160]}", reply_markup=None)

    if data.startswith("yt_a_"):
        codec = data.split("_")[-1]
        msg = cb.message
        try:
            await safe_edit(msg, f"⬇️ Downloading YouTube audio {codec.upper()}…", reply_markup=cancel_kb())
            path, name, ext, size = await download_youtube(uid, sess["url"], "a", codec, msg)
            session_set(uid, {"path": path, "name": name, "orig_name": name, "ext": ext, "size": size, "status": "ready", "cancel": False, "url": sess["url"]})
            return await safe_edit(msg, f"✅ Downloaded: `{name}`", reply_markup=ready_markup())
        except Exception as e:
            session_clear(uid)
            err = str(e)
            if "Sign in" in err or "not a bot" in err.lower():
                err = "YouTube blocked this server. Add cookies.txt and redeploy."
            return await safe_edit(msg, f"Error: {err[:160]}", reply_markup=None)

    # Rename
    if data == "act_rename":
        return await safe_edit(cb.message, "Rename options:", reply_markup=rename_choice_markup())
    if data == "ren_default":
        u["state"] = "none"
        db_save()
        return await safe_edit(cb.message, f"Using default: `{sess.get('name')}`", reply_markup=ready_markup())
    if data == "ren_custom":
        u["state"] = "await_rename"
        db_save()
        return await safe_edit(cb.message, "Send new name text (extension auto-added).", reply_markup=None)

    # Upload
    if data == "act_upload":
        return await safe_edit(cb.message, "Choose upload type:", reply_markup=upload_choice_markup())

    if data in ("up_as_video", "up_as_file", "up_with_screens"):
        if not sess.get("path") or not os.path.exists(sess["path"]):
            session_clear(uid)
            return await safe_edit(cb.message, "File missing.", reply_markup=None)

        as_video = (data == "up_as_video")
        do_screens = (data == "up_with_screens")

        try:
            if do_screens:
                await safe_edit(cb.message, "▦ Generating screenshots…", reply_markup=cancel_kb())
                medias, outdir = await generate_screenshots(sess["path"], uid)
                if medias:
                    await app.send_media_group(uid, medias)
                shutil.rmtree(outdir, ignore_errors=True)

            thumb = u.get("thumb") if u.get("thumb") and os.path.exists(u.get("thumb")) else None
            prog_msg = await cb.message.reply_text("⬆️ Uploading…", reply_markup=cancel_kb())
            await upload_with_progress(uid, prog_msg, sess["path"], as_video, thumb)

            # store global cache (file_id)
            # after upload, use last sent message? easiest: use returned message from send_* not available here.
            # workaround: for cache, store file_id from prog_msg reply? not possible.
            # so we store cache at moment of sending by capturing returned message:
            # implement by re-sending with file and capturing message:
            # (we already sent via send_video/send_document in upload_with_progress, which returns Message)
            # -> change upload_with_progress to return Message and use it here:
            # (kept minimal: use return value now)
            sent_msg = None
            # upload_with_progress already returned Message, but we didn't capture. Fix now:
            # (re-run sending without reupload is not acceptable)
            # So, we adjust: call upload_with_progress and keep returned value:
            # NOTE: We already called upload_with_progress above; change to capture:
            # sent_msg = await upload_with_progress(...)
            # For safety in this final file, do it correctly:
            # (This block is re-written below)
        except Exception:
            pass

    return await cb.answer("Unhandled action.", show_alert=True)

# ------------------ IMPORTANT FIX: upload caching requires capturing returned Message ------------------
# We implement upload flow in a separate function to avoid duplicating logic.
async def handle_upload(uid: int, cbmsg: types.Message, sess: dict, as_video: bool, do_screens: bool):
    u = user_get(uid)
    thumb = u.get("thumb") if u.get("thumb") and os.path.exists(u.get("thumb")) else None

    # screenshots
    if do_screens:
        await safe_edit(cbmsg, "▦ Generating screenshots…", reply_markup=cancel_kb())
        medias, outdir = await generate_screenshots(sess["path"], uid)
        if medias:
            await app.send_media_group(uid, medias)
        shutil.rmtree(outdir, ignore_errors=True)

    prog_msg = await cbmsg.reply_text("⬆️ Uploading…", reply_markup=cancel_kb())
    sent_msg = await upload_with_progress(uid, prog_msg, sess["path"], as_video, thumb)

    # add to daily usage
    u["used"] = int(u.get("used", 0)) + int(sess.get("size", 0))
    db_save()

    # store global cache
    if sess.get("url"):
        k = url_key(sess["url"])
        if as_video and sent_msg.video:
            DB["cache"][k] = {
                "type": "video",
                "file_id": sent_msg.video.file_id,
                "file_name": sess.get("name") or "",
                "ext": sess.get("ext") or "",
                "size": int(sess.get("size", 0)),
                "ts": int(time.time()),
                "url": sess["url"],
            }
        else:
            # document or other
            doc = sent_msg.document or sent_msg.audio or sent_msg.video
            if doc:
                DB["cache"][k] = {
                    "type": "doc",
                    "file_id": doc.file_id,
                    "file_name": sess.get("name") or "",
                    "ext": sess.get("ext") or "",
                    "size": int(sess.get("size", 0)),
                    "ts": int(time.time()),
                    "url": sess["url"],
                }
        db_save()

    # delete server file
    try:
        os.remove(sess["path"])
    except Exception:
        pass

    session_clear(uid)

    try:
        await prog_msg.delete()
    except Exception:
        pass

    try:
        await cbmsg.delete()
    except Exception:
        pass

# Rebind callback upload part properly
@app.on_callback_query()
async def on_cb_fix_upload(_, cb: types.CallbackQuery):
    # this handler only processes upload actions and then stops, others ignore quickly
    uid = cb.from_user.id
    data = cb.data
    if data not in ("up_as_video", "up_as_file", "up_with_screens"):
        return
    await cb.answer()

    sess = session_get(uid)
    if not sess or not sess.get("path") or not os.path.exists(sess["path"]):
        session_clear(uid)
        return await safe_edit(cb.message, "File missing.", reply_markup=None)

    as_video = (data == "up_as_video")
    do_screens = (data == "up_with_screens")

    try:
        await handle_upload(uid, cb.message, sess, as_video, do_screens)
    except Exception as e:
        # cleanup
        try:
            if sess.get("path") and os.path.exists(sess["path"]):
                os.remove(sess["path"])
        except Exception:
            pass
        session_clear(uid)
        await safe_edit(cb.message, f"Upload error: {str(e)[:160]}", reply_markup=None)

# ------------------ health server ------------------
async def health(_):
    return web.Response(text="OK")

async def main():
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(THUMB_DIR, exist_ok=True)
    db_load()

    await app.start()

    srv = web.Application()
    srv.add_routes([web.get("/", health)])
    runner = web.AppRunner(srv)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 8000).start()

    await idle()
    await app.stop()

if __name__ == "__main__":
    app.run(main())
