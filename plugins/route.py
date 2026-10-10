from aiohttp import web
from urllib.parse import quote
import re
import math
import logging
import mimetypes
from aiohttp.http_exceptions import BadStatusLine
from dreamxbotz.Bot import multi_clients, work_loads
from dreamxbotz.server.exceptions import FIleNotFound, InvalidHash
from dreamxbotz.util.custom_dl import ByteStreamer
from dreamxbotz.util.render_template import render_page
import info

logger = logging.getLogger(__name__)

routes = web.RouteTableDef()

# In-memory caches for ultra-fast startup and instant seeking
class_cache = {}
file_props_cache = {}  # id -> file_id (Telegram metadata never changes for a message)

@routes.get("/favicon.ico")
async def favicon_route_handler(request):
    return web.FileResponse('dreamxbotz/template/favicon.ico')

@routes.get("/", allow_head=True)
async def root_route_handler(request):
    try:
        with open("dreamxbotz/template/Invalid.html", "r", encoding="utf-8") as f:
            return web.Response(text=f.read(), content_type="text/html")
    except Exception:
        return web.Response(
            text="<h1>Restricted Cloud Node</h1><p>Visit official Telegram bot: <a href='https://t.me/BoultflixMovieBot'>@BoultflixMovieBot</a></p>",
            content_type="text/html"
        )

@routes.get(r"/watch/{path:\S+}", allow_head=True)
async def watch_handler(request: web.Request):
    try:
        path = request.match_info["path"]
        match = re.search(r"^([a-zA-Z0-9_-]{6})(\d+)$", path)
        if match:
            secure_hash = match.group(1)
            id = int(match.group(2))
        else:
            id_match = re.search(r"(\d+)(?:\/\S+)?", path)
            if not id_match:
                raise web.HTTPNotFound(text="Not found")
            id = int(id_match.group(1))
            secure_hash = request.rel_url.query.get("hash")

        if request.method == "HEAD":
            return web.Response(status=200, content_type='text/html')

        # HTML template render (No ffprobe injection)
        return web.Response(text=await render_page(id, secure_hash), content_type='text/html')
    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)
    except FIleNotFound as e:
        raise web.HTTPNotFound(text=e.message)
    except (AttributeError, BadStatusLine, ConnectionResetError):
        raise web.HTTPBadRequest()
    except Exception as e:
        logger.critical(str(e))
        raise web.HTTPInternalServerError(text=str(e))

@routes.options(r"/{path:\S+}")
async def options_handler(request: web.Request):
    return web.Response(
        status=204,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
            "Access-Control-Allow-Headers": "Range, Content-Type, Accept-Encoding",
            "Access-Control-Max-Age": "86400",
        }
    )

@routes.get(r"/{path:\S+}", allow_head=True)
async def stream_handler(request: web.Request):
    try:
        path = request.match_info["path"]
        match = re.search(r"^([a-zA-Z0-9_-]{6})(\d+)$", path)
        if match:
            secure_hash = match.group(1)
            id = int(match.group(2))
        else:
            id_match = re.search(r"(\d+)(?:\/\S+)?", path)
            if not id_match:
                raise web.HTTPNotFound(text="Not found")
            id = int(id_match.group(1))
            secure_hash = request.rel_url.query.get("hash")

        return await media_streamer(request, id, secure_hash)
    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)
    except FIleNotFound as e:
        raise web.HTTPNotFound(text=e.message)
    except web.HTTPNotFound:
        raise
    except (AttributeError, BadStatusLine, ConnectionResetError):
        raise web.HTTPBadRequest()
    except Exception as e:
        logger.critical(str(e))
        raise web.HTTPInternalServerError(text=str(e))

async def media_streamer(request: web.Request, id: int, secure_hash: str):
    range_header = request.headers.get("Range", None)
    is_download = request.rel_url.query.get("dl") == "1"

    client_indices = sorted(work_loads.keys(), key=lambda k: work_loads[k])
    file_id = None
    tg_connect = None
    active_client_idx = client_indices[0] if client_indices else 0

    # 1. Check in-memory metadata cache (Instant 0.001ms lookup without hitting Telegram repeatedly)
    if id in file_props_cache:
        file_id = file_props_cache[id]
        candidate_client = multi_clients.get(active_client_idx)
        if candidate_client in class_cache:
            tg_connect = class_cache[candidate_client]
        else:
            tg_connect = ByteStreamer(candidate_client)
            class_cache[candidate_client] = tg_connect
    else:
        # Multi-client automatic failover retry logic
        for idx in client_indices:
            candidate_client = multi_clients.get(idx)
            if not candidate_client:
                continue
            try:
                if candidate_client in class_cache:
                    connector = class_cache[candidate_client]
                else:
                    connector = ByteStreamer(candidate_client)
                    class_cache[candidate_client] = connector

                props = await connector.get_file_properties(id)
                if props and getattr(props, "file_size", None):
                    file_id = props
                    tg_connect = connector
                    active_client_idx = idx
                    file_props_cache[id] = file_id
                    if len(file_props_cache) > 10000:
                        file_props_cache.clear()
                    break
            except Exception as err:
                logger.warning(f"Client {idx} failed file lookup: {err}. Trying fallback...")
                continue

    if not file_id or not tg_connect:
        raise FIleNotFound("File properties could not be retrieved from any client.")

    if not secure_hash or file_id.unique_id[:6] != secure_hash:
        raise InvalidHash

    file_size = file_id.file_size

    # 2. Robust RFC 7233 Range parser (Fixes ValueError crash on suffix ranges like bytes=-500000)
    if range_header:
        range_match = re.search(r"bytes\s*=\s*(\d+)?\s*-\s*(\d+)?", range_header)
        if range_match:
            start_str, end_str = range_match.groups()
            if start_str and end_str:
                from_bytes = int(start_str)
                until_bytes = int(end_str)
            elif start_str:
                from_bytes = int(start_str)
                until_bytes = file_size - 1
            elif end_str:
                suffix_len = int(end_str)
                from_bytes = max(0, file_size - suffix_len)
                until_bytes = file_size - 1
            else:
                from_bytes = 0
                until_bytes = file_size - 1
        else:
            from_bytes = 0
            until_bytes = file_size - 1
    else:
        from_bytes = 0
        until_bytes = file_size - 1

    # Bounds validation and clamping
    if from_bytes >= file_size or from_bytes < 0:
        return web.Response(
            status=416,
            text="416: Range Not Satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    until_bytes = min(until_bytes, file_size - 1)
    if until_bytes < from_bytes:
        return web.Response(
            status=416,
            text="416: Range Not Satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    req_length = until_bytes - from_bytes + 1

    mime_type = file_id.mime_type
    original_file_name = file_id.file_name or "video.mp4"
    if not mime_type:
        mime_type = mimetypes.guess_type(original_file_name)[0] or "video/mp4"

    clean_name = re.sub(r'[\r\n\\"/]', '_', original_file_name)
    formatted_file_name = f"Boultflix - {clean_name}"
    encoded_name = quote(formatted_file_name)
    disposition = "attachment" if is_download else "inline"
    disposition_header = f"{disposition}; filename=\"{formatted_file_name}\"; filename*=UTF-8''{encoded_name}"

    resp_headers = {
        "Content-Disposition": disposition_header,
        "Accept-Ranges": "bytes",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "Range, Content-Type, Accept-Encoding",
        "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges",
    }

    if range_header:
        resp_headers["Content-Range"] = f"bytes {from_bytes}-{until_bytes}/{file_size}"

    # 3. StreamResponse with explicit Content-Length (Forces Chrome to show exact MB/GB total & progress %)
    response = web.StreamResponse(
        status=206 if range_header else 200,
        headers=resp_headers
    )
    response.content_type = mime_type
    response.content_length = req_length

    await response.prepare(request)

    # Instant response on HEAD requests without starting Telegram downloads
    if request.method == "HEAD":
        return response

    chunk_size = 1024 * 1024  # 1MB
    offset = from_bytes - (from_bytes % chunk_size)
    first_part_cut = from_bytes - offset
    last_part_cut = (until_bytes % chunk_size) + 1
    part_count = math.ceil((until_bytes + 1) / chunk_size) - math.floor(offset / chunk_size)

    body = tg_connect.yield_file(
        file_id, active_client_idx, offset, first_part_cut, last_part_cut, part_count, chunk_size
    )

    try:
        async for chunk in body:
            await response.write(chunk)
    except (ConnectionResetError, ConnectionError, web.GracefulExit):
        # Client aborted playback/seek/download cleanly
        pass
    except Exception as err:
        logger.warning(f"Stream client disconnected: {err}")

    return response
