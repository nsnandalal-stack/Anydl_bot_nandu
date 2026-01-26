import os
import re
import time
import json
import math
import shutil
import asyncio
import requests
import subprocess
from aiohttp import web

from pyrogram import Client, filters, types, enums, idle, errors
from yt_dlp import YoutubeDL

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

app = Client("dl_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN, sleep_threshold=120)

DB = {"users": {}, "active": {}}

# ------------------ persistence ------------------
def db_load():
    global DB
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                DB = json.load(f)
        except Exception:
            DB = {"users": {}, "active": {}}
    DB.setdefault("users", {})
    DB.setdefault("active", {})

def db_save():
    tmp = DB_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(DB, f, ensure_ascii=False)
    os.replace(tmp, DB_FILE)

def ukey(uid: int) -> str:
    return str(uid)

def user_get(uid: int) -> dict:
    k = ukey(uid)
    if k not in DB["users"]:
        DB["users"][k] = {"thumb": None, "state": "none", "pending": {}}
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

# ------------------ helpers ------------------
def is_youtube(url: str) -> bool:
    u = url.lower()
    return "youtube.com" in u or "youtu.be" in u

def safe_filename(name: str) -> str:
    name = name.strip().replace("\n", " ")
    name = re.sub(r'[\\/*?:"<>|]', "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name[:180] if name else "file"

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

# ------------------ UI (consistent + clearer labels) ------------------
def join_markup():
    return types.InlineKeyboardMarkup([
        [types.InlineKeyboardButton("➕ Join Channel", url=INVITE_LINK)],
        [types.InlineKeyboardButton("✅ Verify", callback_data="join_verify")]
    ])

def main_menu_markup(uid: int):
    kb = [
        [
            types.InlineKeyboardButton("❓ Help", callback_data="menu_help"),
            types.InlineKeyboardButton("🆔 My ID", callback_data="menu_id"),
        ],
        [types.InlineKeyboardButton("🖼️ Thumbnail Manager", callback_data="thumb_menu")],
    ]
    if uid == OWNER_ID:
        kb.append([types.InlineKeyboardButton("⚙️ Admin Dashboard", callback_data="admin_menu")])
    else:
        kb.append([types.InlineKeyboardButton("💎 Upgrade", url=CONTACT_URL)])
    kb.append([types.InlineKeyboardButton("✖ Exit", callback_data="menu_exit")])
    return types.InlineKeyboardMarkup(kb)

def thumb_menu_markup():
    return types.InlineKeyboardMarkup([
        [
            types.InlineKeyboardButton("👁️ View Thumbnail", callback_data="thumb_view"),
            types.InlineKeyboardButton("🗑️ Delete Thumbnail", callback_data="thumb_delete"),
        ],
        [types.InlineKeyboardButton("✖ Exit", callback_data="thumb_exit")]
    ])

def cancel_kb():
    return types.InlineKeyboardMarkup([[types.InlineKeyboardButton("⛔ Cancel", callback_data="act_cancel")]])

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
            types.InlineKeyboardButton("▶️ Video", callback_data="up_as_video"),
            types.InlineKeyboardButton("📄 File", callback_data="up_as_file"),
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

# ------------------ yt-dlp (YouTube support) ------------------
def ydl_base_opts():
    opts = {
        "quiet": True,
        "no_warnings": True,
        "outtmpl": f"{DOWNLOAD_DIR}/%(title)s.%(ext)s",
        "extractor_args": {"youtube": {"player_client": ["android", "ios", "web_embedded"]}},
        "noplaylist": True,
    }
    if os.path.exists(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE
    return opts

# yt-dlp progress -> edit message with percent/eta
def make_ydl_hook(uid: int, status_msg: types.Message):
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
            speed = d.get("speed") or 0
            eta = d.get("eta")

            pct = (downloaded / total * 100) if total else 0
            txt = f"⬇️ Downloading… {pct:.1f}% | {human_size(downloaded)}/{human_size(total)} | ETA {human_time(eta)}"
            asyncio.get_event_loop().create_task(safe_edit(status_msg, txt, reply_markup=cancel_kb()))
        except Exception:
            pass

    return hook

async def download_generic_best_effort(uid: int, url: str, status_msg: types.Message) -> tuple[str, str, str]:
    """
    Try yt-dlp first. If it fails, try direct HTTP streaming download.
    Returns (path, name, ext)
    """
    # Try yt-dlp first
    opts = ydl_base_opts()
    opts["progress_hooks"] = [make_ydl_hook(uid, status_msg)]
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = ydl.prepare_filename(info)
            name = os.path.basename(path)
            ext = os.path.splitext(name)[1] or os.path.splitext(path)[1] or ""
            return path, name, ext
    except Exception:
        # Fallback: direct HTTP
        r = requests.get(url, stream=True, timeout=20)
        r.raise_for_status()
        name = safe_filename(url.split("/")[-1] or "file.bin")
        ext = os.path.splitext(name)[1]
        path = os.path.join(DOWNLOAD_DIR, name)
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
                    pct = (downloaded / total * 100) if total else 0
                    txt = f"⬇️ Downloading… {pct:.1f}% | {human_size(downloaded)}/{human_size(total)} | ETA {human_time(eta)}"
                    await safe_edit(status_msg, txt, reply_markup=cancel_kb())

        return path, name, ext

async def download_youtube(uid: int, url: str, kind: str, value: str, status_msg: types.Message) -> tuple[str, str, str]:
    opts = ydl_base_opts()
    opts["progress_hooks"] = [make_ydl_hook(uid, status_msg)]
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
        return path, name, ext

# ------------------ screenshots + upload progress ------------------
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

# ------------------ health ------------------
async def health(_):
    return web.Response(text="OK")

# =======================
# COMMANDS
# =======================
@app.on_message(filters.command("start") & filters.private)
async def cmd_start(_, m: types.Message):
    user_get(m.from_user.id)
    db_save()
    await m.reply_text(
        "Ready." if m.from_user.id == OWNER_ID else "Welcome.",
        reply_markup=main_menu_markup(m.from_user.id),
    )

# =======================
# TEXT FLOW (rename input / link input / broadcast input)
# =======================
@app.on_message(filters.text & ~filters.command(["start"]) & filters.private)
async def on_text(_, m: types.Message):
    uid = m.from_user.id
    u = user_get(uid)

    # Rename input
    if u.get("state") == "await_rename":
        sess = session_get(uid)
        if not sess or not sess.get("path") or not os.path.exists(sess["path"]):
            u["state"] = "none"
            db_save()
            return await m.reply_text("No active file. Send again.")
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

    # Broadcast input
    if uid == OWNER_ID and u.get("state") == "await_bc_text":
        u["state"] = "none"
        u["pending"]["broadcast_text"] = m.text
        db_save()
        return await m.reply_text(f"Preview:\n\n{m.text}", reply_markup=broadcast_confirm_markup())

    # Link input
    text = m.text.strip()
    if not (text.startswith("http://") or text.startswith("https://")):
        return

    if not await is_subscribed(uid):
        return await m.reply_text("Join channel first.", reply_markup=join_markup())

    status_msg = await m.reply_text("🔎 Detecting…", reply_markup=cancel_kb())
    session_set(uid, {"cancel": False})

    if is_youtube(text):
        session_set(uid, {"url": text, "status": "await_format", "cancel": False})
        return await safe_edit(status_msg, "YouTube link detected. Choose format:", reply_markup=youtube_format_markup())

    # Generic link
    try:
        await safe_edit(status_msg, "⬇️ Starting download…", reply_markup=cancel_kb())
        path, name, ext = await download_generic_best_effort(uid, text, status_msg)
        session_set(uid, {"path": path, "name": name, "orig_name": name, "ext": ext, "status": "ready", "cancel": False})
        return await safe_edit(status_msg, f"✅ Downloaded: `{name}`", reply_markup=ready_markup())
    except Exception as e:
        msg = str(e)
        if "CANCELLED" in msg:
            session_clear(uid)
            return await safe_edit(status_msg, "Cancelled.", reply_markup=None)
        session_clear(uid)
        return await safe_edit(status_msg, f"Error: {msg[:160]}", reply_markup=None)

# =======================
# FORWARDED FILES (non-image)
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

    session_set(uid, {"path": path, "name": name, "orig_name": name, "ext": ext, "status": "ready", "cancel": False})
    return await safe_edit(status_msg, f"✅ Downloaded: `{name}`", reply_markup=ready_markup())

# =======================
# CALLBACKS (ALL BUTTONS)
# =======================
@app.on_callback_query()
async def on_cb(_, cb: types.CallbackQuery):
    uid = cb.from_user.id
    data = cb.data
    u = user_get(uid)
    await cb.answer()

    # ----- menu
    if data == "menu_help":
        return await safe_edit(cb.message, "Commands: /start /setcustomthumbnail\nSend link or forward file.", reply_markup=main_menu_markup(uid))
    if data == "menu_id":
        return await cb.answer(f"Your ID: {uid}", show_alert=True)
    if data == "menu_exit":
        try:
            await cb.message.delete()
        except Exception:
            pass
        return

    # ----- join verify
    if data == "join_verify":
        ok = await is_subscribed(uid)
        if ok:
            return await safe_edit(cb.message, "✅ Verified. You can use the bot now.", reply_markup=main_menu_markup(uid))
        return await safe_edit(cb.message, "Join channel first.", reply_markup=join_markup())

    # ----- thumbnail manager
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
        # refresh immediately (no dead response)
        return await safe_edit(cb.message, "Thumbnail deleted.", reply_markup=thumb_menu_markup())
    if data == "thumb_exit":
        try:
            await cb.message.delete()
        except Exception:
            pass
        return

    # ----- cancel (global)
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

    # ----- session required below
    sess = session_get(uid)
    if not sess:
        return await cb.answer("No active task.", show_alert=True)

    # ----- YouTube format buttons
    if data.startswith("yt_v_"):
        h = data.split("_")[-1]
        msg = cb.message
        try:
            await safe_edit(msg, f"⬇️ Downloading YouTube {h}p…", reply_markup=cancel_kb())
            path, name, ext = await download_youtube(uid, sess["url"], "v", h, msg)
            session_set(uid, {"path": path, "name": name, "orig_name": name, "ext": ext, "status": "ready", "cancel": False})
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
            path, name, ext = await download_youtube(uid, sess["url"], "a", codec, msg)
            session_set(uid, {"path": path, "name": name, "orig_name": name, "ext": ext, "status": "ready", "cancel": False})
            return await safe_edit(msg, f"✅ Downloaded: `{name}`", reply_markup=ready_markup())
        except Exception as e:
            session_clear(uid)
            err = str(e)
            if "Sign in" in err or "not a bot" in err.lower():
                err = "YouTube blocked this server. Add cookies.txt and redeploy."
            return await safe_edit(msg, f"Error: {err[:160]}", reply_markup=None)

    # ----- rename flow
    if data == "act_rename":
        return await safe_edit(cb.message, "Rename options:", reply_markup=rename_choice_markup())

    if data == "ren_default":
        u["state"] = "none"
        db_save()
        return await safe_edit(cb.message, f"Using default name: `{sess.get('name')}`", reply_markup=ready_markup())

    if data == "ren_custom":
        u["state"] = "await_rename"
        db_save()
        return await safe_edit(cb.message, "Send new name text (extension will be added automatically).", reply_markup=None)

    # ----- upload flow
    if data == "act_upload":
        return await safe_edit(cb.message, "Choose upload type:", reply_markup=upload_choice_markup())

    if data in ("up_as_video", "up_as_file", "up_with_screens"):
        if not sess.get("path") or not os.path.exists(sess["path"]):
            session_clear(uid)
            return await safe_edit(cb.message, "File missing. Send again.", reply_markup=None)

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

            # delete file from server immediately after upload
            try:
                os.remove(sess["path"])
            except Exception:
                pass

            session_clear(uid)

            try:
                await prog_msg.delete()
            except Exception:
                pass

            return await safe_edit(cb.message, "✅ Uploaded and cleaned.", reply_markup=None)

        except Exception as e:
            # delete file even on error
            try:
                if sess.get("path") and os.path.exists(sess["path"]):
                    os.remove(sess["path"])
            except Exception:
                pass
            session_clear(uid)
            return await safe_edit(cb.message, f"Upload error: {str(e)[:160]}", reply_markup=None)

    # Anything else
    return await cb.answer("Unhandled.", show_alert=True)

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
