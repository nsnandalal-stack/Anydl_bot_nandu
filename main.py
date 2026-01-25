from aiohttp import web

# Dummy web server for Koyeb health checks
async def health_check(request):
    return web.Response(text="Bot is Alive")

async def main():
    if not os.path.exists(DOWNLOAD_DIR): os.makedirs(DOWNLOAD_DIR)
    
    # 1. Start Bot
    await app.start()
    
    # 2. Start Scheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(lambda: DB["active"].clear(), "interval", hours=1)
    scheduler.start()
    
    # 3. Start Dummy Web Server on Port 8000
    server = web.Application()
    server.add_routes([web.get('/', health_check)])
    runner = web.AppRunner(server)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8000)
    await site.start()
    
    print("Bot is Started Successfully with Health Check on Port 8000")
    await idle()

if __name__ == "__main__":
    app.run(main())
