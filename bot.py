import plugins.listener  # noqa: F401
import logging
import logging.config
import time
import asyncio
import gc
from pathlib import Path
from datetime import date, datetime
from pyrogram import idle, __version__
from pyrogram.raw.all import layer
from pyrogram.errors import FloodWait
from aiohttp import web

from database.ia_filterdb import Media, Media2
from database.users_chats_db import db
from info import MULTIPLE_DB, ON_HEROKU, LOG_STR, LOG_CHANNEL, PORT, TIMEZONE, TZ
from utils import temp
from Script import script
from plugins import web_server, check_expired_premium, keep_alive
from bot_core.Bot import boultflix_bot as bot
from bot_core.util.keepalive import ping_server
from bot_core.Bot.clients import initialize_clients

logger = logging.getLogger(__name__)
logging.config.fileConfig('logging.conf')
logging.getLogger().setLevel(logging.INFO)
logging.getLogger("pyrogram").setLevel(logging.ERROR)
logging.getLogger("imdbpy").setLevel(logging.ERROR)
logging.getLogger("aiohttp").setLevel(logging.ERROR)
logging.getLogger("aiohttp.web").setLevel(logging.ERROR)
logging.getLogger("pymongo").setLevel(logging.WARNING)

try:
    import uvloop
    uvloop.install()
    logger.info("uvloop installed as event-loop policy.")
except ImportError:
    pass

botStartTime = time.time()

async def mongo_keepalive_heartbeat():
    while True:
        try:
            await db.db.command('ping')
        except Exception:
            pass
        await asyncio.sleep(60)

async def memory_cleanup():
    while True:
        await asyncio.sleep(180)
        gc.collect()

def get_plugins_names(plugins_dir="plugins"):
    plugins_path = Path(plugins_dir)
    if not plugins_path.exists():
        logger.warning("Plugins directory not found: %s", plugins_path)
        return []

    return [
        ".".join(file.relative_to(plugins_path).with_suffix("").parts)
        for file in sorted(plugins_path.rglob("*.py"))
        if file.name != "__init__.py"
    ]

async def boultflix_start():
    logger.info('\n\nInitializing Boultflix Bot Engine')
    bot.loop = asyncio.get_running_loop()
    await bot.start()
    bot_info = await bot.get_me()
    bot.username = bot_info.username
    await initialize_clients()
    plugins_names = get_plugins_names()
    if plugins_names:
        logger.info("Plugins Loaded: %d plugins found.", len(plugins_names))
    else:
        logger.warning("No plugins found.")

    if ON_HEROKU:
        asyncio.create_task(ping_server())
    b_users, b_chats = await db.get_banned()
    temp.BANNED_USERS = set(b_users)
    temp.BANNED_CHATS = set(b_chats)
    await Media.ensure_indexes()
    if MULTIPLE_DB:
        await Media2.ensure_indexes()
        logger.info("Multiple Database Mode On. Now Files Will Be Saved In Second DB If First DB Is Full")
    else:
        logger.info("Single DB Mode On ! Files Will Be Saved In First Database")

    me = bot_info
    temp.ME = me.id
    temp.U_NAME = me.username
    temp.B_NAME = me.first_name
    temp.B_LINK = me.mention
    bot.username = '@' + me.username
    asyncio.create_task(check_expired_premium(bot))

    logger.info(f"{me.first_name} with Pyrogram v{__version__} (Layer {layer}) started on @{me.username}.")
    logger.info(LOG_STR)
    logger.info(script.LOGO)
    today = date.today()
    now = datetime.now(TZ)
    current_time = now.strftime("%I:%M:%S %p")
    try:
        await bot.send_message(chat_id=LOG_CHANNEL, text=script.RESTART_TXT.format(temp.B_LINK, today, current_time, TIMEZONE))
    except Exception as e:
        logger.warning(f"Could not send restart log to LOG_CHANNEL: {e}")

    app = web.AppRunner(await web_server())
    await app.setup()
    bind_address = "0.0.0.0"
    await web.TCPSite(app, bind_address, PORT).start()
    asyncio.create_task(keep_alive())
    asyncio.create_task(mongo_keepalive_heartbeat())
    asyncio.create_task(memory_cleanup())

    try:
        await idle()
    finally:
        await app.cleanup()
        await bot.stop()

if __name__ == '__main__':
    try:
        logger.info('Boultflix service started...')
        asyncio.run(boultflix_start())
    except FloodWait as e:
        logger.info(f"FloodWait! Sleeping for {e.value} seconds then restarting...")
        time.sleep(e.value)
        asyncio.run(boultflix_start())
    except KeyboardInterrupt:
        logger.info('Service stopped. Bye.')
