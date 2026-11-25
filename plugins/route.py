from aiohttp import web
import logging

logger = logging.getLogger(__name__)

routes = web.RouteTableDef()

@routes.get("/", allow_head=True)
async def root_route_handler(request: web.Request):
    return web.Response(
        text="<h1>Boultflix Service Active</h1><p>Telegram Bot is running smoothly.</p>",
        content_type="text/html",
        status=200
    )

@routes.get("/health", allow_head=True)
async def health_check_handler(request: web.Request):
    return web.json_response({"status": "healthy", "service": "Boultflix", "uptime": "active"})
