import os
import re
import html
import time
import asyncio
import logging
from collections import defaultdict
from typing import Optional, Tuple
from urllib.parse import quote_plus
from datetime import datetime

from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import MessageIdInvalid, MessageNotModified, FloodWait
from pymongo.errors import DuplicateKeyError

from info import CHANNELS, MOVIE_UPDATE_CHANNEL, BAD_WORDS, ADMINS
from Script import script
from database.users_chats_db import db
from database.ia_filterdb import save_file
from utils import temp
from plugins.extras.Imdbposter import get_movie_detailsx

logger = logging.getLogger(__name__)

BOT_USERNAME_FALLBACK = "BoultFlixMovieBot"

PIRACY_STRIP_REGEX = re.compile(
    r'\b(?:'
    r'ds4k|ds1080p|ds720p|ds480p|line[\s._-]*aud(?:io)?|line|org[\s._-]*aud(?:io)?|org|'
    r'clean[\s._-]*aud(?:io)?|mic|hq[\s._-]*line|hq[\s._-]*mic|'
    r'e[\s._-]?subs?|m[\s._-]?subs?|esu|msu|esb|msb|hc[\s._-]?subs?|hardsub|softsub|subbed|sub|subs|dub|dubbed|multi|dual|hq|hdrip|prehd|'
    r'x264|x265|h264|h265|hevc|avc|10bit|10-bit|8bit|dovi|hdr10\+|hdr10|hdr|dv|'
    r'ddp5\.1|ddp5|dd5\.1|ddp|dd|ac3|eac3|aac|atmos|dts|truehd|mp3|'
    r'webrip|web-dl|webdl|web|dl|bluray|brrip|bdrip|remux|imax|proper|repack|'
    r'hdcam|hdtc|camrip|cam|ts|tc|hdts|telesync|dvdscr|dvdrip|predvd|'
    r'reloaded|uncut|the[\s._-]*rule|rule|special[\s._-]*edition|'
    r'2160p|1440p|1080p|720p|540p|480p|360p|240p|4k|2k|'
    r'v1|v2|v3|v4|v5|v6|ver\d+|version\d+'
    r')\b',
    re.IGNORECASE
)

_BASE_IGNORE_WORDS = {
    "rarbg", "dub", "sub", "sample", "mkv", "mp4", "avi", "aac", "ac3", "eac3", "ddp", "ddp5", "atmos", "dts",
    "combined", "esub", "esubs", "msub", "msubs", "proper", "repack", "unrated", "extended", "imax",
    "remux", "10bit", "10-bit", "x264", "x265", "h264", "h265", "hevc", "avc", "dovi", "hdr", "hdr10", "hdr10+",
    "web", "dl", "reloaded", "uncut", "ds4k", "line",
    "hdcam", "hdtc", "camrip", "cam", "ts", "tc", "hdts", "telesync", "dvdscr", "dvdrip", "predvd", "webrip",
    "web-dl", "tvrip", "hdtv", "web dl", "webdl", "bluray", "brrip", "bdrip", "360p", "480p", "720p", "1080p",
    "2160p", "4k", "1440p", "540p", "240p", "140p", "hdrip", "hq-hdrip", "hq-hdtc", "hq-cam", "hq-ts", "hq-predvd",
    "dual", "multi", "audio", "dubbed", "v1", "v2", "v3", "v4", "v5", "v6", "version", "ver", "cleaned", "clean",
    "5.1", "7.1", "2.0", "5.1ch", "7.1ch", "dd5.1", "ddp5.1", "dd", "ddp",
    "hin", "hindi", "tam", "tamil", "tel", "telugu", "mal", "malayalam", "kan", "kannada",
    "ben", "bengali", "mar", "marathi", "guj", "gujarati", "pun", "punjabi", "eng", "english",
    "kor", "korean", "jpn", "japanese", "chi", "chinese", "spa", "spanish", "fre", "french",
}

IGNORE_WORDS = _BASE_IGNORE_WORDS | set(BAD_WORDS if isinstance(BAD_WORDS, (list, tuple, set)) else [])

CAPTION_LANGUAGES = {
    "hin": "Hindi", "hindi": "Hindi", "tam": "Tamil", "tamil": "Tamil",
    "kan": "Kannada", "kannada": "Kannada", "tel": "Telugu", "telugu": "Telugu",
    "mal": "Malayalam", "malayalam": "Malayalam", "eng": "English", "english": "English",
    "pun": "Punjabi", "punjabi": "Punjabi", "ben": "Bengali", "bengali": "Bengali",
    "mar": "Marathi", "marathi": "Marathi", "guj": "Gujarati", "gujarati": "Gujarati",
    "urd": "Urdu", "urdu": "Urdu", "kor": "Korean", "korean": "Korean",
    "jpn": "Japanese", "japanese": "Japanese", "bho": "Bhojpuri", "bhojpuri": "Bhojpuri",
    "ori": "Odia", "odia": "Odia", "asm": "Assamese", "assamese": "Assamese",
    "spa": "Spanish", "spanish": "Spanish", "fre": "French", "french": "French",
    "ger": "German", "german": "German", "ita": "Italian", "italian": "Italian",
    "rus": "Russian", "russian": "Russian", "chi": "Chinese", "chinese": "Chinese",
    "tha": "Thai", "thai": "Thai", "ind": "Indonesian", "indonesian": "Indonesian",
    "dual": "Dual Audio", "multi": "Multi Audio"
}

YEAR_PATTERN = re.compile(r'\b(19\d\d|20\d\d)\b')
SEASON_EPISODE_PATTERN = re.compile(r'\bS(\d{1,2})[\s._-]*E(\d{1,3})\b', re.IGNORECASE)
SEASON_ONLY_PATTERN = re.compile(r'\b(?:Season|S)[\s._-]*(\d{1,2})\b', re.IGNORECASE)
RESOLUTION_PATTERN = re.compile(r'\b(2160p|1440p|1080p|720p|480p|360p|4k)\b', re.IGNORECASE)
SERIES_SUFFIX_RE = re.compile(r'\s+Season\s*\d+\b|\s+S\d{1,2}\b', re.IGNORECASE)

MEDIA_FILTER = filters.document | filters.video | filters.audio

STATS = defaultdict(int)
sending_updates = set()
edit_locks = defaultdict(asyncio.Lock)
pending_updates = {}
_update_running = set()
_update_dirty = set()

# Async background queue for zero-lag batch handling (100-200 files)
media_queue: asyncio.Queue = asyncio.Queue()
_worker_task: Optional[asyncio.Task] = None

# In-memory fast metadata cache
_meta_cache = {}

def decode_title_escapes(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'__40_|_40_', ' (', text)
    text = re.sub(r'__41_|_41_', ') ', text)
    text = re.sub(r'__5B_|_5B_', ' [', text)
    text = re.sub(r'__5D_|_5D_', '] ', text)
    return text

def clean_mentions_links(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r'@\w+', '', text)
    text = re.sub(r'https?://\S+', '', text)
    text = re.sub(r'www\.\S+', '', text)
    text = re.sub(r't\.me/\S+', '', text)
    return re.sub(r'\s+', ' ', text).strip()

def normalize(s: str) -> str:
    s = clean_mentions_links(s or "")
    s = re.sub(r'[._\-\[\]\(\)]', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()

def canon_key(title: str, season=None) -> str:
    t = re.sub(r'[^a-z0-9]', '', (title or "").lower())
    return f"{t}_s{int(season)}" if season is not None else t

def split_trailing_year(title: str) -> Tuple[str, Optional[str]]:
    m = re.search(r'\s*\b(19\d\d|20\d\d)\b\s*$', title or "")
    if m:
        return title[:m.start()].strip(" -_"), m.group(1)
    return title.strip(), None

def parse_local_title(filename: str) -> Tuple[str, Optional[str]]:
    if not filename:
        return "Unknown", None
    name = decode_title_escapes(filename)
    name = clean_mentions_links(name)
    name = re.sub(r'\.[a-zA-Z0-9]{2,4}$', '', name)
    name = re.sub(r'\[.*?\]|\(.*?\)', ' ', name)

    year_match = YEAR_PATTERN.search(name)
    year = year_match.group(1) if year_match else None

    if year_match:
        name = name[:year_match.start()]

    name = PIRACY_STRIP_REGEX.sub(' ', name)
    name = normalize(name)
    tokens = [w for w in name.split() if w.lower() not in IGNORE_WORDS]
    cleaned = " ".join(tokens)
    return cleaned or "Unknown", year

def build_base_name(title: str, year: Optional[str], is_series: bool, season=None) -> str:
    base = title.strip()
    if is_series and season is not None:
        base = f"{base} Season {int(season)}"
    elif year and str(year) not in base:
        base = f"{base} {year}"
    return base

def get_qualities(text: str) -> str:
    if not text:
        return "N/A"
    res = RESOLUTION_PATTERN.findall(text)
    if not res:
        return "N/A"
    seen = []
    for r in res:
        norm = "2160p (4K)" if r.lower() == "4k" or r.lower() == "2160p" else r.lower()
        if norm not in seen:
            seen.append(norm)
    return ", ".join(seen) if seen else "N/A"

def format_movie_qualities(quality_list: list) -> str:
    all_res = set()
    for q in quality_list:
        if q and q != "N/A":
            for r in RESOLUTION_PATTERN.findall(q):
                all_res.add("2160p" if r.lower() == "4k" else r.lower())
    order = ["2160p", "1440p", "1080p", "720p", "480p", "360p"]
    found = [("4K" if r == "2160p" else r) for r in order if r in all_res]
    return ", ".join(found) if found else "HD"

def extract_season_episode(text: str) -> Tuple[Optional[int], Optional[str]]:
    if not text:
        return None, None
    m = SEASON_EPISODE_PATTERN.search(text)
    if m:
        return int(m.group(1)), str(int(m.group(2)))
    m2 = SEASON_ONLY_PATTERN.search(text)
    if m2:
        return int(m2.group(1)), None
    return None, None

def format_runtime(runtime_val, is_series: bool = False) -> str:
    if not runtime_val or str(runtime_val).strip().upper() in ("N/A", "NONE", "0"):
        return "N/A"
    try:
        mins = int(re.search(r'\d+', str(runtime_val)).group(0))
        if mins <= 0:
            return "N/A"
        hrs, rem = divmod(mins, 60)
        if is_series:
            return f"~{mins} min/ep"
        if hrs > 0:
            return f"{hrs}h {rem}m" if rem else f"{hrs}h"
        return f"{mins}m"
    except Exception:
        return str(runtime_val)

def _safe_bot_username() -> str:
    uname = (getattr(temp, "U_NAME", None) or BOT_USERNAME_FALLBACK)
    uname = str(uname).strip().lstrip("@").strip()
    return uname or BOT_USERNAME_FALLBACK

def _build_buttons(movie_doc, base_name):
    btn_style = enums.ButtonStyle.SUCCESS if movie_doc.get("tag", "#MOVIE") == "#SERIES" else enums.ButtonStyle.PRIMARY
    btn_q = SERIES_SUFFIX_RE.sub("", str(base_name or "")).strip(" :-–—,") or str(base_name or "").strip()
    clean_query = re.sub(r'[^a-zA-Z0-9_\-]', '', btn_q.replace(" ", "-"))[:50]
    uname = _safe_bot_username()
    url = f"https://t.me/{uname}?start=getfile-{clean_query}"
    return InlineKeyboardMarkup([[InlineKeyboardButton("ɢᴇᴛ ғɪʟᴇs", url=url, style=btn_style)]])

def _expand_episode_ids(episodes) -> list:
    out = []
    for ep in episodes:
        try:
            out.append(int(ep))
        except (ValueError, TypeError):
            pass
    return sorted(set(out))

def _series_runtime(movie_doc, base_name) -> str:
    stored = movie_doc.get("runtime")
    if stored and str(stored).strip().upper() not in ("N/A", "NONE"):
        return str(stored)
    file_runtimes = [int(f["runtime"]) for f in (movie_doc.get("files") or []) if f.get("runtime") and str(f["runtime"]).isdigit() and int(f["runtime"]) > 0]
    if file_runtimes:
        avg = round(sum(file_runtimes) / len(file_runtimes))
        return f"{avg}"
    return "N/A"

def generate_movie_message(movie_doc, base_name, compact: int = 0):
    all_raw_qualities = []
    all_languages = set()
    episodes_by_season = defaultdict(set)
    valid_file_runtimes = []

    for file in (movie_doc.get("files") or []):
        if file.get("quality") and file.get("quality") != "N/A":
            all_raw_qualities.append(file["quality"])
        if file.get("language") and file.get("language") != "N/A":
            for lang in file["language"].split(","):
                all_languages.add(CAPTION_LANGUAGES.get(lang.strip().lower(), lang.strip().title()))
        sn = file.get("season")
        if sn is not None and file.get("episode") is not None:
            try:
                episodes_by_season[int(sn)].add(str(file["episode"]))
            except (ValueError, TypeError):
                pass
        if file.get("runtime") and str(file["runtime"]).isdigit() and int(file["runtime"]) > 0:
            valid_file_runtimes.append(int(file["runtime"]))

    primary_tag = movie_doc.get("tag", "#MOVIE")
    is_series = (primary_tag == "#SERIES")

    epi_block = ""
    if episodes_by_season and compact < 1:
        episode_lines = []
        for season, episodes in sorted(episodes_by_season.items()):
            sorted_nums = _expand_episode_ids(episodes)
            if sorted_nums:
                collapsed = []
                start = end = sorted_nums[0]
                for num in sorted_nums[1:]:
                    if num == end + 1:
                        end = num
                    else:
                        collapsed.append(str(start) if start == end else f"{start}-{end}")
                        start = end = num
                collapsed.append(str(start) if start == end else f"{start}-{end}")
                episode_lines.append(f"S{int(season)}: {', '.join(collapsed)}")
        if episode_lines:
            epi_block = f"\n📺 ᴇᴘɪsᴏᴅᴇs : <b>{chr(10).join(episode_lines)}</b>"

    genres_val = movie_doc.get("genres") or "N/A"
    quality_str = format_movie_qualities(all_raw_qualities)
    lang_items = sorted(all_languages)
    language_str = ", ".join(lang_items) if lang_items else "Hindi"

    raw_rating = str(movie_doc.get("rating") or "N/A").strip()
    clean_rating = raw_rating if raw_rating.replace(".", "", 1).isdigit() else "N/A"

    raw_runtime = movie_doc.get("runtime") or "N/A"
    if is_series:
        raw_runtime = _series_runtime(movie_doc, base_name)
    elif str(raw_runtime).strip().upper() in ("N/A", "NONE", "0", "-") and valid_file_runtimes:
        raw_runtime = f"{valid_file_runtimes[0]}"

    runtime = format_runtime(raw_runtime, is_series=is_series)

    stored_title = movie_doc.get("display_title") or movie_doc.get("title", base_name)
    display_title = re.sub(r'[:,]?\s*\b(?:Episode|Ep)\b\.?\s*\d+.*|\s+Season\s*\d+\b|\s+S\d{1,2}\b', '', str(stored_title or base_name), flags=re.IGNORECASE).strip() or str(base_name)
    movie_year = movie_doc.get("year")
    filename_display = f"{display_title} {movie_year}".strip() if (movie_year and str(movie_year) not in display_title and not is_series) else display_title

    esc = lambda v: html.escape(str(v if v is not None else ""), quote=False)
    imdb_link = movie_doc.get("imdb_url") or f"https://www.imdb.com/find/?q={quote_plus(str(display_title))}"

    text = script.MOVIE_UPDATE_NOTIFY_TXT.format(
        imdb_url=html.escape(str(imdb_link), quote=True),
        filename=esc(filename_display),
        tag=esc(primary_tag),
        genres=esc(genres_val),
        runtime=runtime,
        quality=esc(quality_str),
        language=esc(language_str),
        episodes=epi_block,
        rating=clean_rating,
        search_link=getattr(temp, "B_LINK", None) or f"@{_safe_bot_username()}"
    ).strip()

    return re.sub(r"N/A\s*/\s*10", "N/A", text)

def visible_len(text: str) -> int:
    return len(html.unescape(re.sub(r"<[^>]+>", "", text or "")))

def build_post_text(movie_doc, base_name, photo: bool) -> str:
    limit = 1024 if photo else 4096
    text = generate_movie_message(movie_doc, base_name)
    if visible_len(text) > limit:
        text = generate_movie_message(movie_doc, base_name, compact=1)
    if visible_len(text) > limit:
        text = generate_movie_message(movie_doc, base_name, compact=2)
    return text

async def find_cached_doc(movies, keys: list, ids: list, year, is_series: bool):
    keys = [k for k in dict.fromkeys(keys) if k]
    ids = [i for i in dict.fromkeys(ids) if i]
    clauses = [{"clean_title": {"$in": keys}}, {"alias_keys": {"$in": keys}}, {"_id": {"$in": ids}}]
    docs = await movies.find({"$or": clauses}).to_list(length=10)
    if not docs:
        return None
    docs.sort(key=lambda d: (d["_id"] not in ids, not d.get("message_id")))
    return docs[0]

def schedule_update(bot, base_name, delay=3):
    if base_name in _update_running:
        _update_dirty.add(base_name)
        return
    old = pending_updates.pop(base_name, None)
    if old and not old.done():
        old.cancel()

    async def _run():
        await asyncio.sleep(delay)
        for _ in range(60):
            if base_name not in sending_updates:
                break
            await asyncio.sleep(1)
        while True:
            _update_running.add(base_name)
            _update_dirty.discard(base_name)
            try:
                await update_movie_message(bot, base_name)
            finally:
                _update_running.discard(base_name)
            if base_name not in _update_dirty:
                break
            await asyncio.sleep(1)

    task = asyncio.create_task(_run())
    pending_updates[base_name] = task

    def _done(t, key=base_name):
        if pending_updates.get(key) is t:
            pending_updates.pop(key, None)
        if not t.cancelled() and t.exception():
            logger.error(f"Scheduled update failed for {key}: {t.exception()}")
    task.add_done_callback(_done)

async def update_movie_message(bot, base_name):
    async with edit_locks[base_name]:
        try:
            movie_doc = await db.movie_updates.find_one({"_id": base_name})
            if not movie_doc:
                return
            message_id = movie_doc.get("message_id")
            if not message_id:
                await send_movie_update(bot, base_name)
                return

            is_photo = bool(movie_doc.get("is_photo", False))
            text = build_post_text(movie_doc, base_name, is_photo)
            buttons = _build_buttons(movie_doc, base_name)

            for attempt in range(2):
                try:
                    if is_photo:
                        await bot.edit_message_caption(
                            chat_id=MOVIE_UPDATE_CHANNEL,
                            message_id=message_id,
                            caption=text,
                            reply_markup=buttons,
                            parse_mode=enums.ParseMode.HTML
                        )
                    else:
                        await bot.edit_message_text(
                            chat_id=MOVIE_UPDATE_CHANNEL,
                            message_id=message_id,
                            text=text,
                            reply_markup=buttons,
                            parse_mode=enums.ParseMode.HTML,
                            disable_web_page_preview=True
                        )
                    return
                except MessageNotModified:
                    return
                except FloodWait as e:
                    logger.warning(f"FloodWait {e.value}s while editing {base_name}")
                    await asyncio.sleep(e.value + 1)
                except MessageIdInvalid:
                    await db.movie_updates.update_one({"_id": base_name}, {"$set": {"message_id": None}})
                    await send_movie_update(bot, base_name)
                    return
        except Exception as e:
            logger.error(f"Failed to update movie message for {base_name}: {e}")

async def send_movie_update(bot, base_name):
    if base_name in sending_updates:
        return None
    sending_updates.add(base_name)
    try:
        for _ in range(3):
            try:
                movie_doc = await db.movie_updates.find_one({"_id": base_name})
                if not movie_doc:
                    return None
                if movie_doc.get("message_id"):
                    schedule_update(bot, base_name, delay=1)
                    return None

                buttons = _build_buttons(movie_doc, base_name)
                poster_url = movie_doc.get("poster_url")
                photo_url = poster_url if (poster_url and str(poster_url).startswith("http")) else None
                is_photo, msg = False, None

                if photo_url:
                    try:
                        msg = await bot.send_photo(
                            chat_id=MOVIE_UPDATE_CHANNEL,
                            photo=photo_url,
                            caption=build_post_text(movie_doc, base_name, True),
                            reply_markup=buttons,
                            parse_mode=enums.ParseMode.HTML
                        )
                        is_photo = True
                    except FloodWait:
                        raise
                    except Exception as e:
                        logger.warning(f"Photo send failed for {base_name} ({e}); sending text post instead")

                if msg is None:
                    msg = await bot.send_message(
                        chat_id=MOVIE_UPDATE_CHANNEL,
                        text=build_post_text(movie_doc, base_name, False),
                        reply_markup=buttons,
                        parse_mode=enums.ParseMode.HTML,
                        disable_web_page_preview=True
                    )

                await db.movie_updates.update_one({"_id": base_name}, {"$set": {"message_id": msg.id, "is_photo": is_photo}})
                logger.info(f"[POST] Published '{base_name}' to update channel (message id {msg.id})")
                return msg
            except FloodWait as e:
                logger.warning(f"FloodWait {e.value}s while posting {base_name}")
                await asyncio.sleep(e.value + 2)
            except Exception as e:
                logger.error(f"send_movie_update failed for {base_name}: {e}")
                break
        return None
    finally:
        sending_updates.discard(base_name)

async def _append_file(bot, movies, doc, file_data, alias_key, filename):
    base_name = doc["_id"]
    pending_post = not doc.get("message_id")
    if filename != "Unknown" and any(f.get("filename") == filename for f in (doc.get("files") or [])):
        if pending_post:
            await send_movie_update(bot, base_name)
        return

    update = {"$push": {"files": file_data}}
    if alias_key and alias_key != doc.get("clean_title") and alias_key not in (doc.get("alias_keys") or []):
        update["$addToSet"] = {"alias_keys": alias_key}

    await movies.update_one({"_id": base_name}, update)
    STATS["files_merged"] += 1
    logger.info(f"[MERGED] '{filename}' -> existing post '{base_name}'")

    if pending_post:
        await send_movie_update(bot, base_name)
    else:
        schedule_update(bot, base_name)

async def process_single_media(bot, filename: str, caption: str, file_runtime_mins: Optional[int] = None):
    if not hasattr(db, "movie_updates"):
        db.movie_updates = db.db.movie_updates
    movies = db.movie_updates

    filename = filename or "Unknown"
    caption = caption or ""
    filename_dec = decode_title_escapes(filename)
    filename_clean = clean_mentions_links(filename)

    season, episode = extract_season_episode(filename_dec)
    if season is None and caption:
        season, episode = extract_season_episode(caption)

    title_local, year_local = parse_local_title(filename)
    if not title_local:
        title_local = normalize(filename_clean) or "Unknown"

    is_series = (season is not None)
    local_name = build_base_name(title_local, year_local, is_series, season)
    local_key = canon_key(title_local, season)

    quality = next((q for q in (get_qualities(caption), get_qualities(filename_dec)) if q and q != "N/A"), "N/A")
    scan_text = f"{caption} {filename_clean}".lower()
    lang_keys = {k for k in CAPTION_LANGUAGES if re.search(rf"\b{re.escape(k)}\b", scan_text)}
    language = ", ".join(sorted({CAPTION_LANGUAGES[k] for k in lang_keys})) if lang_keys else "N/A"
    runtime_str = f"{file_runtime_mins}" if file_runtime_mins else "N/A"

    file_data = {
        "filename": filename,
        "processed": normalize(filename_clean),
        "quality": quality,
        "language": language,
        "timestamp": datetime.now(),
        "tag": "#SERIES" if is_series else "#MOVIE",
        "season": season,
        "episode": episode,
        "runtime": runtime_str,
    }

    # 1) Check existing post in database
    cached = await find_cached_doc(movies, [local_key], [local_name], year_local, is_series)
    if cached:
        await _append_file(bot, movies, cached, file_data, local_key, filename)
        return

    # 2) Fetch official details from TMDB with memory-safe cache
    meta_title = title_local
    meta_year = year_local
    poster_url = None
    genres = "N/A"
    rating = "N/A"
    imdb_url = ""

    search_query = f"{title_local} {year_local}".strip() if year_local else title_local
    if search_query in _meta_cache:
        tmdb_data = _meta_cache[search_query]
    else:
        try:
            tmdb_data = await asyncio.wait_for(get_movie_detailsx(search_query), timeout=5.0)
            if len(_meta_cache) > 2000:
                _meta_cache.clear()
            _meta_cache[search_query] = tmdb_data
        except Exception:
            tmdb_data = None

    if tmdb_data and isinstance(tmdb_data, dict):
        if tmdb_data.get("title"):
            meta_title = tmdb_data["title"]
        if tmdb_data.get("year"):
            meta_year = str(tmdb_data["year"])
        poster_url = tmdb_data.get("poster_url")
        if tmdb_data.get("genres"):
            raw_g = tmdb_data["genres"]
            genres = ", ".join(raw_g) if isinstance(raw_g, list) else str(raw_g)
        if tmdb_data.get("rating"):
            rating = str(tmdb_data["rating"])
        if tmdb_data.get("imdb_id"):
            imdb_url = f"https://www.imdb.com/title/{tmdb_data['imdb_id']}/"

    base_name = build_base_name(meta_title, meta_year, is_series, season if is_series else None)
    final_key = canon_key(meta_title, season if is_series else None)

    if final_key != local_key:
        cached = await find_cached_doc(movies, [final_key], [base_name], meta_year, is_series)
        if cached:
            await _append_file(bot, movies, cached, file_data, local_key, filename)
            return

    new_doc = {
        "_id": base_name,
        "clean_title": final_key,
        "alias_keys": [local_key] if local_key != final_key else [],
        "title": meta_title,
        "display_title": meta_title,
        "files": [file_data],
        "poster_url": poster_url,
        "genres": genres,
        "rating": rating,
        "runtime": runtime_str,
        "imdb_url": imdb_url,
        "year": meta_year,
        "tag": "#SERIES" if is_series else "#MOVIE",
        "message_id": None,
    }

    try:
        await movies.insert_one(new_doc)
    except DuplicateKeyError:
        await movies.update_one({"_id": base_name}, {"$push": {"files": file_data}, "$addToSet": {"alias_keys": local_key}})
        await send_movie_update(bot, base_name)
        return

    STATS["posts_created"] += 1
    await send_movie_update(bot, base_name)

async def channel_post_worker(bot):
    logger.info("[CHANNEL] Background post worker started.")
    while True:
        try:
            item = await media_queue.get()
            filename, caption, file_runtime_mins = item
            try:
                await process_single_media(bot, filename, caption, file_runtime_mins)
            except Exception as e:
                logger.error(f"[CHANNEL] Error in process_single_media: {e}", exc_info=True)
            finally:
                media_queue.task_done()
            # Pacing interval to prevent Telegram FloodWait during large 100-200 file batches
            await asyncio.sleep(1.5)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"[CHANNEL] Worker loop error: {e}")
            await asyncio.sleep(2)

@Client.on_message(filters.chat(CHANNELS) & MEDIA_FILTER)
async def media_handler(bot, message):
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(channel_post_worker(bot))

    media = next((getattr(message, ft) for ft in ("document", "video", "audio") if getattr(message, ft, None)), None)
    if not media:
        return

    duration_secs = getattr(media, "duration", None) or (message.video.duration if message.video else (message.audio.duration if message.audio else None))
    file_runtime_mins = round(duration_secs / 60) if duration_secs else None

    media.file_type = next(ft for ft in ("document", "video", "audio") if getattr(message, ft, None))
    media.caption = message.caption or ""

    try:
        await save_file(media)
    except Exception:
        logger.exception("save_file failed in media_handler")
        return

    try:
        if await db.movie_update_status(bot.me.id):
            filename = media.file_name or message.caption or "Unknown"
            await media_queue.put((filename, media.caption, file_runtime_mins))
            logger.info(f"[MEDIA] Queued '{filename}' (queue depth: {media_queue.qsize()})")
    except Exception as e:
        logger.exception(f"Error queuing media update: {e}")

# Admin tools
@Client.on_message(filters.command(["chstats", "channelstats"]) & filters.user(ADMINS))
async def chstats_handler(bot, message):
    q_len = media_queue.qsize()
    total_posts = await db.movie_updates.count_documents({}) if hasattr(db, "movie_updates") else 0
    text = (
        f"📊 <b>Channel Update Stats</b>\n\n"
        f"• Pending in Queue: <code>{q_len}</code>\n"
        f"• Total Posts: <code>{total_posts}</code>\n"
        f"• Posts Created: <code>{STATS['posts_created']}</code>\n"
        f"• Files Merged: <code>{STATS['files_merged']}</code>\n"
    )
    await message.reply_text(text, parse_mode=enums.ParseMode.HTML)
