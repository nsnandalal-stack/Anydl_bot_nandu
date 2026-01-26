# (In-memory state for rename)
rename_state = {}

@app.on_message(filters.private & (filters.text | filters.forwarded))
async def handle_media(c, m):
    uid = m.from_user.id
    text = m.text or ""
    
    # 1. Check Rename State
    if uid in rename_state:
        # Perform actual file rename on disk... logic here
        pass

    # 2. Check for Links
    if "youtube.com" in text or "youtu.be" in text:
        # Send resolution buttons (1080p, 720p, MP3 etc)
        pass
