from aiohttp import web
import re
import math
import logging
import mimetypes
import asyncio
import json
from aiohttp.http_exceptions import BadStatusLine
from dreamxbotz.Bot import multi_clients, work_loads
from dreamxbotz.server.exceptions import FIleNotFound, InvalidHash
from dreamxbotz.util.custom_dl import ByteStreamer
from dreamxbotz.util.render_template import render_page
import info

logger = logging.getLogger(__name__)

routes = web.RouteTableDef()

# --- WORLD LANGUAGE WHITELIST & DICTIONARY ---
LANG_MAP = {
    "hin": "Hindi", "hindi": "Hindi",
    "eng": "English", "english": "English",
    "ben": "Bengali", "bengali": "Bengali", "bangla": "Bengali",
    "tam": "Tamil", "tamil": "Tamil",
    "tel": "Telugu", "telugu": "Telugu",
    "mal": "Malayalam", "malayalam": "Malayalam",
    "kan": "Kannada", "kannada": "Kannada",
    "mar": "Marathi", "marathi": "Marathi",
    "guj": "Gujarati", "gujarati": "Gujarati",
    "pan": "Punjabi", "pun": "Punjabi", "punjabi": "Punjabi",
    "urd": "Urdu", "urdu": "Urdu",
    "jpn": "Japanese", "jap": "Japanese", "japanese": "Japanese",
    "kor": "Korean", "korean": "Korean",
    "spa": "Spanish", "spanish": "Spanish",
    "fre": "French", "fra": "French", "french": "French",
    "ger": "German", "deu": "German", "german": "German",
    "rus": "Russian", "russian": "Russian",
    "chi": "Chinese", "zho": "Chinese", "chinese": "Chinese",
    "ara": "Arabic", "arabic": "Arabic",
    "ita": "Italian", "italian": "Italian",
    "por": "Portuguese", "portuguese": "Portuguese",
    "tha": "Thai", "thai": "Thai",
    "vie": "Vietnamese", "vietnamese": "Vietnamese",
    "ind": "Indonesian", "indonesian": "Indonesian",
    "tur": "Turkish", "turkish": "Turkish"
}

STREAM_METADATA_CACHE = {}

def clean_track_name(raw_title, raw_lang, track_type, index, fallback_filename=""):
    raw_title = str(raw_title or "").strip()
    raw_lang = str(raw_lang or "").strip().lower()
    fallback_filename = str(fallback_filename or "").lower()

    is_sdh = bool(re.search(r'\b(sdh|cc|hearing impaired)\b', raw_title, re.IGNORECASE))

    detected_lang = None

    # 1. Direct language code match
    if raw_lang in LANG_MAP:
        detected_lang = LANG_MAP[raw_lang]

    # 2. Check title against whitelist
    if not detected_lang and raw_title:
        title_lower = raw_title.lower()
        for k, v in LANG_MAP.items():
            if re.search(r'\b' + re.escape(k) + r'\b', title_lower):
                detected_lang = v
                break

    # 3. Jugad: If language is undefined / missing in MKV, check movie filename
    if not detected_lang and fallback_filename:
        for k, v in LANG_MAP.items():
            if re.search(r'\b' + re.escape(k) + r'\b', fallback_filename):
                detected_lang = v
                break

    # 4. Fallback if completely unknown
    if not detected_lang:
        detected_lang = f"{'Audio' if track_type == 'audio' else 'Subtitle'} {index}"

    if is_sdh and track_type == 'subtitle' and "[SDH]" not in detected_lang:
        return f"{detected_lang} [SDH]"

    return detected_lang

async def probe_stream_metadata(stream_url, file_name=""):
    if stream_url in STREAM_METADATA_CACHE:
        return STREAM_METADATA_CACHE[stream_url]

    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-probesize", "3000000",
        "-analyzeduration", "3000000",
        stream_url
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3.5)
        probe = json.loads(stdout.decode('utf-8'))

        audios = []
        subs = []
        a_idx, s_idx = 1, 1

        for s in probe.get("streams", []):
            c_type = s.get("codec_type")
            tags = s.get("tags", {})
            title = tags.get("title", "")
            lang = tags.get("language", "")

            if c_type == "audio":
                clean_name = clean_track_name(title, lang, "audio", a_idx, file_name)
                audios.append(clean_name)
                a_idx += 1
            elif c_type == "subtitle":
                clean_name = clean_track_name(title, lang, "subtitle", s_idx, file_name)
                subs.append(clean_name)
                s_idx += 1

        res = {
            "audios": audios if audios else ["Default Audio"],
            "subs": subs
        }
        STREAM_METADATA_CACHE[stream_url] = res
        return res
    except Exception as e:
        logger.warning(f"Fast ffprobe skipped/timed out: {e}")
        return {"audios": ["Default Audio"], "subs": []}

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
            id = int(re.search(r"(\d+)(?:\/\S+)?", path).group(1))
            secure_hash = request.rel_url.query.get("hash")

        html_content = await render_page(id, secure_hash)

        # Build stream URL for zero-load probe
        stream_url = f"{request.scheme}://{request.host}/{secure_hash}{id}"
        meta = await probe_stream_metadata(stream_url)

        # Seamless injection into HTML (Zero Template Breakage)
        script_inject = f"""
        <script>
            window.__SERVER_AUDIOS__ = {json.dumps(meta['audios'])};
            window.__SERVER_SUBS__ = {json.dumps(meta['subs'])};
        </script>
        """
        html_content = html_content.replace("</head>", f"{script_inject}\n</head>")

        return web.Response(text=html_content, content_type='text/html')
    except InvalidHash as e:
        raise web.HTTPForbidden(text=e.message)
    except FIleNotFound as e:
        raise web.HTTPNotFound(text=e.message)
    except (AttributeError, BadStatusLine, ConnectionResetError):
        raise web.HTTPBadRequest()
    except Exception as e:
        logger.critical(e.with_traceback(None))
        raise web.HTTPInternalServerError(text=str(e))

class_cache = {}

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
        logger.critical(e.with_traceback(None))
        raise web.HTTPInternalServerError(text=str(e))

async def media_streamer(request: web.Request, id: int, secure_hash: str):
    range_header = request.headers.get("Range", None)
    is_download = request.rel_url.query.get("dl") == "1"

    client_indices = sorted(work_loads.keys(), key=lambda k: work_loads[k])
    file_id = None
    tg_connect = None
    active_client_idx = 0

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

            file_id = await connector.get_file_properties(id)
            tg_connect = connector
            active_client_idx = idx
            break
        except Exception as err:
            logger.warning(f"Client {idx} failed file lookup: {err}. Trying fallback...")
            continue

    if not file_id or not tg_connect:
        raise FIleNotFound("File properties could not be retrieved from any client.")

    if file_id.unique_id[:6] != secure_hash:
        raise InvalidHash

    file_size = file_id.file_size

    if range_header:
        from_bytes, until_bytes = range_header.replace("bytes=", "").split("-")
        from_bytes = int(from_bytes)
        until_bytes = int(until_bytes) if until_bytes else file_size - 1
    else:
        from_bytes = request.http_range.start or 0
        until_bytes = (request.http_range.stop or file_size) - 1

    if (until_bytes >= file_size) or (from_bytes < 0) or (until_bytes < from_bytes):
        return web.Response(
            status=416,
            body="416: Range not satisfiable",
            headers={"Content-Range": f"bytes */{file_size}"},
        )

    chunk_size = 1024 * 1024
    until_bytes = min(until_bytes, file_size - 1)

    offset = from_bytes - (from_bytes % chunk_size)
    first_part_cut = from_bytes - offset
    last_part_cut = until_bytes % chunk_size + 1

    req_length = until_bytes - from_bytes + 1
    part_count = math.ceil((until_bytes + 1) / chunk_size) - math.floor(offset / chunk_size)
    body = tg_connect.yield_file(
        file_id, active_client_idx, offset, first_part_cut, last_part_cut, part_count, chunk_size
    )

    mime_type = file_id.mime_type
    original_file_name = file_id.file_name

    if not mime_type:
        mime_type = mimetypes.guess_type(original_file_name)[0] or "video/mp4"

    safe_name = original_file_name.replace('"', '').replace("'", "")
    formatted_file_name = f"Boultflix - {safe_name}"
    disposition = "attachment" if is_download else "inline"

    resp_headers = {
        "Content-Type": f"{mime_type}",
        "Content-Length": str(req_length),
        "Content-Disposition": f'{disposition}; filename="{formatted_file_name}"',
        "Accept-Ranges": "bytes",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "Range, Content-Type",
        "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges",
    }

    if range_header:
        resp_headers["Content-Range"] = f"bytes {from_bytes}-{until_bytes}/{file_size}"

    return web.Response(
        status=206 if range_header else 200,
        body=body,
        headers=resp_headers
    )
