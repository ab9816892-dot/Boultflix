import logging
import logging.config
from typing import Union, Optional, AsyncGenerator
from pyrogram import Client, types
from info import API_ID, API_HASH, BOT_TOKEN, SESSION

logger = logging.getLogger(__name__)

class BoultFlixBot(Client):

    def __init__(self):
        super().__init__(
            name=SESSION,
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            workers=10,
            plugins=dict(root="plugins"),
            sleep_threshold=10,
        )

    async def iter_messages(self, chat_id: Union[int, str], limit: int, offset: int = 0) -> Optional[AsyncGenerator["types.Message", None]]:
        current = offset
        while True:
            new_diff = min(200, limit - current)
            if new_diff <= 0:
                return
            messages = await self.get_messages(chat_id, list(range(current, current + new_diff + 1)))
            for message in messages:
                yield message
                current += 1

boultflix_bot = BoultFlixBot()

multi_clients = {}
work_loads = {}
