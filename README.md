# Telegram Media Downloader Bot

A professional, minimal, and production-ready media downloader bot built with Python and Pyrogram. Optimized for deployment on **Koyeb** using Docker.

## 🌟 Features
- **Smart Detection:** Automatically handles YouTube, Instagram, TikTok, Twitter, and direct file links using `yt-dlp`.
- **YouTube Specialized:** Choose between Video and Audio (MP3) with file size previews.
- **Admin Dashboard:** Monitor disk usage, active sessions, and user stats via `/admin`.
- **Force Subscription:** Users must join your channel to use the bot.
- **Usage Limits:** 5GB daily limit for free users (configurable).
- **Safety First:** Built-in NSFW keyword filter to prevent server abuse.
- **Auto-Cleanup:** Automatically deletes files from the server after 1 hour to save disk space.
- **Owner Monitoring:** Sends real-time notifications to the owner when users download files.

## 🛠 Prerequisites
1. **API_ID & API_HASH:** Get them from [my.telegram.org](https://my.telegram.org).
2. **BOT_TOKEN:** Get it from [@BotFather](https://t.me/BotFather).
3. **CHANNEL_ID:** Add your bot to your channel as an Admin and get the ID (e.g., `-100123456789`).

## 🚀 Deployment on Koyeb

1. **GitHub Setup:**
   - Create a new private repository on GitHub.
   - Upload the 4 files (`main.py`, `requirements.txt`, `Dockerfile`, `.env.example`) to the repository.

2. **Koyeb Setup:**
   - Go to [Koyeb.com](https://app.koyeb.com) and create a new App.
   - Select **GitHub** as the deployment method.
   - Choose your repository.
   - In the **Environment Variables** section, add the following:
     - `API_ID`: Your Telegram API ID.
     - `API_HASH`: Your Telegram API Hash.
     - `BOT_TOKEN`: Your Telegram Bot Token.
     - `CHANNEL_ID`: Your Channel ID (must start with -100).
   - Click **Deploy**.

## ⚙️ Environment Variables
| Variable | Description |
| :--- | :--- |
| `API_ID` | Telegram API ID from my.telegram.org |
| `API_HASH` | Telegram API Hash from my.telegram.org |
| `BOT_TOKEN` | Bot token from @BotFather |
| `CHANNEL_ID` | The ID of the channel for Force Subscription |

## 🎮 Admin Commands
These commands only work for the hardcoded owner ID (`519459195`):

- `/admin` - Opens the graphical admin dashboard (Stats, Disk, Links).
- `/pro [user_id]` - Upgrades a specific user to "Pro" (Unlimited usage + No NSFW filter).
- `/ban [user_id]` - Bans a user from the bot.
- `/broadcast [message]` - Sends a message to all users in the database.

## 📂 Project Structure
- `main.py`: The core logic of the bot.
- `requirements.txt`: Python dependencies.
- `Dockerfile`: Instructions to build the server environment.
- `.env.example`: Template for configuration.

## ⚠️ Important Note
This bot is designed for **Koyeb's ephemeral storage**. Because Koyeb resets files on every restart, the user database is kept in memory. If the bot restarts, the "Pro" status of users will reset unless the code is modified to use a persistent database (like MongoDB or PostgreSQL).
