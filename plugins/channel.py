import os
import re
import json
import logging
import asyncio
import aiohttp
import inspect
import html
import time
import random
from difflib import SequenceMatcher
from urllib.parse import quote_plus, unquote
from datetime import datetime
from bs4 import BeautifulSoup
from collections import Counter, defaultdict
from plugins.Dreamxfutures.Imdbposter import get_movie_detailsx, get_movie_details
from database.users_chats_db import db
from pyrogram import Client, filters, enums
from info import CHANNELS, MOVIE_UPDATE_CHANNEL, BAD_WORDS, TMDB_POSTER, ADMINS, TMDB_API_KEY
from Script import script
from database.ia_filterdb import save_file
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from utils import temp
from pymongo.errors import DuplicateKeyError
from pyrogram.errors import MessageIdInvalid, MessageNotModified, FloodWait
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_HDHUB_DOMAIN = "https://new1.hdhub4u.free"

try:
    from info import GEMINI_API_KEY
except ImportError:
    GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# --- Gemini configuration -------------------------------------------------
# NOTE: gemini-1.5 / 2.0 are shut down; gemini-2.5-* is scheduled to shut down 16 Oct 2026.
# Override with env GEMINI_MODELS="modelA,modelB". If every model 404s the bot auto-discovers
# usable ones through the ListModels endpoint.
GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"
GEMINI_MODELS = [m.strip() for m in os.environ.get(
    "GEMINI_MODELS",
    "gemini-3.1-flash-lite,gemini-3.5-flash-lite,gemini-3-flash-preview,gemini-flash-latest,gemini-2.5-flash"
).split(",") if m.strip()]
GEMINI_MIN_INTERVAL = float(os.environ.get("GEMINI_MIN_INTERVAL", "2.0"))   # seconds between calls
GEMINI_MAX_RETRIES = 3
GEMINI_TIMEOUT = 25
try:
    from info import OMDB_API_KEY
except ImportError:
    OMDB_API_KEY = os.environ.get("OMDB_API_KEY", "")

OMDB_URL = "https://www.omdbapi.com/"
BOT_USERNAME_FALLBACK = "BoultFlixMovieBot"
GEMINI_COOLDOWN = 45          # seconds to skip Gemini after all models fail (429/503 storms)

gemini_semaphore = asyncio.Semaphore(1)

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
    "combined", "esub", "esubs", "msub", "msubs", "esu", "msu", "proper", "repack", "unrated", "extended", "imax",
    "remux", "10bit", "10-bit", "x264", "x265", "h264", "h265", "hevc", "avc", "dovi", "hdr", "hdr10", "hdr10+",
    "web", "dl", "ott", "reloaded", "uncut", "ds4k", "line",
    "hdcam", "hdtc", "camrip", "cam", "ts", "tc", "hdts", "telesync", "dvdscr", "dvdrip", "predvd", "webrip",
    "web-dl", "tvrip", "hdtv", "web dl", "webdl", "bluray", "brrip", "bdrip", "360p", "480p", "720p", "1080p",
    "2160p", "4k", "1440p", "540p", "240p", "140p", "hdrip", "hq-hdrip", "hq-hdtc", "hq-cam", "hq-ts", "hq-predvd",
    "dual", "multi", "audio", "dubbed", "v1", "v2", "v3", "v4", "v5", "v6", "version", "ver", "cleaned", "clean",
    "nf", "netflix", "sonyliv", "sliv", "amzn", "primevideo", "hotstar", "jiohotstar", "zee5", "jhs", "atvp", "dnp",
    "hoichoi", "sunnxt", "mxplayer", "erosnow", "mxtv",
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

OTT_PLATFORMS = {
    "nf": "Netflix", "netflix": "Netflix", "sonyliv": "SonyLiv", "sony": "SonyLiv",
    "sliv": "SonyLiv", "amzn": "Amazon Prime Video",
    "primevideo": "Amazon Prime Video", "amazon": "Amazon Prime Video",
    "hotstar": "Disney+ Hotstar", "disney": "Disney+", "dnp": "Disney+",
    "zee5": "Zee5", "dsnp": "Disney+ Hotstar", "jio": "JioHotstar", "jiohotstar": "JioHotstar", "mxplayer": "MX Player", "jhs": "JioHotstar", "jiocinema": "JioCinema",
    "aha": "Aha", "hbo": "HBO Max", "paramount": "Paramount+", "atv": "Apple TV+", "atvp": "Apple TV+", "appletv": "Apple TV+",
    "hoichoi": "Hoichoi", "sunnxt": "Sun NXT", "viki": "Viki",
    "crunchyroll": "Crunchyroll", "hulu": "Hulu", "peacock": "Peacock",
    "lionsgate": "Lionsgate Play", "lionsgateplay": "Lionsgate Play",
    "altbalaji": "ALTT", "altt": "ALTT", "shemaroo": "ShemarooMe",
    "shemaroome": "ShemarooMe", "chaupal": "Chaupal",
    "planetmarathi": "Planet Marathi", "manorama": "ManoramaMAX", "manoramamax": "ManoramaMAX",
    "tubi": "Tubi", "eros": "Eros Now", "erosnow": "Eros Now"
}

STANDARD_FORMATS = {
    "hdtc": "HDTC", "hd-tc": "HDTC", "hq-hdtc": "HQ-HDTC", "hdcam": "HDCam",
    "hd-cam": "HDCam", "hq-hdcam": "HQ-HDCam", "cam": "CAM", "camrip": "CamRip",
    "hq-cam": "HQ-CAM", "ts": "TS", "hdts": "HDTS", "hq-ts": "HQ-TS", "tc": "TC",
    "telesync": "TeleSync", "predvd": "PreDVD", "hq-predvd": "HQ-PreDVD",
    "dvdrip": "DVDRip", "dvdscr": "DVDScr", "webrip": "WEBRip", "web-dl": "WEB-DL",
    "webdl": "WEB-DL", "web dl": "WEB-DL", "bluray": "BluRay", "brrip": "BRRip",
    "bdrip": "BDRip", "remux": "Remux", "imax": "IMAX", "hdrip": "HDRip",
    "hq-hdrip": "HQ-HDRip", "hdtv": "HDTV", "tvrip": "TVRip", "hevc": "HEVC",
    "10bit": "10-Bit", "10-bit": "10-Bit", "hdr": "HDR", "hdr10": "HDR10",
    "hdr10+": "HDR10+", "dv": "Dolby Vision", "dovi": "Dolby Vision"
}

RES_ORDER = {
    "140p": 140, "240p": 240, "360p": 360, "480p": 480, "540p": 540,
    "720p": 720, "1080p": 1080, "1440p": 1440, "2160p": 2160, "4k": 2160
}

CLEAN_PATTERN = re.compile(
    r'@[^ \n\r\t\.,:;!?()\[\]{}<>\\/"\'=_%]+|'
    r'https?://\S+|www\.[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,4}|'
    r'\b[A-Za-z0-9\-]{2,}\.(?:com|net|org|mx|cz|link|club|cc|ws|xyz|vip|info|ink|ist|wtf|pm|bz|biz|rest|lat|cfd|sbs|bond|cyou|icu|click)\b|'
    r'[\U00010000-\U0010ffff]|'
    r'[\u2000-\u3300]|'
    r'[\(\[\{][@#\^\*]+[\)\]\}]'
)

NORMALIZE_PATTERN = re.compile(r"[._\-+]+|[()\[\]{}:;'\"–—!,.?~`*^|\\/]")
RESOLUTION_PATTERN = re.compile(r"\b(?:2160p|4K|1440p|1080p|720p|540p|480p|360p|240p|140p)\b", re.IGNORECASE)
SOURCE_PATTERN = re.compile(
    r"\b(?:HDCam|HD-Cam|HQ-HDCam|HDTC|HD-TC|HQ-HDTC|CamRip|CAM|HQ-CAM|TS|HDTS|HQ-TS|TC|TeleSync|DVDScr|DVDRip|PreDVD|HQ-PreDVD|"
    r"WEBRip|WEB-DL|TVRip|HDTV|WEB DL|WebDl|BluRay|BRRip|BDRip|Remux|IMAX|"
    r"HEVC|10Bit|10-Bit|HDRip|HQ-HDRip|HDR10\+|HDR10|HDR|DV|DoVi)\b",
    re.IGNORECASE
)
VERSION_STANDALONE = re.compile(r"\b(?:[vV]\d+|ver\.?\s*\d+|version\s*\d+)\b", re.IGNORECASE)
YEAR_PATTERN = re.compile(r"(?<![A-Za-z0-9])(?:19|20)\d{2}(?![A-Za-z0-9])")
AUDIO_CHANNELS_PATTERN = re.compile(
    r'\b(?:DD|DDP|AC3|EAC3|AAC|TRUEHD|ATMOS|DOLBY)?[\s._-]*[257][\s._-][01](?:[\s._-]*CH)?\b',
    re.IGNORECASE
)

BONUS_RANGE_REGEX = re.compile(r'\bS(\d{1,2})[\s._-]*(?:Bonus|Special)[\s._-]*E(?:p(?:isode)?)?0*(\d{1,3})\s*(?:to|-)\s*(?:E(?:p(?:isode)?)?)?0*(\d{1,3})\b', re.IGNORECASE)
BONUS_REGEX = re.compile(r'\bS(\d{1,2})[\s._-]*(?:Bonus|Special)[\s._-]*E(?:p(?:isode)?)?0*(\d{1,3})\b', re.IGNORECASE)
RANGE_REGEX = re.compile(r'\bS(\d{1,2})[\s._-]*(?:Part)?[\s._-]*E(?:p(?:isode)?)?0*(\d{1,3})\s*(?:to|-)\s*(?:E(?:p(?:isode)?)?)?0*(\d{1,3})(?![\dpPkK])', re.IGNORECASE)   # range end must not be '108' of '1080p' / '72' of '720p'
SINGLE_REGEX = re.compile(r'\bS(\d{1,2})[\s._-]*(?:Part)?[\s._-]*E(?:p(?:isode)?)?0*(\d{1,3})\b', re.IGNORECASE)
NAMED_REGEX = re.compile(r'Season\s*0*(\d{1,2})[\s\-,:]*(?:Part)?[\s\-,:]*Ep(?:isode)?\s*0*(\d{1,3})\b', re.IGNORECASE)
X_REGEX = re.compile(r'(?<!\d)\b0*([1-9]\d?)\s*[xX]\s*0*([1-9]\d?)\b(?!\d)', re.IGNORECASE)
DAY_REGEX = re.compile(r'\b(?:S(?:eason)?\s*0*(\d{1,2})[\s._-]*)?(?:Day\s*0*(\d{1,3})|D0*([1-9]\d{0,2}))\b', re.IGNORECASE)
NO_S_REGEX = re.compile(r'\b(?:Season|S)\s*0*(\d{1,2})[\s._-]+E(?:p(?:isode)?)?0*(\d{1,3})\b', re.IGNORECASE)
EP_ONLY_RANGE = re.compile(r'\b(?:EP|Episode)0*(\d{1,3})\s*-\s*(?:EP|Episode)?\s*0*(\d{1,3})\b', re.IGNORECASE)
EP_ONLY_SINGLE = re.compile(r'\b(?:EP|Episode)\.?\s*0*(\d{1,3})\b', re.IGNORECASE)

MEDIA_FILTER = filters.document | filters.video | filters.audio

pending_updates = {}
sending_updates = set()
edit_locks = defaultdict(asyncio.Lock)

# =====================================================================
#  TEXT CLEANING / LOCAL TITLE PARSING
# =====================================================================
EXT_RE = re.compile(r"\.(?:mkv|mp4|avi|mov|webm|m4v|flv|wmv|mp3|m4a|aac|flac|srt|zip|rar)$", re.IGNORECASE)

# Anything from here on in a normalized filename is release junk, never title.
_CUT_RE = re.compile(
    r"\b(?:2160p|1440p|1080p|720p|540p|480p|360p|240p|4k|uhd|web ?dl|webrip|blu ?ray|brrip|bdrip|"
    r"hdrip|hdtv|hdcam|hd ?cam|hdtc|hd ?tc|hdts|camrip|dvdrip|dvdscr|predvd|tele ?sync|remux|"
    r"x ?26[45]|h ?26[45]|hevc|10 ?bit|ddp? ?\d|aac|ac3|eac3|atmos|hdr10|dovi|"
    r"dual audio|multi audio|org audio|line audio|combined|"
    r"e ?su(?:bs?)?|m ?su(?:bs?)?|esb|msb|"
    r"s\d{1,2} ?e\d{1,3}|s\d{1,2}|season ?\d+|ep(?:isode)? ?\d+|e\d{2,3}|\d{1,2}x\d{1,3})\b",
    re.IGNORECASE,
)
SUB_TAG_REGEX = re.compile(
    r"\b(?:e ?su(?:bs?)?|m ?su(?:bs?)?|esb|msb|hc ?subs?|eng ?subs?|multi ?subs?|subbed|subs?)\b",
    re.IGNORECASE,
)
_HEAD_JUNK = {"combined", "mkv", "mp4", "avi", "mov", "webm", "hq", "fhd", "ott", "dubbed", "dub",
              "hdrip", "proper", "repack", "uncut", "unrated", "extended"}
_IGNORE_LOWER = {w.lower() for w in IGNORE_WORDS}

# Genres that mean the scraper hit a promo / talk show / interview page instead of the film.
BLOCKED_GENRES = {"talk-show", "talk show", "news", "reality-tv", "reality tv", "reality",
                  "game-show", "game show"}
SOFT_BLOCKED_GENRES = {"music"}   # dropped only when other genres remain


_ASCII_PAIRS = ((40, 41), (91, 93), (123, 125))                       # ( ) [ ] { }
_ASCII_SINGLE = {38: "&", 39: "'", 45: "-", 43: "+", 58: ":", 33: "!", 44: ","}
ARTIFACT_NUMBERS = {40, 41, 91, 93, 123, 125, 240, 264, 265, 360, 480, 540, 576, 720, 1080, 1440, 2160}


def decode_title_escapes(text) -> str:
    """'OK_Jaanu___40_2017__41_' -> 'OK_Jaanu ( 2017 ) ';  'Movie%282017%29' -> 'Movie(2017)';  '&amp;' -> '&'.
    Uploader tools escape punctuation as _<ASCII code>_ ; without this the code (40/41) is read as a sequel number."""
    if not text:
        return ""
    t = html.unescape(str(text))
    if "%" in t:
        t = unquote(t)
    for open_c, close_c in _ASCII_PAIRS:                               # only when BOTH halves are present (avoids 'Ali_Baba_40_Thieves')
        o, c = rf"_{{1,3}}{open_c}_{{1,3}}", rf"_{{1,3}}{close_c}_{{1,3}}"
        if re.search(o, t) and re.search(c, t):
            t = re.sub(o, f" {chr(open_c)} ", t)
            t = re.sub(c, f" {chr(close_c)} ", t)
    for code, ch in _ASCII_SINGLE.items():                              # "Don_39_t" -> "Don't"
        t = re.sub(rf"(?<=[A-Za-z0-9])_{{1,3}}{code}_{{1,3}}(?=[A-Za-z0-9])", ch, t)
    return t


def clean_mentions_links(text: str) -> str:
    return CLEAN_PATTERN.sub(" ", decode_title_escapes(text)).strip()


def normalize(s: str) -> str:
    s = NORMALIZE_PATTERN.sub(" ", s or "")
    return re.sub(r"\s+", " ", s).strip()


def squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def remove_ignored_words(text: str) -> str:
    t = PIRACY_STRIP_REGEX.sub(" ", text or "")
    words = [w for w in t.split() if w.lower() not in _IGNORE_LOWER]
    return " ".join(words) if words else normalize(text)


def canon_key(title: str, season=None) -> str:
    """Canonical DB key. Used for BOTH local filenames and Gemini titles so they always agree."""
    t = normalize((title or "").replace("&", " and "))
    t = SUB_TAG_REGEX.sub(" ", t)
    t = remove_ignored_words(t)
    t = normalize(t).lower()
    if season is not None:
        t = f"{t} season {int(season)}".strip()
    return t


def _parse_ott_list(val) -> list:
    """Gemini may answer ["Netflix","Zee5"], "Netflix, Zee5", "Netflix | Zee5" or [{"name": "Netflix"}]."""
    out = []

    def walk(v):
        if v is None:
            return
        if isinstance(v, str):
            if v.strip(" []'\"").lower() in ("", "none", "n/a", "null", "unknown", "na", "-"):
                return
            for part in re.split(r"\s*[|,;]\s*|\s+and\s+|\s*&\s*", v):
                part = part.strip(" []'\"")
                if part and part.lower() not in ("none", "n/a", "null", "unknown", "na"):
                    out.append(part)
        elif isinstance(v, (list, tuple, set)):
            for x in v:
                walk(x)
        elif isinstance(v, dict):
            walk(v.get("name") or v.get("platform") or v.get("provider"))
    walk(val)
    return list(dict.fromkeys(out))


SERIES_SUFFIX_RE = re.compile(
    r"[\s:,\-–—]*\b(?:season|series|saison|s)\s*(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\b.*$",
    re.IGNORECASE)


def split_trailing_year(title: str) -> Tuple[str, Optional[str]]:
    tokens = normalize(title).split()
    if len(tokens) > 1 and YEAR_PATTERN.fullmatch(tokens[-1]):
        return " ".join(tokens[:-1]), tokens[-1]
    return " ".join(tokens), None


def _unwrap_brackets(text: str) -> str:
    def repl(m):
        inner = m.group(1).strip()
        # "[CineHDs]" style single-word uploader tags -> drop, anything else -> keep the content
        if not inner or re.fullmatch(r"[A-Za-z]+", inner):
            return " "
        return f" {inner} "
    return re.sub(r"[\[\(\{]\s*([^\]\)\}]*?)\s*[\]\)\}]", repl, text)


def _clean_title_head(title: str) -> str:
    t = _strip_season_episode_tokens(title)
    t = SUB_TAG_REGEX.sub(" ", t)
    return " ".join(w for w in t.split() if w.lower() not in _HEAD_JUNK)


def raw_title_head(filename: str) -> str:
    """The title part of a filename BEFORE any word-stripping ('Mar.Jaayen.2026.1080p' -> 'Mar Jaayen'), used to keep title
    words out of OTT / language detection."""
    name = _unwrap_brackets(AUDIO_CHANNELS_PATTERN.sub(" ", EXT_RE.sub("", clean_mentions_links(filename or "").strip())))
    text = normalize(name)
    m = _CUT_RE.search(text)
    tokens = (text[:m.start()] if m else text).split()
    for i in range(len(tokens) - 1, 0, -1):
        if YEAR_PATTERN.fullmatch(tokens[i]):
            tokens = tokens[:i]
            break
    return " ".join(tokens)


def parse_local_title(filename: str) -> Tuple[str, Optional[str]]:
    """Local, deterministic title/year extraction.
    'Sardar.2.2026.480p.WEB-DL.Hindi.ESu.mkv' -> ('Sardar 2', '2026')"""
    name = clean_mentions_links(filename or "")
    name = EXT_RE.sub("", name.strip())
    name = AUDIO_CHANNELS_PATTERN.sub(" ", name)
    name = _unwrap_brackets(name)
    text = normalize(name)

    m = _CUT_RE.search(text)
    head, tail = (text[:m.start()], text[m.start():]) if m else (text, "")
    head_tokens = head.split()

    year, title_tokens = None, head_tokens
    for i in range(len(head_tokens) - 1, 0, -1):          # last year token (never index 0: "2012")
        if YEAR_PATTERN.fullmatch(head_tokens[i]):
            year, title_tokens = head_tokens[i], head_tokens[:i]
            break
    if year is None:
        for tok in tail.split():
            if YEAR_PATTERN.fullmatch(tok):
                year = tok
                break

    if year and len(title_tokens) > 1 and title_tokens[-1] in ("40", "41", "91", "93"):
        title_tokens = title_tokens[:-1]                      # leftover of an escaped bracket, not a sequel
    title = _clean_title_head(" ".join(title_tokens))
    if not title:   # marker at the very start -> fall back to the older whole-string cleaning
        title = normalize(remove_ignored_words(_strip_season_episode_tokens(text)))
        title, y2 = split_trailing_year(title)
        year = year or y2
    return title, year


def build_base_name(title: str, year: Optional[str], is_series: bool, season) -> str:
    base = normalize(title)
    if season is not None:
        if not re.search(r"\bSeason\s*\d+\b", base, re.IGNORECASE):
            base = f"{base} Season {int(season)}"
    elif not is_series and year and str(year) not in base:
        base = f"{base} {year}"
    return base


# =====================================================================
#  TITLE MATCHING / GENRE HELPERS
# =====================================================================
def _title_tokens(s: str) -> list:
    s = normalize(YEAR_PATTERN.sub(" ", s or "")).lower()
    return [w for w in s.split() if len(w) >= 2 or w.isdigit()]


def is_good_title_match(query: str, found_title: str) -> bool:
    q, f = _title_tokens(query), _title_tokens(found_title)
    if not q or not f:
        return False
    if q == f:
        return True
    if not all(w in f for w in q):
        return False
    # sequel digits must agree: "Sardar" must NOT match "Sardar 2"
    if {w for w in q if w.isdigit()} != {w for w in f if w.isdigit()}:
        return False
    return len(f) - len(q) <= 3


_GENRE_CANON = {
    "action": "Action", "adventure": "Adventure", "animation": "Animation", "anime": "Anime", "biography": "Biography",
    "biographical": "Biography", "biopic": "Biopic", "comedy": "Comedy", "crime": "Crime", "documentary": "Documentary",
    "drama": "Drama", "family": "Family", "fantasy": "Fantasy", "film-noir": "Film-Noir", "film noir": "Film-Noir",
    "noir": "Noir", "history": "History", "historical": "Historical", "horror": "Horror", "music": "Music",
    "musical": "Musical", "mystery": "Mystery", "romance": "Romance", "romantic": "Romance", "sci-fi": "Sci-Fi",
    "sci fi": "Sci-Fi", "scifi": "Sci-Fi", "science fiction": "Sci-Fi", "short": "Short", "sport": "Sport",
    "sports": "Sport", "superhero": "Superhero", "thriller": "Thriller", "war": "War", "western": "Western",
    "suspense": "Suspense", "devotional": "Devotional", "mythological": "Mythological", "political": "Political",
    "period": "Period", "kids": "Kids", "teen": "Teen", "psychological": "Psychological", "spy": "Spy",
    "disaster": "Disaster", "martial arts": "Martial Arts", "medical": "Medical", "legal": "Legal",
    "coming-of-age": "Coming-of-Age", "supernatural": "Supernatural", "paranormal": "Paranormal", "survival": "Survival",
    "heist": "Heist", "courtroom": "Courtroom", "social": "Social", "rom-com": "Rom-Com", "dark comedy": "Dark Comedy",
    "black comedy": "Dark Comedy", "satire": "Satire", "tv movie": "TV Movie", "action & adventure": "Action & Adventure",
    "sci-fi & fantasy": "Sci-Fi & Fantasy", "war & politics": "War & Politics", "crime thriller": "Crime Thriller",
    "romantic comedy": "Romantic Comedy", "political thriller": "Political Thriller", "mythology": "Mythological",
}
_GENRE_BANNED_RE = re.compile(
    r"download|full\s*movie|watch|\b\d{3,4}\s*p\b|\b\d+(?:\.\d+)?\s*[gm]b\b|\bgb\b|\bmb\b|free|hdhub|https?:|www\.|links?\b|"
    r"stars?\b|starring|director|language|quality|size\b|screen|story|description|synopsis|storyline", re.IGNORECASE)
_GENRE_STOP = (r"(?:Stars?|Starring|Cast|Directors?|Directed\s*by|Writers?|Languages?|Qualit(?:y|ies)|Size|Format|Run\s*time|Duration|"
               r"Release(?:d)?(?:\s*Date)?|Screen[\s\-]*Shots?|Story\s*line|Story|Description|Plot|Synopsis|Download|Watch|IMDb|Rating|"
               r"Subtitles?|Audio|Country|Genres?|Server|Links?|Review)")


def _canon_genre(token: str) -> Optional[str]:
    t = re.sub(r"\s+", " ", str(token or "").strip(" '\".:;-")).lower()
    if not t or len(t) > 30 or _GENRE_BANNED_RE.search(t):
        return None
    return _GENRE_CANON.get(t) or _GENRE_CANON.get(t.replace("-", " "))


def parse_genres(raw) -> list:
    if not raw:
        return []
    if isinstance(raw, str):
        if raw.strip().upper() in ("N/A", "NONE", "-", ""):
            return []
        raw = re.sub(r"^\s*Genres?\s*[:\-–]\s*", "", raw, flags=re.IGNORECASE)
        # a leaked post body: keep only what stands BEFORE the first field label ("Crime, Thriller Stars: ..." -> "Crime, Thriller")
        raw = re.split(rf"\s*\b{_GENRE_STOP}\b\s*[:\-–]", raw, maxsplit=1, flags=re.IGNORECASE)[0]
        parts = re.split(r"[,|•]", re.sub(r"[\[\]'\"]", "", raw))
    elif isinstance(raw, (list, tuple, set)):
        parts = [(x.get("name") if isinstance(x, dict) else x) for x in raw]
    else:
        return []
    out = []
    for p in parts:
        p = str(p or "").strip(" '\"")
        if p and p.upper() not in ("N/A", "NONE") and p not in out:
            out.append(p)
    return out


def clean_genres(raw) -> list:
    """Only REAL film genres survive (HDHub pages can leak whole post bodies into this field).
    Talk-Show / News / Reality are dropped, 'Music' only when other genres remain, max 5."""
    items = []
    for g in parse_genres(raw):
        if g.lower() in BLOCKED_GENRES:
            continue
        c = _canon_genre(g)
        if c and c not in items:
            items.append(c)
    if len(items) > 1:
        items = [g for g in items if g.lower() not in SOFT_BLOCKED_GENRES]
    return items[:5]


def extract_genre_text(text: str) -> str:
    """Slice the value after 'Genre:' and stop at the next field label, a line break or an HTML break/paragraph tag."""
    for m in re.finditer(rf"\bGenres?\s*[:\-–]\s*(.{{1,200}}?)(?=\s*\b{_GENRE_STOP}\b\s*[:\-–]|\n|<br|<p|$)", text or "", re.IGNORECASE | re.DOTALL):
        parts = re.split(r"\s*(?:,|\||/|•|&|;|\band\b)\s*", m.group(1))
        found = []
        for p in parts:
            c = _canon_genre(p)
            if c and c not in found:
                found.append(c)
        if found:
            return ", ".join(found[:5])
    return "N/A"


def _result_title(res: dict) -> str:
    return str(res.get("title") or res.get("name") or "")


def _result_year(res: dict) -> Optional[int]:
    for k in ("year", "release_year", "release_date", "first_air_date"):
        m = re.search(r"(?:19|20)\d{2}", str(res.get(k) or ""))
        if m:
            return int(m.group(0))
    return None


def _years_compatible(a, b) -> bool:
    try:
        return abs(int(a) - int(b)) <= 2          # digital-release gaps / festival-vs-theatrical years
    except (TypeError, ValueError):
        return True


def accept_result(res: dict, title: str, year: Optional[str], is_series: bool) -> bool:
    """Reject scraper hits that are a different title/year or a talk-show/promo page."""
    if not res or not isinstance(res, dict) or res.get("error"):
        return False
    found = _result_title(res)
    if not found:
        return False
    if not is_good_title_match(title, found):
        a, b = canon_key(title), canon_key(found)
        same_digits = {w for w in a.split() if w.isdigit()} == {w for w in b.split() if w.isdigit()}
        if not (same_digits and SequenceMatcher(None, a, b).ratio() >= 0.82):
            return False
    ry = _result_year(res)
    if year and ry and not is_series and not _years_compatible(year, ry):
        return False
    raw_g = parse_genres(res.get("genres"))
    if raw_g and all(g.lower() in BLOCKED_GENRES for g in raw_g):      # every genre is Talk-Show/News/Reality...
        return False
    return True


# =====================================================================
#  GEMINI CLIENT  (active models, JSON mode, throttled, retried)
# =====================================================================
_gemini_dead_models: set = set()
_gemini_models_live: list = list(GEMINI_MODELS)
_gemini_discovered = False
_gemini_cooldown_until = 0.0
_gemini_last_call = 0.0
_gemini_cache: dict = {}


async def _gemini_throttle():
    """Called inside the semaphore: guarantees spacing between consecutive requests."""
    global _gemini_last_call
    wait = GEMINI_MIN_INTERVAL - (time.monotonic() - _gemini_last_call)
    if wait > 0:
        await asyncio.sleep(wait)
    _gemini_last_call = time.monotonic()


def _model_rank(name: str):
    n = name.lower()
    v = re.search(r"gemini-(\d+(?:\.\d+)?)", n)
    return (any(t in n for t in ("preview", "exp")), "lite" not in n, -(float(v.group(1)) if v else 0.0))


async def _discover_gemini_models(session) -> list:
    """Ask the API which models this key can really call (survives future deprecations)."""
    bad = ("image", "tts", "live", "audio", "embed", "native", "computer", "robot", "cyber", "omni", "veo", "imagen", "gemma")
    try:
        async with session.get(f"{GEMINI_BASE}/models", params={"pageSize": 200}) as resp:
            if resp.status != 200:
                logger.warning(f"[GEMINI] ListModels failed: HTTP {resp.status}")
                return []
            data = await resp.json()
    except Exception as e:
        logger.warning(f"[GEMINI] ListModels exception: {e}")
        return []
    names = []
    for m in data.get("models", []):
        n = m.get("name", "").replace("models/", "")
        if (n.startswith("gemini-") and "flash" in n and "generateContent" in m.get("supportedGenerationMethods", [])
                and not any(b in n for b in bad) and n not in _gemini_dead_models):
            names.append(n)
    names.sort(key=_model_rank)
    logger.info(f"[GEMINI] Discovered usable models: {names[:6]}")
    return names


def _extract_json(raw: str) -> dict:
    raw = re.sub(r"```(?:json)?", "", raw or "").strip()
    candidates = [raw]
    m = re.search(r"\{.*\}", raw, re.S)
    if m:
        candidates.append(m.group(0))
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list) and data and isinstance(data[0], dict):
            data = data[0]
        if isinstance(data, dict):
            return data
    return {}


async def _gemini_try_models(session, payload: dict, models: list):
    """Returns (result_dict | None, fatal_bool)."""
    for model in models:
        if model in _gemini_dead_models:
            continue
        url = f"{GEMINI_BASE}/models/{model}:generateContent"
        for attempt in range(GEMINI_MAX_RETRIES):
            await _gemini_throttle()
            try:
                async with session.post(url, json=payload) as resp:
                    status, body = resp.status, await resp.text()
                    retry_after = resp.headers.get("Retry-After")
            except Exception as e:
                logger.warning(f"[GEMINI] {model} request failed ({type(e).__name__}: {e}) attempt {attempt + 1}")
                await asyncio.sleep(2 * (attempt + 1))
                continue

            if status == 200:
                try:
                    parts = json.loads(body)["candidates"][0]["content"]["parts"]
                    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
                except (KeyError, IndexError, TypeError, json.JSONDecodeError):
                    logger.warning(f"[GEMINI] {model} returned unexpected payload: {body[:200]}")
                    break
                data = _extract_json(text)
                if data:
                    logger.info(f"[GEMINI] Gemini ({model}) replied: {json.dumps(data, ensure_ascii=False)}")
                    return data, False
                logger.warning(f"[GEMINI] {model} returned non-JSON text: {text[:200]!r}")
                break
            if status == 404:
                logger.error(f"[GEMINI] Model '{model}' is gone (404). Marking dead. {body[:120]}")
                _gemini_dead_models.add(model)
                break
            if status in (401, 403):
                logger.error(f"[GEMINI] Auth/permission error {status}: {body[:200]}")
                return None, True
            if status in (429, 500, 502, 503, 504):
                try:
                    delay = float(retry_after)
                except (TypeError, ValueError):
                    delay = (2 ** attempt) * 2 + random.random()
                logger.warning(f"[GEMINI] {status} on {model}; retry in {delay:.1f}s ({attempt + 1}/{GEMINI_MAX_RETRIES})")
                await asyncio.sleep(min(delay, 20))
                continue
            logger.warning(f"[GEMINI] HTTP {status} on {model}: {body[:200]}")
            break
    return None, False


async def identify_movie_with_gemini(filename: str, caption: str = "", duration_mins: Optional[int] = None,
                                     local_title: str = "", local_year: Optional[str] = None,
                                     ott_hint: str = "", digital_source: bool = False) -> dict:
    """Ask Gemini ONLY for: title, year, is_series, ott. Episodes are never sent/parsed by AI."""
    global _gemini_cooldown_until, _gemini_discovered, _gemini_models_live
    key = (GEMINI_API_KEY or "").strip()
    if not key:
        logger.info("[GEMINI] No API key configured -> using local filename parsing only.")
        return {}
    cache_key = canon_key(local_title) + "|" + str(local_year or "")
    if local_title and cache_key in _gemini_cache:
        logger.info(f"[GEMINI] Using cached Gemini answer for '{local_title}' (no new API call)")
        STATS["gemini_cache_hits"] += 1
        return _gemini_cache[cache_key]
    if time.monotonic() < _gemini_cooldown_until:
        logger.info("[GEMINI] In cooldown, using local parsing for this file.")
        return {}

    today = datetime.now().strftime("%d %B %Y")
    source_line = ("YES - the file is a WEB-DL/WEBRip/HDRip/BluRay rip, so the title has ALREADY been released digitally "
                   "(streaming / VOD / premium rental)." if digital_source else
                   "Unknown / possibly a theatrical (CAM/HDTC/PreDVD) copy.")
    prompt = (
        "You are a metadata resolver for an Indian movie/series Telegram channel.\n"
        f"Today's date is {today}. Many 2025-2026 films have ALREADY reached OTT/digital release even if they are newer "
        "than your training data, so reason about platforms instead of defaulting to an empty list.\n"
        "Identify the OFFICIAL title of the film or web series for this uploaded file.\n"
        f"- Filename: {filename}\n- Caption: {caption}\n"
        f"- Local parser guess: title=\"{local_title}\", year=\"{local_year or ''}\"\n"
        f"- Video duration: {duration_mins or 'unknown'} minutes\n"
        f"- Platform tags found in filename/caption: {ott_hint or 'none'}\n"
        f"- Digital-release source detected: {source_line}\n\n"
        "Rules:\n"
        "1. title: official name ONLY. Keep sequel numbers/subtitles of MOVIES (e.g. Sardar 2, Dhamaal 4). "
        "For a WEB SERIES return ONLY the clean series name, e.g. \"Panchayat\" - NEVER \"Panchayat Season 1\", "
        "\"Panchayat S01\" or \"Panchayat Season 1 Complete\"; season/episode numbers are handled separately by the bot. "
        "Remove resolution, source, codecs, audio/language lists, subtitle tags (ESub, MSub, ESu), uploader handles, "
        "site names, release groups. Never include season or episode numbers or the year.\n"
        "2. year: 4-digit release year (first-air year for a series).\n"
        "3. is_series: true only for TV/web series.\n"
        "4. ott: the Indian streaming platform(s) that stream or digitally release it. "
        "If a platform tag is present in the filename (AMZN/Prime = Amazon Prime Video, NF = Netflix, JHS/JioHotstar/DSNP/HS = "
        "JioHotstar or Disney+ Hotstar, ZEE5, SonyLIV/SLIV, Aha, SunNXT, Hoichoi, MX Player, Lionsgate Play, Apple TV+) "
        "treat it as strong evidence. List ALL available Indian platforms in the JSON array (e.g. [\"Netflix\", \"Amazon Prime Video\", \"JioHotstar\"]). Do not stop at one. "
        "If the digital-release source is YES, you MUST give your best answer from "
        "the film's known/announced digital rights, studio/producer deals and typical platform for its language and cast; "
        "do not return an empty list just because you are unsure. "
        "Return [] ONLY when the source is a theatrical copy (CAM/HDTC/PreDVD) with no platform tag.\n"
        "   Use these exact names: Netflix, Amazon Prime Video, JioHotstar, Disney+ Hotstar, SonyLIV, Zee5, Aha, "
        "Sun NXT, Apple TV+, Hoichoi, MX Player, Lionsgate Play.\n\n"
        "Return ONLY JSON: {\"title\": \"\", \"year\": \"YYYY\", \"is_series\": false, \"ott\": []}"
    )
    STATS["gemini_calls"] += 1
    logger.info(
        f"[GEMINI] Sending to Gemini -> filename='{filename}' | caption='{(caption or '')[:80]}' | "
        f"local guess='{local_title}' ({local_year or 'no year'}) | platform tags='{ott_hint or 'none'}' | "
        f"digital release source={'YES' if digital_source else 'NO'} | models={[m for m in _gemini_models_live if m not in _gemini_dead_models][:3]}")
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 1024, "responseMimeType": "application/json"},
    }
    headers = {"Content-Type": "application/json", "x-goog-api-key": key}
    timeout = aiohttp.ClientTimeout(total=GEMINI_TIMEOUT)

    result, fatal = None, False
    async with gemini_semaphore:                       # strictly one Gemini call at a time
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            result, fatal = await _gemini_try_models(session, payload, _gemini_models_live)
            if result is None and not fatal and not _gemini_discovered:
                _gemini_discovered = True
                found = await _discover_gemini_models(session)
                if found:
                    _gemini_models_live = found
                    result, fatal = await _gemini_try_models(session, payload, found)

    health("Gemini", bool(result), None if result else ("auth error" if fatal else "no model answered"))
    if result:
        if len(_gemini_cache) > 500:
            _gemini_cache.clear()
        if local_title:
            _gemini_cache[cache_key] = result
        return result

    alive = [m for m in _gemini_models_live if m not in _gemini_dead_models]
    if fatal:
        _gemini_cooldown_until = time.monotonic() + 600
    elif not alive:
        logger.error("[GEMINI] No working model. Set env GEMINI_MODELS to a current model id.")
        _gemini_cooldown_until = time.monotonic() + 600
    else:
        _gemini_cooldown_until = time.monotonic() + GEMINI_COOLDOWN
    return {}


async def resolve_identity(filename_clean, caption, duration_mins, title_local, year_local, local_season) -> dict:
    """Combine local parse + Gemini into one trusted identity (title/year/is_series/ott)."""
    ai = {}
    if GEMINI_API_KEY:
        hint_text = f"{filename_clean} {caption}"
        ott_hint = extract_ott_platform(hint_text.lower())
        digital = bool(re.search(r"\b(?:web[\s._-]?dl|webrip|hdrip|blu[\s._-]?ray|brrip|bdrip|amzn|nf|zee5|jhs|dsnp)\b", hint_text, re.IGNORECASE))
        ai = await identify_movie_with_gemini(filename_clean, caption, duration_mins, title_local, year_local,
                                              ott_hint if ott_hint != "N/A" else "", digital)

    ai_title, ai_year = "", None
    if isinstance(ai.get("title"), str):
        t = SERIES_SUFFIX_RE.sub("", ai["title"]).strip(" :-–—,") or ai["title"]
        if t != ai["title"]:
            logger.info(f"[GEMINI] Removed season text from AI title: '{ai['title']}' -> '{t}'")
        ai_title, y_in_title = split_trailing_year(t)
        ai_year = y_in_title
    m = re.search(r"(?:19|20)\d{2}", str(ai.get("year") or ""))
    ai_year = ai_year or (m.group(0) if m else None)

    title = title_local
    if ai_title and len(ai_title) <= 120:
        local_digits = {w for w in canon_key(title_local).split() if w.isdigit()}
        ai_digits = {w for w in canon_key(ai_title).split() if w.isdigit()}
        ratio = SequenceMatcher(None, canon_key(ai_title), canon_key(title_local)).ratio()
        stray = {d for d in (local_digits - ai_digits) if int(d) not in ARTIFACT_NUMBERS}      # 40/41/480/720/1080... are artifacts
        if stray and all(int(d) >= 10 for d in stray):
            # a stray 2-digit+ number whose removal makes the local name equal Gemini's title is a leftover token, not a sequel
            reduced = " ".join(w for w in canon_key(title_local).split() if w not in stray)
            if SequenceMatcher(None, reduced, canon_key(ai_title)).ratio() >= 0.9:
                logger.info(f"[GEMINI] Ignoring stray number(s) {sorted(stray)} in local name '{title_local}' - Gemini's '{ai_title}' matches without it")
                stray = set()
        if title_local and (stray or ratio < 0.3):
            logger.warning(f"[GEMINI] Distrusting AI title '{ai_title}' vs local '{title_local}' (sequel/similarity check)")
        else:
            title = normalize(ai_title)

    ai_series = ai.get("is_series")
    if isinstance(ai_series, str):
        ai_series = ai_series.strip().lower() == "true"
    ai_ott = _parse_ott_list(ai.get("ott"))

    is_series_final = bool(local_season is not None)       # explicit S01 / Season 1 / E01 token only
    if ai_series and not is_series_final:
        logger.info(f"[SERIES] Gemini says '{title}' is a series but the file has no S01 / Season / E01 token -> keeping it as a MOVIE "
                    f"(multi-part films like 'KGF Chapter 1' must not become series)")
    if ai:
        logger.info(f"[GEMINI] Result used -> title='{title}' | year={year_local or ai_year} | "
                    f"series={is_series_final} | OTT from Gemini={ai_ott or 'none'}")
    else:
        logger.info(f"[GEMINI] No usable Gemini answer -> falling back to local title '{title}' ({year_local or 'no year'})")
    return {
        "title": title,
        "year": year_local or ai_year,     # the year written on the file wins over the AI's guess
        "is_series": is_series_final,
        "ott": ai_ott,
        "ai_ok": bool(ai) or not GEMINI_API_KEY,
    }

def get_qualities(text: str) -> str:
    if not text:
        return "N/A"
    v_match = VERSION_STANDALONE.search(text)
    version_str = None
    if v_match:
        raw_v = v_match.group(0).upper()
        raw_v = re.sub(r'^(?:VER\.?|VERSION)\s*', 'V', raw_v)
        version_str = raw_v if raw_v.startswith("V") else f"V{raw_v}"

    resolutions = [r.lower() if r.lower() != "4k" else "4K" for r in RESOLUTION_PATTERN.findall(text)]
    resolutions = list(dict.fromkeys(resolutions))

    sources = []
    for s in SOURCE_PATTERN.findall(text):
        s_norm = re.sub(r"[._]+", "-", s).strip().lower()
        formatted = STANDARD_FORMATS.get(s_norm, s.upper())
        if formatted not in sources:
            sources.append(formatted)

    if version_str:
        attached = False
        for idx, s in enumerate(sources):
            if any(k in s.upper() for k in ["HDTC", "CAM", "TS", "PREDVD", "WEBRIP", "WEB-DL", "RIP", "BLURAY"]):
                sources[idx] = f"{s} {version_str}"
                attached = True
                break
        if not attached:
            sources.append(version_str)

    all_items = resolutions + sources
    return ", ".join(all_items) if all_items else "N/A"

def format_movie_qualities(quality_list: list) -> str:
    if not quality_list:
        return "N/A"
    resolutions = set()
    sources = set()
    versions = set()

    for item in quality_list:
        if not item or item == "N/A":
            continue
        for vm in VERSION_STANDALONE.findall(item):
            v_num = re.sub(r"\D", "", vm)
            if v_num:
                versions.add(int(v_num))
        for r in RESOLUTION_PATTERN.findall(item):
            resolutions.add(r.lower() if r.lower() != "4k" else "4K")
        for s in SOURCE_PATTERN.findall(item):
            s_norm = re.sub(r"[._]+", "-", s).strip().lower()
            sources.add(STANDARD_FORMATS.get(s_norm, s.upper()))

    if "HQ-HDTC" in sources and "HDTC" in sources: sources.remove("HDTC")
    if "HQ-CAM" in sources: sources.discard("CAM"); sources.discard("HDCam")
    if "HDCam" in sources and "CAM" in sources: sources.remove("CAM")
    if "HQ-TS" in sources: sources.discard("TS"); sources.discard("HDTS")
    if "HDTS" in sources and "TS" in sources: sources.remove("TS")
    if "HQ-PreDVD" in sources and "PreDVD" in sources: sources.remove("PreDVD")
    if "HDR10+" in sources: sources.discard("HDR10"); sources.discard("HDR")
    elif "HDR10" in sources: sources.discard("HDR")

    sorted_res = sorted(resolutions, key=lambda x: RES_ORDER.get(x.lower(), 9999))
    version_str = f"V{max(versions)}" if versions else ""
    source_list = [s for s in sorted(sources)]

    if version_str:
        attached = False
        for idx, s in enumerate(source_list):
            if any(k in s.upper() for k in ["HDTC", "CAM", "TS", "PREDVD", "WEBRIP", "WEB-DL", "RIP", "BLURAY"]):
                source_list[idx] = f"{s} {version_str}"
                attached = True
                break
        if not attached:
            source_list.append(version_str)

    final_parts = sorted_res + source_list
    return ", ".join(final_parts) if final_parts else "N/A"

def extract_ott_platform(text: str) -> str:
    text = text.lower()
    platforms = {plat for key, plat in OTT_PLATFORMS.items() if re.search(rf"\b{re.escape(key)}\b", text)}
    return " | ".join(sorted(platforms)) if platforms else "N/A"

_OTT_BASE = dict(OTT_PLATFORMS)                 # shipped table (weak keys already removed); runtime config extends / disables
_OTT_CANON_EXTRA = {
    "primevideo": "Amazon Prime Video", "amazonprimevideo": "Amazon Prime Video", "amazonprime": "Amazon Prime Video",
    "disneyplushotstar": "Disney+ Hotstar", "disneyhotstar": "Disney+ Hotstar", "hotstar": "Disney+ Hotstar",
    "jiohotstar": "JioHotstar", "jiocinema": "JioCinema", "sonyliv": "SonyLiv", "appletvplus": "Apple TV+",
    "appletv": "Apple TV+", "sunnxt": "Sun NXT", "mxplayer": "MX Player", "zee5": "Zee5",
}
_OTT_CANON = {}


def _rebuild_ott_canon():
    _OTT_CANON.clear()
    for _k, _v in OTT_PLATFORMS.items():
        _OTT_CANON[squash(_k)] = _v
        _OTT_CANON[squash(_v)] = _v
    _OTT_CANON.update(_OTT_CANON_EXTRA)


_rebuild_ott_canon()


def canonicalize_ott(item) -> Optional[str]:
    raw = str(item or "")
    s = squash(raw)
    if not s:
        return None
    if s in _OTT_CANON:
        return _OTT_CANON[s]
    low = raw.lower()
    for key, plat in OTT_PLATFORMS.items():
        if re.search(rf"\b{re.escape(key)}\b", low):
            return plat
    return None


def _without_title(text: str, title: str) -> str:
    """Lower-cased, normalised text with the movie title removed, so OTT / language keys such as 'max', 'prime', 'apple',
    'stage', 'mar', 'tel' are only matched against the release tags and never against the title itself."""
    t = normalize(text or "").lower()
    ti = normalize(title or "").lower()
    if ti:
        t = re.sub(rf"(?<![a-z0-9]){re.escape(ti)}(?![a-z0-9])", " ", t)
    return t


async def fetch_online_ott(imdb_details: dict, tmdb_details: dict, filename: str, caption: str,
                           ai_ott: list = None, trace: list = None, title: str = "") -> str:
    """Combine EVERY platform found by Gemini, TMDb providers, IMDb and filename tags -> 'A | B | C'."""
    ordered = []                       # keeps priority order, no duplicates
    trace = trace if trace is not None else []

    def add(plat, source):
        if plat and plat not in ordered:
            ordered.append(plat)
        if source not in trace:
            trace.append(source)

    # 1) Gemini
    g = [p for p in (canonicalize_ott(i) for i in (ai_ott or [])) if p]
    if g:
        for p in g: add(p, "Gemini")
        logger.info(f"[OTT] Step 1/4 Gemini: [SUCCESS] {' | '.join(g)}")
    else:
        logger.info("[OTT] Step 1/4 Gemini: [FAILED/FALLBACK] no platform returned")

    # 2) TMDb watch providers (India: flatrate + ads + rent + buy)
    tmdb_id = tmdb_details.get("id") if isinstance(tmdb_details, dict) else None
    t_found = []
    if tmdb_id and TMDB_API_KEY:
        media_type = "tv" if (tmdb_details.get("first_air_date") or tmdb_details.get("name")) else "movie"
        try:
            url = f"https://api.themoviedb.org/3/{media_type}/{tmdb_id}/watch/providers?api_key={TMDB_API_KEY}"
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        reg = (await resp.json()).get("results", {}).get("IN") or {}
                        for bucket in ("flatrate", "ads", "rent", "buy"):
                            for p in (reg.get(bucket) or []):
                                pname = str((p or {}).get("provider_name") or "")
                                if bucket != "flatrate" and squash(pname) == "appletv":
                                    continue          # plain "Apple TV" in rent/buy is the STORE, not the Apple TV+ subscription
                                plat = canonicalize_ott(pname)
                                if plat and plat not in t_found:
                                    t_found.append(plat)
        except Exception as e:
            logger.info(f"[OTT] TMDb provider request error: {e}")
    if t_found:
        for p in t_found: add(p, "TMDb")
        logger.info(f"[OTT] Step 2/4 TMDb providers: [SUCCESS] {' | '.join(t_found)}")
    else:
        logger.info("[OTT] Step 2/4 TMDb providers: [FAILED/FALLBACK] none listed for India")

    # 3) IMDb scraper
    i_found = []
    if isinstance(imdb_details, dict) and imdb_details:
        raw = imdb_details.get("distributors", "")
        raw = " , ".join(raw) if isinstance(raw, (list, tuple)) else str(raw or "")
        raw_ott = f"{raw} , {imdb_details.get('ott', '')}"
        for chunk in re.split(r"[,;|/\n]", raw_ott):          # "Sun NXT", "Amazon Prime Video" ... one name per chunk
            plat = canonicalize_ott(chunk)
            if plat and plat not in i_found:
                i_found.append(plat)
    if i_found:
        for p in i_found: add(p, "IMDb")
        logger.info(f"[OTT] Step 3/4 IMDb: [SUCCESS] {' | '.join(i_found)}")
    else:
        logger.info("[OTT] Step 3/4 IMDb: [FAILED/FALLBACK] no platform data")

    # 4) filename / caption tags (AMZN, NF, DSNP, ZEE5 ...)
    text = _without_title(f"{filename} {caption}", title)
    f_found = []
    for key, plat in OTT_PLATFORMS.items():
        if re.search(rf"\b{re.escape(key)}\b", text) and plat not in f_found:
            f_found.append(plat)
    if f_found:
        for p in f_found: add(p, "Filename tag")
        logger.info(f"[OTT] Step 4/4 Filename/caption tags: [SUCCESS] {' | '.join(f_found)}")
    else:
        logger.info("[OTT] Step 4/4 Filename/caption tags: [FAILED/FALLBACK] no tag found")

    # same service under two names -> keep one
    if "JioHotstar" in ordered:
        ordered = [p for p in ordered if p not in ("Disney+ Hotstar", "Disney+")]
    elif "Disney+ Hotstar" in ordered:
        ordered = [p for p in ordered if p != "Disney+"]
    if "HBO Max" in ordered:
        ordered = [p for p in ordered if p != "Max"]
    return " | ".join(ordered) if ordered else "N/A"


def format_runtime(runtime_val, is_series: bool = False) -> str:
    if not runtime_val or str(runtime_val).strip().upper() in ("N/A", "NONE", "0", "-", ""):
        return "N/A"
    if isinstance(runtime_val, (list, tuple)):
        if not runtime_val:
            return "N/A"
        runtime_val = runtime_val[0]

    runtime_str = str(runtime_val).strip()
    runtime_str = re.sub(r'[\s/]*(?:ep|episode)\b', '', runtime_str, flags=re.IGNORECASE).strip()
    total_mins = 0
    try:
        try:
            total_mins = int(float(runtime_str))
        except ValueError:
            colon_match = re.match(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$", runtime_str)
            if colon_match:
                hours = int(colon_match.group(1))
                mins = int(colon_match.group(2))
                total_mins = (hours * 60) + mins
            else:
                hours_match = re.search(r"(\d+)\s*(?:h|hr|hour)s?", runtime_str, re.IGNORECASE)
                mins_match = re.search(r"(\d+)\s*(?:m|min|minute)s?", runtime_str, re.IGNORECASE)
                if hours_match or mins_match:
                    hours = int(hours_match.group(1)) if hours_match else 0
                    mins = int(mins_match.group(1)) if mins_match else 0
                    total_mins = (hours * 60) + mins
                else:
                    numbers = re.findall(r"\d+", runtime_str)
                    if numbers:
                        total_mins = int(numbers[0])
    except Exception:
        return "N/A"

    if total_mins <= 0: return "N/A"
    if total_mins >= 60:
        hours = total_mins // 60
        mins = total_mins % 60
        return f"{hours}h {mins}m" if mins > 0 else f"{hours}h"
    else:
        return f"{total_mins}m"

def _kwargs_for(func, is_series: bool) -> dict:
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return {}
    if "is_series" in sig.parameters:
        return {"is_series": is_series}
    if "media_type" in sig.parameters:
        return {"media_type": "tv" if is_series else "movie"}
    return {}


def _queries(title: str, year: Optional[str], is_series: bool) -> list:
    t = re.sub(r"\s+Season\s*\d+", "", title, flags=re.IGNORECASE).strip()
    qs = [f"{t} {year}".strip() if (year and not is_series) else t, t]
    return list(dict.fromkeys(q for q in qs if q))


async def fetch_imdb_safely(title: str, is_series: bool, year: Optional[str] = None) -> dict:
    kwargs = _kwargs_for(get_movie_details, is_series)
    clean_t = re.sub(r"\s+Season\s*\d+", "", title, flags=re.IGNORECASE).strip()
    for q in _queries(title, year, is_series):
        try:
            res = await get_movie_details(q, **kwargs)
            if accept_result(res, clean_t, year, is_series):
                return res
        except Exception:
            pass
    return {}


async def fetch_tmdb_safely(title: str, is_series: bool, year: Optional[str] = None) -> dict:
    if not TMDB_POSTER:
        return {}
    kwargs = _kwargs_for(get_movie_detailsx, is_series)
    clean_t = re.sub(r"\s+Season\s*\d+", "", title, flags=re.IGNORECASE).strip()
    for q in _queries(title, year, is_series):
        try:
            res = await get_movie_detailsx(q, **kwargs)
            if accept_result(res, clean_t, year, is_series):
                return res
        except Exception:
            pass
    return {}


async def verify_hdhub_imdb_id(imdb_id: str, title: str, is_series: bool, year: Optional[str]) -> Tuple[str, dict]:
    """Checks the IMDb id scraped from HDHub. Returns ('ok', details) | ('rejected', {}) | ('unknown', {}).
    'rejected' = the id belongs to a DIFFERENT title (talk-show, interview...). 'unknown' = no service could say."""
    clean_t = re.sub(r"\s+Season\s*\d+", "", title, flags=re.IGNORECASE).strip()
    if TMDB_POSTER:
        try:
            res = await get_movie_detailsx(imdb_id, **_kwargs_for(get_movie_detailsx, is_series))
        except Exception:
            res = None
        if isinstance(res, dict) and _result_title(res):
            if accept_result(res, clean_t, year, is_series):
                return "ok", res
            logger.warning(f"[META] HDHub IMDb id {imdb_id} belongs to '{_result_title(res)}', not '{title}'")
            return "rejected", {}
    if OMDB_API_KEY:
        o = await fetch_omdb(title, year, is_series, imdb_id)
        if o:
            return "ok", {}
    return "unknown", {}


async def search_tmdb_backdrop_force(title: str, year: Optional[str] = None, is_series: bool = False) -> Optional[str]:
    """First TMDb search result that is REALLY this title (name + year checked) and has a backdrop."""
    try:
        api_key = TMDB_API_KEY
        if not api_key:
            return None
        clean_q = normalize(title or "")
        toks = clean_q.split()
        if year and len(toks) > 1 and toks[-1] == str(year):       # strip ONLY the release year ('Blade Runner 2049' stays intact)
            clean_q = " ".join(toks[:-1])
        if not clean_q:
            return None
        media_type = "tv" if is_series else "movie"
        url = f"https://api.themoviedb.org/3/search/{media_type}?api_key={api_key}&query={quote_plus(clean_q)}"

        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
        for item in (data.get("results") or []):
            if not item.get("backdrop_path"):
                continue
            found = str(item.get("title") or item.get("name") or "")
            if not (is_good_title_match(clean_q, found)
                    or SequenceMatcher(None, canon_key(clean_q), canon_key(found)).ratio() >= 0.85):
                continue                                              # a different film's backdrop is worse than none
            ry = _result_year(item)
            if year and not is_series and ry and not _years_compatible(year, ry):
                continue
            return f"https://image.tmdb.org/t/p/original{item['backdrop_path']}"
    except Exception as e:
        logger.warning(f"[POSTER] TMDb backdrop search failed: {type(e).__name__}: {e}")
    return None


async def get_hdhub_base_url() -> str:
    try:
        if hasattr(db, 'db'):
            setting = await db.db.settings.find_one({"_id": "hdhub_base_url"})
            if setting and setting.get("url"):
                return setting["url"].rstrip("/")
    except Exception:
        pass
    return DEFAULT_HDHUB_DOMAIN

async def get_blogger_poster_url(base_name: str, year: Optional[str] = None) -> Optional[str]:
    try:
        blog_url = "https://tmdbimdbhdhub4u.blogspot.com"
        title_only = normalize(base_name or "")
        # 1) Blogger's own search (finds OLD posts too)  2) the latest-50 feed
        feeds = [f"{blog_url}/feeds/posts/default?q={quote_plus(title_only)}&alt=json&max-results=25",
                 f"{blog_url}/feeds/posts/default?alt=json&max-results=50"]
        clean_query = f"{title_only} {year}".strip() if year else title_only
        timeout = aiohttp.ClientTimeout(total=6)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for feed_url in feeds:
                try:
                    async with session.get(feed_url) as resp:
                        if resp.status != 200: continue
                        data = await resp.json(content_type=None)
                except Exception:
                    continue
                for entry in ((data or {}).get("feed") or {}).get("entry") or []:
                    post_title = ((entry or {}).get("title") or {}).get("$t") or ""
                    if is_good_title_match(clean_query, post_title):
                        content_html = ((entry.get("content") or {}).get("$t")) or ""
                        soup = BeautifulSoup(content_html, "html.parser")
                        img_tag = soup.find("img")
                        if img_tag and img_tag.get("src"):
                            img_url = img_tag["src"]
                            img_url = re.sub(r'/s\d+(-c)?/', '/s1600/', img_url)
                            img_url = re.sub(r'/w\d+-[h\d]+/', '/', img_url)
                            return img_url
    except Exception as e:
        logger.warning(f"[POSTER] Blogger lookup failed: {type(e).__name__}: {e}")
    return None


# ---- HDHub4u result matching helpers (private to get_hdhub4u_data; is_good_title_match is untouched) ----
_HDHUB_JUNK_RE = re.compile(
    r"\b(?:download(?:ing)?|watch(?:ing)?|online|free|full\s*movie|full\s*hd|full|web\s*series|complete|all\s*episodes?|"
    r"episodes?|ep\s*\d+|seasons?\s*\d*|s\d{1,2}(?:\s*e\d{1,3})?|e\d{1,3}|"
    r"\d{3,4}p|4k|uhd|hevc|x\s*26[45]|h\s*26[45]|10\s*bit|\d+(?:\.\d+)?\s*(?:mb|gb)|"
    r"web\s*dl|web\s*rip|hd\s*rip|blu\s*ray|br\s*rip|bd\s*rip|dvd\s*rip|dvdscr|hdtv|hdcam|hd\s*tc|hdts|cam\s*rip|pre\s*dvd|"
    r"e\s*subs?|m\s*subs?|subs?|subbed|dual\s*audio|multi\s*audio|org(?:inal)?\s*audio|dubbed|dub|"
    r"hindi|tamil|telugu|malayalam|kannada|bengali|bangla|punjabi|marathi|gujarati|english|korean|japanese|urdu|bhojpuri|"
    r"uncut|unrated|extended|imax|remastered|proper|hq|hd|bollywood|hollywood|south|"
    r"netflix|amzn|nf|zee5|hotstar|jiohotstar|sonyliv|prime\s*video|hdhub4u|hdhub)\b",
    re.IGNORECASE,
)


_HDHUB_JUNK_BASE = _HDHUB_JUNK_RE.pattern[len(r"\b(?:"):-len(r")\b")]


def _hdhub_clean_title(text: str) -> str:
    """'Jawan (2023) Hindi Full Movie WEB-DL 480p 720p ESubs Download' -> 'Jawan'"""
    t = AUDIO_CHANNELS_PATTERN.sub(" ", text or "")
    t = re.sub(r"[\[\(\{][^\]\)\}]*[\]\)\}]", " ", t)          # (2023) [Hindi + Tamil] {1080p}
    t = _HDHUB_JUNK_RE.sub(" ", normalize(t))
    return normalize(t)


def _hdhub_tokens(s: str) -> list:
    s = normalize(YEAR_PATTERN.sub(" ", s or "")).lower()
    return [w for w in s.split() if len(w) >= 2 or w.isdigit()]


def _hdhub_fold(s: str) -> str:
    """Spelling-tolerant form: lower-case and collapse doubled vowels ('Pathaan' -> 'pathan', 'Jawaan' -> 'jawan')."""
    return re.sub(r"([aeiou])\1+", r"\1", (s or "").lower())


def _hdhub_fuzzy_ok(q_fold: str, c_fold: str) -> bool:
    a, b = q_fold.replace(" ", ""), c_fold.replace(" ", "")
    if not a or not b:
        return False
    if a == b:
        return True
    # SequenceMatcher >= 0.78 for normal titles; short names need a stricter ratio ('Fan' must not match 'Fani')
    thr = 0.78 if len(a) >= 7 else (0.88 if len(a) >= 5 else 1.01)
    return SequenceMatcher(None, a, b).ratio() >= thr


def _hdhub_match_score(query: str, title_text: str, href: str) -> Optional[int]:
    """None = no match, else a score (0 = exact; lower is better). The cleaned result title AND the link slug are
    checked. Core words must be present (or be a spelling variant) and sequel digits must agree ('Sardar' != 'Sardar 2')."""
    q = _hdhub_tokens(_hdhub_clean_title(query)) or _hdhub_tokens(query)
    if not q:
        return None
    q_digits = {w for w in q if w.isdigit()}
    q_fold = _hdhub_fold(" ".join(q))
    slug = re.sub(r"[-_]+", " ", (href or "").split("?")[0].rstrip("/").rsplit("/", 1)[-1])
    best = None
    for text in (title_text, slug):
        f = _hdhub_tokens(_hdhub_clean_title(text))
        if not f or {w for w in f if w.isdigit()} != q_digits:
            continue
        extras = len(f) - len(q)
        if extras > 4:
            continue
        if all(w in f for w in q):
            score = max(extras, 0)
        else:
            score = None
            for cand in (" ".join(f[:len(q)]), " ".join(f)):      # head of the title ignores trailing clutter
                if _hdhub_fuzzy_ok(q_fold, _hdhub_fold(cand)):
                    score = 5 + max(extras, 0)
                    break
            if score is None:
                continue
        if best is None or score < best:
            best = score
    return best


def _hdhub_browser_headers(base_url: str, referer: str, same_origin: bool = True) -> dict:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,hi;q=0.8",
        "Referer": referer or base_url,
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin" if same_origin else "none",
        "Sec-Fetch-User": "?1",
        "Sec-Ch-Ua": '"Google Chrome";v="129", "Not=A?Brand";v="8", "Chromium";v="129"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Cache-Control": "max-age=0",
    }


def _hdhub_name_variants(clean_query: str) -> list:
    """['Pathaan', 'Pathan']: the query as given + a doubled-vowel-collapsed spelling."""
    out = [clean_query]
    collapsed = re.sub(r"([aeiou])\1+", r"\1", clean_query, flags=re.IGNORECASE)
    if collapsed.lower() != clean_query.lower():
        out.append(collapsed)
    return out


def _hdhub_slug_urls(base_url: str, names: list, year: Optional[str]) -> list:
    """Direct post URLs HDHub commonly uses. Pattern-major order so the main patterns are tried for every spelling first."""
    slugs = []
    for n in names:
        sl = re.sub(r"[^a-z0-9]+", "-", n.lower()).strip("-")
        if sl and sl not in slugs:
            slugs.append(sl)
    if year:
        tails = RUNTIME_CONFIG.get("hdhub_slug_tails_year") or ["-{y}-hindi-webrip-full-movie", "-{y}-full-movie", "-{y}", "",
                                                                 "-{y}-hindi-web-dl-full-movie", "-{y}-hindi-full-movie"]
    else:
        tails = RUNTIME_CONFIG.get("hdhub_slug_tails_noyear") or ["-full-movie", "", "-hindi-full-movie"]
    urls = []
    for tail in tails:
        for sl in slugs:
            u = f"{base_url}/{sl}{tail.format(y=year)}/"
            if u not in urls:
                urls.append(u)
    return urls[:12]


def _hdhub_page_title(html: str) -> str:
    """Title of a post page (h1 first, <title> as fallback). Used to VALIDATE a direct-slug hit: a soft-404 that
    returns the homepage with HTTP 200 has the site name as title and is rejected."""
    try:
        soup = BeautifulSoup(html, "html.parser")
        h1 = soup.find("h1")
        if h1 and h1.get_text(strip=True):
            return h1.get_text(" ", strip=True)
        if soup.title and soup.title.get_text(strip=True):
            return soup.title.get_text(" ", strip=True)
    except Exception:
        pass
    return ""


def _hdhub_parse_page(movie_html: str, is_series: bool) -> Tuple[str, str, str, bool]:
    genres, rating, info_url = "N/A", "N/A", ""
    movie_soup = BeautifulSoup(movie_html, "html.parser")
    search_area = movie_soup.select_one(".entry-content, .post-content, article") or movie_soup.body or movie_soup

    for a_tag in search_area.find_all("a", href=True):
        href = a_tag["href"].strip()
        m_imdb = re.search(r'imdb\.com/title/(tt\d+)', href, re.IGNORECASE)
        if m_imdb: info_url = f"https://www.imdb.com/title/{m_imdb.group(1)}/"; break
        m_tmdb = re.search(r'themoviedb\.org/(?:movie|tv)/\d+', href, re.IGNORECASE)
        if m_tmdb: info_url = f"https://{m_tmdb.group(0)}" if not m_tmdb.group(0).startswith("http") else m_tmdb.group(0); break

    text = search_area.get_text("\n")                 # every <br>/<p>/<strong> text node on its own line
    lines = [re.sub(r'\s+', ' ', line).strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        if not is_series and re.search(r'\b(?:Season|Episodes?)\b\s*[:\-–]', line, re.IGNORECASE): is_series = True
        if rating == "N/A":
            # "IMDb Rating: 7.4/10" / "IMDb: 7.4" - but never "... 10 Reactions" (comment counters) or a bare '10'
            r_match = re.search(r'(?:IMDb(?:\s*Rating)?|Rating)\s*[:\-•]?\s*(\d{1,2}(?:\.\d)?)(?:\s*/\s*10\b|(?=\s*(?:$|[|•,;)\]])))', line, re.IGNORECASE)
            if r_match and 0 < float(r_match.group(1)) <= 10: rating = f"{float(r_match.group(1)):.1f}"
    genres = extract_genre_text(text)
    return genres, rating, info_url, is_series


async def _hdhub_scrape(base_name: str, year: Optional[str], diag: dict) -> Tuple[str, str, str, bool]:
    """HDHub4u lookup. 1) direct slug probes  2) /?s= search (spelling variants, page 1 then page 2, fuzzy match)
    3) extract genre / rating / IMDb link from the post page."""
    NA = ("N/A", "N/A", "", False)
    is_series = False
    try:
        base_url = (await get_hdhub_base_url() or DEFAULT_HDHUB_DOMAIN).rstrip("/")
        name = normalize(base_name or "")
        year = str(year).strip() if year and re.fullmatch(r"(?:19|20)\d{2}", str(year).strip()) else None
        tokens = name.split()
        if len(tokens) > 1 and YEAR_PATTERN.fullmatch(tokens[-1]):      # only a TRAILING year is the release year
            year = year or tokens[-1]
            if tokens[-1] == (year or ""):
                tokens = tokens[:-1]
        clean_query = " ".join(tokens).strip()
        if not clean_query:
            return NA
        variants = _hdhub_name_variants(clean_query)

        page_html, page_title, via = None, "", ""
        timeout = aiohttp.ClientTimeout(total=7)
        # ONE session for every request so Cloudflare/site cookies carry over; closed even on errors
        async with aiohttp.ClientSession(timeout=timeout) as session:

            async def fetch(url, referer):
                try:
                    async with session.get(url, headers=_hdhub_browser_headers(base_url, referer), allow_redirects=True) as resp:
                        if resp.status != 200:
                            return resp.status, None
                        return 200, await resp.text()
                except Exception as e:
                    return type(e).__name__, None

            # ---------- Step 1/3: direct slug probes ----------
            urls = _hdhub_slug_urls(base_url, variants, year)
            sem = asyncio.Semaphore(5)

            async def probe(u):
                async with sem:
                    st, page_text = await fetch(u, base_url + "/")
                    return u, st, page_text
            probed = await asyncio.gather(*(probe(u) for u in urls))
            status_count = {}
            for u, st, page_text in probed:
                status_count[st] = status_count.get(st, 0) + 1
                if page_html is None and st == 200 and page_text:
                    t = _hdhub_page_title(page_text)
                    if _hdhub_match_score(clean_query, t, "") is not None:      # validate by PAGE TITLE (never by our own slug)
                        page_html, page_title, via = page_text, t, u
                    else:
                        logger.info(f"[HDHUB] direct slug {u} answered HTTP 200 but the page is '{t[:60] or 'untitled'}', not '{clean_query}' - ignored")
            if status_count and all((k in (403, 429, 503)) or isinstance(k, str) for k in status_count):
                diag["blocked"] = True                      # every probe was refused / errored -> we are blocked, not "not found"
            if page_html is not None:
                logger.info(f"[HDHUB] Step 1/3 Direct slug probe: [SUCCESS] found '{page_title[:70]}' at {via}")
            else:
                summary = ", ".join(f"HTTP {k} x{v}" for k, v in status_count.items()) or "no URLs"
                logger.info(f"[HDHUB] Step 1/3 Direct slug probe ({len(urls)} URLs): [FAILED/FALLBACK] no matching post ({summary}) -> trying search")

            # ---------- Step 2/3: search (variants, page 1 then page 2) ----------
            if page_html is None:
                attempts = [(v, 1) for v in variants] + [(v, 2) for v in variants]
                blocked = False
                for variant, page in attempts:
                    if blocked:
                        break
                    words = [w for w in variant.split() if len(w) >= 2][:3]
                    q = '+'.join(words) if words else variant
                    search_url = f"{base_url}/?s={q}" + ("&paged=2" if page == 2 else "")
                    label = f"/?s={q}" + (" page 2" if page == 2 else "")
                    st, page_text = await fetch(search_url, base_url + "/")
                    if st != 200:
                        logger.warning(f"[HDHUB] search returned HTTP {st}")
                        logger.info(f"[HDHUB] Step 2/3 Search {label}: [FAILED/FALLBACK] HTTP {st}")
                        blocked = st in (403, 429, 503)
                        if blocked: diag["blocked"] = True
                        continue
                    if re.search(r"just a moment|cf-browser-verification|challenge-platform|cf-chl", page_text[:6000], re.IGNORECASE):
                        logger.warning("[HDHUB] search returned a Cloudflare challenge page (HTTP 200 but no results) - headers alone cannot pass it")
                        logger.info(f"[HDHUB] Step 2/3 Search {label}: [FAILED/FALLBACK] Cloudflare challenge")
                        blocked = True
                        diag["blocked"] = True
                        continue

                    soup = BeautifulSoup(page_text, "html.parser")
                    for tag in soup(["header", "nav", "footer", "aside", "script", "style", "form"]): tag.decompose()
                    candidates, seen_hrefs = [], set()
                    for art in soup.select("article, .post-item, .recent-movies li, .entry-title, .thumb"):
                        a_tag = art.find("a", href=True)
                        if not a_tag: continue
                        href = a_tag["href"].strip()
                        if not href or href == "#" or any(x in href for x in ["/category/", "/tag/", "/author/", "/page/"]): continue
                        if not href.startswith("http"): href = f"{base_url}/{href.lstrip('/')}"
                        if href in seen_hrefs: continue
                        seen_hrefs.add(href)
                        img = a_tag.find("img") or art.find("img")
                        # the first <a> often wraps only the poster -> fall back to title attr / img alt / whole card text
                        title_text = (a_tag.get_text(" ", strip=True) or (a_tag.get("title") or "").strip()
                                      or ((img.get("alt") or "").strip() if img else "") or art.get_text(" ", strip=True))
                        candidates.append((title_text, href))

                    best_score, best_url, best_title = None, None, ""
                    for title_text, href in candidates:
                        sc = _hdhub_match_score(clean_query, title_text, href)
                        if sc is not None and (best_score is None or sc < best_score):
                            best_score, best_url, best_title = sc, href, title_text
                            if sc == 0:
                                break
                    if not best_url:
                        preview = " | ".join(t[:40] for t, _ in candidates[:3]) or "none"
                        logger.info(f"[HDHUB] Step 2/3 Search {label}: [FAILED/FALLBACK] {len(candidates)} cards, none matched '{variant}'. First: {preview}")
                        continue
                    st, page_text = await fetch(best_url, search_url)
                    if st != 200:
                        logger.warning(f"[HDHUB] movie page returned HTTP {st} ({best_url})")
                        logger.info(f"[HDHUB] Step 2/3 Search {label}: [FAILED/FALLBACK] matched '{best_title[:50]}' but its page returned HTTP {st}")
                        continue
                    page_html, page_title, via = page_text, best_title, best_url
                    logger.info(f"[HDHUB] Step 2/3 Search {label}: [SUCCESS] matched '{best_title[:70]}' -> {best_url}")
                    break

        if page_html is None:
            logger.info(f"[HDHUB] [FAILED/FALLBACK] '{clean_query}' not found by direct slug or search")
            return NA

        # ---------- Step 3/3: extract ----------
        if re.search(r'\b(?:Season\s*\d+|S\d{1,2}|Series|Episodes?)\b', page_title or "", re.IGNORECASE):
            is_series = True
        genres, rating, info_url, is_series = _hdhub_parse_page(page_html, is_series)
        if genres != "N/A" or rating != "N/A" or info_url:
            logger.info(f"[HDHUB] Step 3/3 Extract: [SUCCESS] genres={genres} | rating={rating} | imdb_url={info_url or 'none'}")
        else:
            logger.info("[HDHUB] Step 3/3 Extract: [FAILED/FALLBACK] post opened but no genre / rating / IMDb link on the page")
        return genres, rating, info_url, is_series
    except Exception as e:
        logger.warning(f"[HDHUB] scrape error: {type(e).__name__}: {e}")
        diag["blocked"] = True
    return NA


async def get_hdhub4u_data(base_name: str, year: Optional[str] = None) -> Tuple[str, str, str, bool]:
    """Circuit-breaker + health/stats wrapper. After HDHUB_BREAKER_THRESHOLD consecutive BLOCKED lookups (403 / Cloudflare /
    network errors - 'not found' does not count) HDHub4u is skipped for HDHUB_BREAKER_COOLDOWN seconds."""
    now = time.monotonic()
    if now < _hdhub_breaker["open_until"]:
        logger.info(f"[HDHUB] circuit breaker OPEN ({int(_hdhub_breaker['open_until'] - now)}s left) - skipping HDHub4u for '{base_name}'")
        STATS["hdhub_skipped_breaker"] += 1
        return "N/A", "N/A", "", False
    diag = {"blocked": False}
    res = await _hdhub_scrape(base_name, year, diag)
    found = res[0] != "N/A" or res[1] != "N/A" or bool(res[2])
    if found or not diag["blocked"]:
        _hdhub_breaker["fails"] = 0
    else:
        _hdhub_breaker["fails"] += 1
        if _hdhub_breaker["fails"] >= HDHUB_BREAKER_THRESHOLD:
            _hdhub_breaker["open_until"] = time.monotonic() + HDHUB_BREAKER_COOLDOWN
            _hdhub_breaker["fails"] = 0
            logger.warning(f"[HDHUB] {HDHUB_BREAKER_THRESHOLD} blocked lookups in a row -> circuit breaker OPEN for {HDHUB_BREAKER_COOLDOWN}s")
    STATS["hdhub_found" if found else ("hdhub_blocked" if diag["blocked"] else "hdhub_notfound")] += 1
    return res


SEASON_ONLY_REGEX = re.compile(r"(?<![A-Za-z0-9])(?:S|Season|Saison)[\s._-]*0*(\d{1,2})(?![A-Za-z0-9])", re.IGNORECASE)


def _valid_episode_range(a: int, b: int) -> bool:
    """'5-108' from 'S01E05-1080p' is NOT a range. Real ranges ascend, are short and never end in a resolution/codec number."""
    return a < b and (b - a) <= 150 and b not in (264, 265, 360, 480, 540, 720, 1080, 1440, 2160)


def extract_season_episode(filename: str) -> Tuple[Optional[int], Optional[str]]:
    filename = AUDIO_CHANNELS_PATTERN.sub(" ", filename or "")
    if m := BONUS_RANGE_REGEX.search(filename): return int(m.group(1)), f"Bonus {int(m.group(2))}-{int(m.group(3))}"
    if m := BONUS_REGEX.search(filename): return int(m.group(1)), f"Bonus {int(m.group(2))}"
    if m := RANGE_REGEX.search(filename):
        if _valid_episode_range(int(m.group(2)), int(m.group(3))):
            return int(m.group(1)), f"{int(m.group(2))}-{int(m.group(3))}"
        return int(m.group(1)), str(int(m.group(2)))             # 'S01E05-720p' -> single episode 5
    if m := SINGLE_REGEX.search(filename): return int(m.group(1)), str(int(m.group(2)))
    if m := NAMED_REGEX.search(filename): return int(m.group(1)), str(int(m.group(2)))
    if m := X_REGEX.search(filename):
        s_val, ep_val = int(m.group(1)), int(m.group(2))
        if ep_val not in (264, 265, 720, 1080) and s_val not in (264, 265): return s_val, str(ep_val)
    if m := DAY_REGEX.search(filename):
        ep_val = m.group(2) or m.group(3)
        # a bare "D2" (no season, no word 'Day') is far more often part of a movie title than a reality-show day
        if ep_val and not (m.group(3) and not m.group(1) and not m.group(2)):
            return int(m.group(1)) if m.group(1) else 1, str(int(ep_val))
    if m := NO_S_REGEX.search(filename): return int(m.group(1)), str(int(m.group(2)))
    if m := EP_ONLY_RANGE.search(filename):
        if _valid_episode_range(int(m.group(1)), int(m.group(2))):
            return 1, f"{int(m.group(1))}-{int(m.group(2))}"
    if m := EP_ONLY_SINGLE.search(filename):
        if int(m.group(1)) not in (264, 265): return 1, str(int(m.group(1)))
    # season marker but NO episode (S01 COMBiNED, Season 1 Complete, S01-S03 pack): still a series -> season only.
    # NOTE: "Part 1" / "Chapter 1" are deliberately NOT season markers (KGF Chapter 1, Salaar Part 1 stay movies).
    if m := SEASON_ONLY_REGEX.search(filename): return int(m.group(1)), None
    return None, None


_update_running: set = set()      # edits currently talking to Telegram (never cancelled mid-flight)
_update_dirty: set = set()        # new data arrived while an edit was running -> redo once afterwards


def schedule_update(bot, base_name, delay=6):
    """Debounced edit: a burst of uploads results in ONE edit of the existing post."""
    if base_name in _update_running:           # in flight: do NOT cancel it, just re-run when it finishes
        _update_dirty.add(base_name)
        return
    old = pending_updates.pop(base_name, None)
    if old and not old.done():                 # still sleeping -> safe to replace
        old.cancel()

    async def _run():
        await asyncio.sleep(delay)
        for _ in range(90):                    # the first post may still be uploading (flood-wait) -> wait for it
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

    task = asyncio.get_running_loop().create_task(_run())
    pending_updates[base_name] = task

    def _done(t, key=base_name):
        if pending_updates.get(key) is t:
            pending_updates.pop(key, None)
        if not t.cancelled() and t.exception():
            logger.error(f"Scheduled update failed for {key}: {t.exception()}")
    task.add_done_callback(_done)

def _strip_season_episode_tokens(name: str) -> str:
    if not name: return name
    patterns = [
        r"\bS\d{1,2}[\s._-]*(?:Bonus|Special)[\s._-]*(?:E(?:p(?:isode)?)?)?0*\d{1,3}\b",
        r"\b(?:Bonus|Special)[\s._-]*Ep(?:isode)?\.?\s*\d{1,3}\b",
        r"\bS\d{1,2}E\d{1,3}\b", r"\bS\d{1,2}\b", r"\bE\d{1,3}\b", r"\b\d{1,2}x\d{1,3}\b",
        r"\bSeason\s*\d{1,2}\b", r"\bEp(?:isode)?\.?\s*\d{1,3}\b", r"\bEpisode\s*\d{1,3}\b",
        r"\b[vV]\d+\b", r"\b(?:version|ver)\.?\s*\d+\b",
        r"\b(?:dd|ddp|ac3|eac3|aac)?\s*[257]\s*[._]\s*[01]\b", r"\b(?:dd|ddp)\s*[257]\b",
        r"\b(?:hin|hindi|tam|tamil|tel|telugu|mal|malayalam|kan|kannada|ben|bengali|mar|marathi|guj|gujarati|pun|punjabi|eng|english|kor|korean|jpn|japanese|dual|multi|audio|dubbed)\b",
        r"\b(?:reloaded|uncut)\b",
        r"\b(?:[hH][\s._-]*26[45]|[xX][\s._-]*26[45])\b"
    ]
    for p in patterns: name = re.sub(p, " ", name, flags=re.IGNORECASE)
    return normalize(name).strip()

async def fetch_omdb(title: str, year: Optional[str], is_series: bool, imdb_id: Optional[str] = None) -> dict:
    """OMDb fallback (needs OMDB_API_KEY). Result is validated like every other source."""
    if not OMDB_API_KEY:
        return {}
    clean_t = re.sub(r"\s+Season\s*\d+", "", title, flags=re.IGNORECASE).strip()
    params = {"apikey": OMDB_API_KEY}
    if imdb_id and str(imdb_id).startswith("tt"):
        params["i"] = imdb_id
    else:
        params["t"] = clean_t
        params["type"] = "series" if is_series else "movie"
        if year and not is_series:
            params["y"] = str(year)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6)) as session:
            async with session.get(OMDB_URL, params=params) as resp:
                if resp.status != 200:
                    return {}
                data = await resp.json(content_type=None)
    except Exception as e:
        logger.warning(f"[OMDB] request failed: {e}")
        return {}
    if not isinstance(data, dict) or data.get("Response") != "True":
        return {}
    poster = data.get("Poster") or ""
    res = {
        "title": data.get("Title"), "year": data.get("Year"), "genres": data.get("Genre"),
        "rating": data.get("imdbRating"), "imdb_id": data.get("imdbID"), "runtime": data.get("Runtime"),
        "poster_url": poster if poster.startswith("http") else "",
    }
    if not accept_result(res, clean_t, year, is_series):
        logger.warning(f"[OMDB] Rejected '{res.get('title')}' for '{title}'")
        return {}
    return res


def _valid_rating(r) -> Optional[str]:
    try:
        v = float(str(r).split("/")[0].strip())
    except (TypeError, ValueError):
        return None
    return f"{v:.1f}" if 0 < v <= 10 else None


def _to_minutes(v) -> Optional[int]:
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        nums = [n for n in (_to_minutes(x) for x in v) if n]
        return round(sum(nums) / len(nums)) if nums else None
    sv = str(v).strip()
    if not sv or sv.upper() in ("N/A", "NONE", "0", "-"):
        return None
    c = re.match(r"^(\d{1,2}):(\d{2})", sv)
    if c:
        return int(c.group(1)) * 60 + int(c.group(2))
    h, m = re.search(r"(\d+)\s*h", sv, re.I), re.search(r"(\d+)\s*m", sv, re.I)
    if h or m:
        return (int(h.group(1)) if h else 0) * 60 + (int(m.group(1)) if m else 0)
    n = re.search(r"\d+", sv)
    return int(n.group(0)) if n else None


def _mlog(step: int, name: str, ok: bool, detail: str):
    logger.info(f"[METADATA] Step {step}/4 {name}: {'[SUCCESS]' if ok else '[FAILED/FALLBACK]'} {detail}")


def _plog(step: int, name: str, ok: bool, detail: str):
    logger.info(f"[POSTER] Step {step}/8 {name}: {'[SUCCESS]' if ok else '[FAILED/FALLBACK]'} {detail}")


# =====================================================================
#  RUNTIME INFRASTRUCTURE: stats, health, cache, runtime config, indexes
# =====================================================================
STATS = defaultdict(int)
_HEALTH: dict = {}
_health_events = 0
_hdhub_breaker = {"fails": 0, "open_until": 0.0}
HDHUB_BREAKER_THRESHOLD = 5
HDHUB_BREAKER_COOLDOWN = 300
CACHE_TTL = int(os.environ.get("CHANNEL_CACHE_TTL", "86400"))       # positive results are reused for 24h (0 = off)
CACHE_NEG_TTL = 1200                                                # "nothing found" is remembered for 20 min
_meta_cache: dict = {}


def health_summary() -> str:
    if not _HEALTH:
        return "[HEALTH] no data yet"
    return "[HEALTH] " + " | ".join(f"{n}: {h['ok']}/{h['ok'] + h['fail']} ok" + (f" (last error: {h['last_error']})" if h["last_error"] else "")
                                    for n, h in sorted(_HEALTH.items()))


def health(source: str, ok: bool, err: str = None):
    """Per-source success/failure counters; a one-line summary is logged every 25 events."""
    global _health_events
    h = _HEALTH.setdefault(source, {"ok": 0, "fail": 0, "last_error": None})
    h["ok" if ok else "fail"] += 1
    if not ok and err:
        h["last_error"] = str(err)[:80]
    _health_events += 1
    if _health_events % 25 == 0:
        logger.info(health_summary())


async def _cached(kind: str, key: tuple, factory, is_empty=lambda v: not v):
    """TTL cache for scraper/API lookups. Never caches exceptions."""
    if CACHE_TTL <= 0:
        return await factory()
    k, now = (kind, key), time.monotonic()
    hit = _meta_cache.get(k)
    if hit and hit[0] > now:
        STATS[f"cache_hit_{kind}"] += 1
        return hit[1]
    val = await factory()
    if len(_meta_cache) > 2000:
        _meta_cache.clear()
    _meta_cache[k] = (now + (CACHE_NEG_TTL if is_empty(val) else CACHE_TTL), val)
    return val


CONFIG_KEYS = {"hdhub_slug_tails_year": list, "hdhub_slug_tails_noyear": list, "extra_junk_words": list, "ott_extra": dict,
               "ott_disabled": list, "hdhub_breaker_threshold": int, "hdhub_breaker_cooldown": int, "cache_ttl": int}
RUNTIME_CONFIG: dict = {}
_cfg_loaded_at = 0.0


def apply_runtime_config():
    """Push the DB-stored overrides into the live tables (HDHub junk words, OTT keys, breaker, cache)."""
    global CACHE_TTL, HDHUB_BREAKER_THRESHOLD, HDHUB_BREAKER_COOLDOWN, _HDHUB_JUNK_RE
    c = RUNTIME_CONFIG
    HDHUB_BREAKER_THRESHOLD = int(c.get("hdhub_breaker_threshold") or 5)
    HDHUB_BREAKER_COOLDOWN = int(c.get("hdhub_breaker_cooldown") or 300)
    if "cache_ttl" in c:
        CACHE_TTL = int(c["cache_ttl"])
    extra = [str(w) for w in (c.get("extra_junk_words") or []) if str(w).strip()]
    _HDHUB_JUNK_RE = re.compile(r"\b(?:" + _HDHUB_JUNK_BASE + "".join("|" + re.escape(w) for w in extra) + r")\b", re.IGNORECASE)
    table = dict(_OTT_BASE)
    table.update({str(k).lower(): str(v) for k, v in (c.get("ott_extra") or {}).items()})
    for k in (c.get("ott_disabled") or []):
        table.pop(str(k).lower(), None)
    OTT_PLATFORMS.clear()
    OTT_PLATFORMS.update(table)
    _rebuild_ott_canon()


async def load_runtime_config(force: bool = False) -> dict:
    """Admin-editable settings live in db.settings['channel_config'] (see /chconfig). Re-read at most once a minute."""
    global _cfg_loaded_at
    if not force and time.monotonic() - _cfg_loaded_at < 60:
        return RUNTIME_CONFIG
    _cfg_loaded_at = time.monotonic()
    try:
        doc = await db.db.settings.find_one({"_id": "channel_config"}) or {}
    except Exception as e:
        logger.warning(f"[CONFIG] could not read channel_config: {e}")
        return RUNTIME_CONFIG
    cfg = {k: v for k, v in doc.items() if k in CONFIG_KEYS and isinstance(v, CONFIG_KEYS[k])}
    if cfg != RUNTIME_CONFIG:
        RUNTIME_CONFIG.clear()
        RUNTIME_CONFIG.update(cfg)
        apply_runtime_config()
        logger.info(f"[CONFIG] runtime config applied: {sorted(cfg)}")
    return RUNTIME_CONFIG


_indexes_ready = False


async def ensure_indexes():
    """Plain (non-unique) indexes: remakes legitimately share a clean_title (Don 1978 / Don 2006), so uniqueness is on _id only."""
    global _indexes_ready
    if _indexes_ready:
        return
    _indexes_ready = True
    try:
        await db.movie_updates.create_index("clean_title")
        await db.movie_updates.create_index("alias_keys")
    except Exception as e:
        logger.warning(f"[DB] index creation skipped: {e}")


SERIES_EP_MAX_MINUTES = 180      # a single episode is never this long -> longer values are combined/batch files


def is_single_episode(season, episode) -> bool:
    """True only for one normal episode (S01E05). Packs (S01 COMBiNED), ranges (1-8) and Bonus are NOT single."""
    return season is not None and episode is not None and str(episode).strip().isdigit()


async def collect_metadata(identity: dict, filename: str, caption: str, file_runtime: str) -> dict:
    """
    Genre / Rating / IMDb URL / Runtime :  IMDb scraper -> OMDb -> HDHub4u -> TMDb -> N/A
    Poster   :  Blogger -> TMDb/IMDb/OMDb landscape -> TMDb/IMDb/OMDb portrait -> text-only (sent as-is, never converted)
    OTT      :  Gemini + TMDb providers + IMDb + filename tags, ALL combined
    Runtime  :  IMDb -> OMDb -> TMDb -> (movie: Telegram file | series: average of uploaded single episodes)
    """
    title, year, is_series = identity["title"], identity.get("year"), identity["is_series"]
    STATS["metadata_lookups"] += 1
    logger.info(f"[METADATA] Looking up '{title}' ({year or 'year unknown'}) as a {'web series' if is_series else 'movie'} "
                f"- order: IMDb scraper -> OMDb -> HDHub4u -> TMDb")

    def _imdb_link(i):
        return f"https://www.imdb.com/title/{i}/" if i and str(i).startswith("tt") else ""

    genres_list, genre_src, rating, rating_src, imdb_url, url_src = [], "N/A", None, "N/A", "", "N/A"

    def fill(src, g_, r_, u_):
        nonlocal genres_list, genre_src, rating, rating_src, imdb_url, url_src
        filled = []
        if not genres_list and g_: genres_list, genre_src = list(g_), src; filled.append("genres")
        if not rating and r_: rating, rating_src = r_, src; filled.append("rating")
        if not imdb_url and u_: imdb_url, url_src = u_, src; filled.append("IMDb URL")
        return filled

    def _missing():
        return [n for n, v in (("genres", genres_list), ("rating", rating), ("IMDb URL", imdb_url)) if not v]

    # IMDb scraper + TMDb are fetched together (TMDb is also needed for poster / OTT / runtime)
    imdb_res, tmdb_res = await asyncio.gather(
        _cached("imdb", (title, year, is_series), lambda: fetch_imdb_safely(title, is_series, year)),
        _cached("tmdb", (title, year, is_series), lambda: fetch_tmdb_safely(title, is_series, year)), return_exceptions=True)
    imdb_details = imdb_res if isinstance(imdb_res, dict) else {}
    tmdb_details = tmdb_res if isinstance(tmdb_res, dict) and tmdb_res else {}
    known_id = imdb_details.get("imdb_id") or tmdb_details.get("imdb_id")
    omdb_box = {}

    async def omdb():           # lazy: at most one OMDb request per file
        if "v" not in omdb_box:
            omdb_box["v"] = await _cached("omdb", (title, year, is_series, known_id), lambda: fetch_omdb(title, year, is_series, known_id)) or {}
            health("OMDb", bool(omdb_box["v"]), None if omdb_box["v"] else "no match / key missing")
        return omdb_box["v"]

    # ---------- Step 1/4: IMDb scraper ----------
    health("IMDb scraper", bool(imdb_details), None if imdb_details else "no validated match")
    if imdb_details:
        got = fill("IMDb scraper", clean_genres(imdb_details.get("genres")), _valid_rating(imdb_details.get("rating")), _imdb_link(imdb_details.get("imdb_id")))
        _mlog(1, "IMDb scraper", True, f"matched '{_result_title(imdb_details)}' | " + (f"filled {', '.join(got)}" if got else "nothing usable in it"))
    else:
        _mlog(1, "IMDb scraper", False, "no validated IMDb match (title/year/genre check failed or no result)")

    # ---------- Step 2/4: OMDb ----------
    if _missing():
        o = await omdb()
        if o:
            got = fill("OMDb", clean_genres(o.get("genres")), _valid_rating(o.get("rating")), _imdb_link(o.get("imdb_id")))
            _mlog(2, "OMDb", bool(got), f"matched '{o.get('title')}' | " + (f"filled {', '.join(got)}" if got else "had nothing for the missing fields"))
        else:
            _mlog(2, "OMDb", False, "no validated match" if OMDB_API_KEY else "OMDB_API_KEY is not set")
    else:
        logger.info("[METADATA] Step 2/4 OMDb: [SKIPPED] not needed, genre/rating/IMDb URL already found")

    # ---------- Step 3/4: HDHub4u (only when something is still missing) ----------
    hd_verified, tt = {}, None
    if _missing():
        hd_why = "page not found / nothing extracted"
        hd_genres, hd_rating, hd_url = [], None, ""
        try:
            g, r, u, _ = await _cached("hdhub", (title, year), lambda: get_hdhub4u_data(title, year),
                                       is_empty=lambda v: v[0] == "N/A" and v[1] == "N/A" and not v[2])
        except Exception as e:
            logger.warning(f"[HDHUB] scrape failed: {e}")
            g, r, u, hd_why = "N/A", "N/A", "", f"scraper error ({type(e).__name__})"
        tt = re.search(r"tt\d+", u or "")
        trusted = True
        if tt:   # the scraped IMDb id must belong to THIS title, otherwise HDHub gave us the wrong page
            verdict, hd_verified = await verify_hdhub_imdb_id(tt.group(0), title, is_series, year)
            trusted = verdict != "rejected"
            if not trusted:
                hd_why = f"IMDb id {tt.group(0)} belongs to a different title (wrong page) - ignored"
            elif verdict == "unknown":
                logger.info(f"[HDHUB] IMDb id {tt.group(0)} could not be cross-checked (TMDb/OMDb had no record) - trusting the title match")
        if trusted:
            hd_genres, hd_rating, hd_url = clean_genres(g), _valid_rating(r), (u or "").strip()
        got = fill("HDHub4u", hd_genres, hd_rating, hd_url)
        health("HDHub4u", bool(got), None if got else hd_why)
        if got:
            _mlog(3, "HDHub4u", True, f"filled {', '.join(got)} | genres={', '.join(hd_genres) or 'none'} | rating={hd_rating or 'none'} | imdb_url={hd_url or 'none'}")
        else:
            _mlog(3, "HDHub4u", False, hd_why)
    else:
        logger.info("[METADATA] Step 3/4 HDHub4u: [SKIPPED] not needed, earlier sources filled everything")
    if not tmdb_details and hd_verified:
        tmdb_details = hd_verified

    # ---------- Step 4/4: TMDb ----------
    health("TMDb", bool(tmdb_details), None if tmdb_details else "no validated match")
    if _missing():
        if tmdb_details:
            tmdb_url = _imdb_link(tmdb_details.get("imdb_id")) or (
                f"https://www.themoviedb.org/{'tv' if is_series else 'movie'}/{tmdb_details['id']}" if tmdb_details.get("id") else "")
            got = fill("TMDb", clean_genres(tmdb_details.get("genres")), _valid_rating(tmdb_details.get("rating")), tmdb_url)
            _mlog(4, "TMDb", bool(got), f"matched '{_result_title(tmdb_details)}' | " + (f"filled {', '.join(got)}" if got else "had nothing for the missing fields"))
        else:
            _mlog(4, "TMDb", False, "no validated TMDb match")
    else:
        logger.info("[METADATA] Step 4/4 TMDb: [SKIPPED] not needed for genre/rating/IMDb URL (TMDb is still used for poster/OTT/runtime if it matched)")
    if _missing():
        logger.info(f"[METADATA] Still missing after every source: {', '.join(_missing())} -> shown as N/A")

    # ---------- OTT (all sources combined) ----------
    ott_trace = []
    ott_platform = await fetch_online_ott(imdb_details, tmdb_details, filename, caption, identity.get("ott"), ott_trace, title)

    # ---------- Poster (images are sent AS-IS, never converted):
    #   Blogger -> TMDb(landscape) -> IMDb(landscape) -> OMDb(landscape) -> TMDb(portrait) -> IMDb(portrait) -> OMDb(portrait) -> text-only
    poster_url, is_backdrop, poster_src = "", False, "none (text-only post)"
    _ok_url = lambda u: bool(u) and str(u).startswith("http")

    def take(n, name, url, src, landscape, why_not):
        nonlocal poster_url, is_backdrop, poster_src
        if poster_url:
            logger.info(f"[POSTER] Step {n}/8 {name}: [SKIPPED] not needed, poster already found")
        elif _ok_url(url):
            poster_url, is_backdrop, poster_src = str(url), landscape, src
            _plog(n, name, True, str(url))
        else:
            _plog(n, name, False, why_not)

    blogger = None
    try:
        blogger = await get_blogger_poster_url(title, year)
    except Exception as e:
        logger.warning(f"[POSTER] Blogger error: {e}")
    take(1, "Blogger (RPEditz, landscape)", blogger, "Blogger (RPEditz)", True, "no custom poster post found for this title")

    t_land, t_src = None, "TMDb backdrop"
    if not poster_url:
        t_land = tmdb_details.get("backdrop_url")
        if not _ok_url(t_land):
            t_land, t_src = await search_tmdb_backdrop_force(title, year, is_series), "TMDb backdrop (search)"
    take(2, "TMDb backdrop (landscape)", t_land, t_src, True, "no TMDb backdrop" if tmdb_details else "no validated TMDb match")
    take(3, "IMDb scraper backdrop (landscape)", imdb_details.get("backdrop_url"), "IMDb scraper backdrop", True,
         "matched title but it has no backdrop" if imdb_details else "no validated IMDb match")
    if poster_url:
        logger.info("[POSTER] Step 4/8 OMDb (landscape): [SKIPPED] not needed, poster already found")
    else:
        _plog(4, "OMDb (landscape)", False, "OMDb only provides portrait posters")
    take(5, "TMDb poster (portrait)", tmdb_details.get("poster_url"), "TMDb poster", False, "no TMDb poster" if tmdb_details else "no validated TMDb match")
    take(6, "IMDb scraper poster (portrait)", imdb_details.get("poster_url"), "IMDb scraper poster", False,
         "matched title but it has no poster" if imdb_details else "no validated IMDb match")
    op = (await omdb()).get("poster_url") if not poster_url else None
    take(7, "OMDb Poster (portrait)", op, "OMDb poster", False, "OMDb has no Poster for this title" if OMDB_API_KEY else "OMDB_API_KEY is not set")
    if not poster_url:
        _plog(8, "Text-only fallback", True, "no poster in any source -> a clean text message will be sent (no logo / no banner / no link preview)")

    # ---------- Runtime: IMDb -> OMDb -> TMDb -> (movie: Telegram file | series: average of single episodes) ----------
    def _rlog(step, name, ok, detail):
        logger.info(f"[RUNTIME] Step {step}/4 {name}: {'[SUCCESS]' if ok else '[FAILED/FALLBACK]'} {detail}")

    def _sane(mins, label):
        if mins and is_series and mins > SERIES_EP_MAX_MINUTES:
            logger.info(f"[RUNTIME] {label} gave {mins} min which is too long for one episode - ignored")
            return None
        return mins

    runtime_min, runtime_src = None, "N/A"
    mins = _sane(_to_minutes(imdb_details.get("runtime")), "IMDb")
    if mins:
        runtime_min, runtime_src = mins, "IMDb episode runtime" if is_series else "IMDb"
        _rlog(1, runtime_src, True, f"{mins} min")
    else:
        _rlog(1, "IMDb scraper", False, "IMDb returned no usable runtime" if imdb_details else "no validated IMDb match")

    if not runtime_min:
        mins = _sane(_to_minutes((await omdb()).get("runtime")), "OMDb") if (OMDB_API_KEY or omdb_box.get("v")) else None
        if mins:
            runtime_min, runtime_src = mins, "OMDb"
            _rlog(2, "OMDb", True, f"{mins} min")
        else:
            _rlog(2, "OMDb", False, "OMDb returned no usable runtime" if OMDB_API_KEY else "OMDB_API_KEY is not set")
    else:
        logger.info("[RUNTIME] Step 2/4 OMDb: [SKIPPED] not needed")

    if not runtime_min:
        tmdb_is_ep = bool(is_series and tmdb_details.get("episode_run_time"))
        tmdb_label = "TMDb episode runtime" if tmdb_is_ep else "TMDb"
        mins = _sane(_to_minutes(tmdb_details.get("episode_run_time") if tmdb_is_ep else tmdb_details.get("runtime")), tmdb_label)
        if mins:
            runtime_min, runtime_src = mins, tmdb_label
            _rlog(3, tmdb_label, True, f"{mins} min")
        else:
            _rlog(3, "TMDb", False, "TMDb returned no usable runtime" if tmdb_details else "no validated TMDb match")
    else:
        logger.info("[RUNTIME] Step 3/4 TMDb: [SKIPPED] not needed")

    if not runtime_min:
        if is_series:
            # a combined/batch file can be 4-8 hours -> never used. The post shows the average of SINGLE episodes instead.
            runtime_src = "calculated average of uploaded single episodes"
            _rlog(4, "Average of uploaded single episodes", True,
                  "no metadata runtime -> will be calculated from single-episode files when the post is built (combined/batch files are ignored)")
        else:
            mins = _to_minutes(file_runtime)
            if mins:
                runtime_min, runtime_src = mins, "Telegram file duration"
                _rlog(4, "Telegram file duration", True, f"{mins} min")
            else:
                _rlog(4, "Telegram file duration", False, "Telegram gave no duration for this file")
    else:
        logger.info("[RUNTIME] Step 4/4 Telegram file / episode average: [SKIPPED] not needed")

    runtime = str(runtime_min) if runtime_min else "N/A"
    logger.info(f"[RUNTIME] Final duration: {runtime + ' minutes' if runtime_min else 'N/A (calculated later for series)' if is_series else 'N/A'} (source: {runtime_src})")

    ott_src_str = " + ".join(ott_trace) if ott_trace else "none"
    logger.info(f"[FINAL] Title: '{title}' | Poster Source: {poster_src} | Genre Source: {genre_src} | "
                f"Rating Source: {rating_src} | Runtime Source: {runtime_src} ({runtime} min) | OTT Source(s): {ott_src_str}")
    logger.info(f"[FINAL] Values: genres={', '.join(genres_list) or 'N/A'} | rating={rating or 'N/A'} | "
                f"IMDb URL source={url_src} | OTT={ott_platform}")

    return {
        "found": bool(imdb_details or tmdb_details or genres_list or rating),
        "poster_url": poster_url, "is_backdrop": is_backdrop,
        "genres": ", ".join(genres_list) if genres_list else "N/A",
        "rating": rating, "runtime": runtime, "runtime_src": runtime_src, "imdb_url": imdb_url,
        "year": year or imdb_details.get("year") or _result_year(tmdb_details),
        "ott_platform": ott_platform,
    }


async def is_admin_user(user_id):
    if not user_id: return False
    admins_set = {int(a) if str(a).lstrip('-').isdigit() else str(a) for a in ADMINS}
    return (user_id in admins_set) or (str(user_id) in admins_set)

@Client.on_message(filters.command(["setdomain", "set_domain", "changedomain"]))
async def set_domain_handler(bot, message):
    if not await is_admin_user(message.from_user.id if message.from_user else None): return
    if len(message.command) < 2:
        return await message.reply_text(f"🌐 **Current HDHub4u URL:** <code>{await get_hdhub_base_url()}</code>\n\n💡 **Usage:** <code>/setdomain https://new1.hdhub4u.free</code>  (aliases: /set_domain, /changedomain)")
    raw_url = message.command[1].strip().split("?")[0].rstrip("/")
    if not raw_url.startswith("http"): raw_url = f"https://{raw_url}"
    try:
        await db.db.settings.update_one({"_id": "hdhub_base_url"}, {"$set": {"url": raw_url}}, upsert=True)
        await message.reply_text(f"✅ **HDHub4u base URL successfully updated to:**\n<code>{raw_url}</code>")
    except Exception as e:
        await message.reply_text(f"❌ Failed to update domain: {e}")

@Client.on_message(filters.chat(CHANNELS) & MEDIA_FILTER)
async def media_handler(bot, message):
    media = next((getattr(message, ft) for ft in ("document", "video", "audio") if getattr(message, ft, None)), None)
    if not media: return
    duration_secs = getattr(media, "duration", None) or (message.video.duration if message.video else (message.audio.duration if message.audio else None))
    file_runtime_mins = round(duration_secs / 60) if duration_secs else None

    media.file_type = next(ft for ft in ("document", "video", "audio") if getattr(message, ft, None))
    media.caption = message.caption or ""

    try:
        success, info = await save_file(media)
    except Exception:
        logger.exception("save_file crashed - not posting a file that may not be stored")
        return
    if not success:
        # already in the primary DB (duplicate) -> still run the channel pipeline so posts / qualities / merges keep working
        logger.info(f"[MEDIA] '{getattr(media, 'file_name', None)}' is already in the primary DB (save_file info={info}) -> continuing to the channel post")

    try:
        if await db.movie_update_status(bot.me.id):
            await process_and_send_update(bot, media.file_name or message.caption or "Unknown", media.caption, file_runtime_mins)
    except Exception:
        logger.exception("Error processing media")

META_VERSION = 2
pipeline_lock = asyncio.Lock()          # ONE file at a time -> no race, no parallel Gemini calls
_refresh_tried: set = set()


async def find_cached_doc(movies, keys: list, ids: list, year, is_series: bool, titles: list = None):
    keys = [k for k in dict.fromkeys(keys) if k]
    ids = [i for i in dict.fromkeys(ids) if i]
    titles = [t for t in dict.fromkeys(titles or []) if t]
    clauses = [{"clean_title": {"$in": keys}}, {"alias_keys": {"$in": keys}}, {"_id": {"$in": ids}}]
    if titles:
        clauses.append({"title": {"$in": titles}})
    docs = await movies.find({"$or": clauses}).to_list(length=20)
    docs = [d for d in docs if is_series or d["_id"] in ids or _years_compatible(d.get("year"), year)]
    if not docs:
        return None
    docs.sort(key=lambda d: (d["_id"] not in ids, not d.get("message_id")))
    return docs[0]


def quality_signature(q) -> frozenset:
    """Resolution set of a file ('1080p, HEVC' -> {1080p}); source-only releases use their source tags."""
    if not q or q == "N/A":
        return frozenset()
    res = {("2160p" if r.lower() == "4k" else r.lower()) for r in RESOLUTION_PATTERN.findall(q)}
    if res:
        return frozenset(res)
    return frozenset(p.strip().lower() for p in format_movie_qualities([q]).split(",") if p.strip() and p.strip() != "N/A")


def is_duplicate_resolution(doc: dict, file_data: dict) -> bool:
    """True if this resolution (for the same season/episode) is already listed on the post."""
    sig = quality_signature(file_data.get("quality"))
    if not sig:
        return False
    seen = set()
    for f in (doc.get("files") or []):
        if f.get("season") == file_data.get("season") and f.get("episode") == file_data.get("episode"):
            seen |= quality_signature(f.get("quality"))
    return sig <= seen


def _doc_has_ott(doc: dict) -> bool:
    vals = [doc.get("ott_platform")] + [f.get("ott_platform") for f in (doc.get("files") or [])]
    return any(v and str(v).strip().upper() not in ("N/A", "NONE", "") for v in vals)


def _genres_dirty(doc: dict) -> bool:
    """True when the stored genre string contains anything that is not a real genre (old HDHub leak, Talk-Show...)."""
    raw = doc.get("genres")
    if not raw or str(raw).strip().upper() in ("N/A", "NONE", "-"):
        return False
    return ", ".join(clean_genres(raw)) != str(raw).strip()


def needs_metadata_refresh(doc: dict) -> bool:
    if doc["_id"] in _refresh_tried:
        return False
    if _genres_dirty(doc):
        return True
    if doc.get("meta_version", 0) >= META_VERSION:
        return False
    return str(doc.get("ott_platform") or "N/A").strip().upper() in ("N/A", "", "NONE")


async def process_and_send_update(bot, filename, caption, file_runtime_mins=None):
    try:
        async with pipeline_lock:
            await _process_file(bot, filename, caption, file_runtime_mins)
    except Exception as e:
        logger.exception(f"Processing failed in process_and_send_update: {e}")


async def _append_file(bot, movies, doc, file_data, alias_key, ctx):
    base_name = doc["_id"]
    if ctx["filename"] != "Unknown" and any(f.get("filename") == ctx["filename"] for f in (doc.get("files") or [])):
        return   # (nameless files are all called "Unknown" - they are different files, never duplicates)
    update, set_fields = {}, {}

    duplicate = is_duplicate_resolution(doc, file_data)
    if duplicate:
        STATS["files_skipped_dup_resolution"] += 1
        logger.info(f"[SKIP] '{ctx['filename']}': {file_data.get('quality')} already on post '{base_name}'")
    else:
        update["$push"] = {"files": file_data}

    # dynamic OTT update: post has no OTT yet, this file carries a tag (AMZN/NF/...) -> add it & edit caption
    new_ott = file_data.get("ott_platform")
    if new_ott and new_ott != "N/A" and not _doc_has_ott(doc):
        set_fields["ott_platform"] = new_ott
        logger.info(f"[OTT] '{base_name}' had no OTT, adding '{new_ott}' from file tag")

    is_series_doc = doc.get("tag") == "#SERIES" or file_data.get("tag") == "#SERIES"
    if (not is_series_doc and (not doc.get("runtime") or str(doc.get("runtime")) == "N/A")
            and ctx["runtime"] != "N/A"):
        set_fields["runtime"] = ctx["runtime"]
    if needs_metadata_refresh(doc):
        set_fields.update(await _refresh_fields(doc, ctx))
    if set_fields:
        update["$set"] = set_fields
    if alias_key and alias_key != doc.get("clean_title") and alias_key not in (doc.get("alias_keys") or []):
        update["$addToSet"] = {"alias_keys": alias_key}
    if not update:
        return
    await movies.update_one({"_id": base_name}, update)
    if not duplicate:
        STATS["files_merged"] += 1
        logger.info(f"[MERGED] '{ctx['filename']}' -> existing post '{base_name}' (no Gemini call)")
    if "$push" in update or "$set" in update:
        schedule_update(bot, base_name)        # debounced caption edit


async def _refresh_fields(doc, ctx) -> dict:
    """Self-heal old docs (Talk-Show genre, OTT N/A) once, never overwriting good data with N/A."""
    _refresh_tried.add(doc["_id"])
    identity = await resolve_identity(ctx["filename_clean"], ctx["caption"], ctx["duration"],
                                      ctx["title_local"], ctx["year_local"] or doc.get("year"), ctx["season"])
    meta = await collect_metadata(identity, ctx["filename"], ctx["caption"], ctx["runtime"])
    out = {}
    if meta["genres"] != "N/A": out["genres"] = meta["genres"]
    if meta["found"]:
        if meta["rating"]: out["rating"] = meta["rating"]
        if meta["imdb_url"]: out["imdb_url"] = meta["imdb_url"]
        if meta["year"]: out["year"] = meta["year"]
    if meta["ott_platform"] != "N/A": out["ott_platform"] = meta["ott_platform"]
    if identity["is_series"] and meta["runtime"] != "N/A":      # series: metadata episode runtime heals old/file-based values
        out["runtime"], out["runtime_src"] = meta["runtime"], meta["runtime_src"]
    if "genres" not in out and _genres_dirty(doc):          # nothing better found -> at least remove the junk
        out["genres"] = ", ".join(clean_genres(doc.get("genres"))) or "N/A"
    if identity["ai_ok"]:
        out["title"] = identity["title"]
        out["display_title"] = identity["title"]
        out["meta_version"] = META_VERSION
    logger.info(f"[REFRESH] {doc['_id']}: {list(out)}")
    return out


async def _process_file(bot, filename, caption, file_runtime_mins=None):
    if not hasattr(db, "movie_updates"):
        db.movie_updates = db.db.movie_updates
    movies = db.movie_updates
    await ensure_indexes()
    await load_runtime_config()
    STATS["files_received"] += 1

    filename, caption = (filename or "Unknown"), (caption or "")
    filename_dec = decode_title_escapes(filename)       # 'OK_Jaanu__40_2017__41_' -> real brackets, for every regex below
    filename_clean = clean_mentions_links(filename)
    unified = f"{clean_mentions_links(caption).lower()} {filename_clean.lower()}".strip()

    # --- everything below is LOCAL (regex only); episodes never touch Gemini ---
    season, episode = extract_season_episode(filename_dec)
    if season is None and caption:                       # generic filename ("video.mkv") but the caption has S01 / Season 1
        season, episode = extract_season_episode(caption)
    title_local, year_local = parse_local_title(filename)
    if not title_local:
        title_local = normalize(filename_clean) or "Unknown"
    local_is_series = season is not None
    local_name = build_base_name(title_local, year_local, local_is_series, season)
    local_key = canon_key(title_local, season)

    quality = next((q for q in (get_qualities(caption), get_qualities(filename_dec)) if q and q != "N/A"), "N/A")
    scan_text = _without_title(_without_title(unified, raw_title_head(filename)), title_local)   # tags only - never the title itself
    lang_keys = {k for k in CAPTION_LANGUAGES if re.search(rf"\b{re.escape(k)}\b", scan_text)}
    language = ", ".join(sorted({CAPTION_LANGUAGES[k] for k in lang_keys})) if lang_keys else "N/A"
    runtime = f"{file_runtime_mins}" if file_runtime_mins else "N/A"

    ctx = {}

    def ctx_season():
        return ctx.get("season", season)

    def make_file_data(is_series):
        return {
            "filename": filename, "processed": normalize(filename_clean), "quality": quality,
            "language": language, "ott_platform": extract_ott_platform(scan_text),
            "timestamp": datetime.now(), "tag": "#SERIES" if is_series else "#MOVIE",
            "season": ctx_season(), "episode": episode, "runtime": runtime,
        }

    ctx.update({"filename": filename, "filename_clean": filename_clean, "caption": caption, "duration": file_runtime_mins,
                "title_local": title_local, "year_local": year_local, "season": season, "runtime": runtime})
    logger.info(f"[FILE] Received '{filename}' -> local parse: title='{title_local}', year={year_local or 'none'}, "
                f"season={season}, episode={episode}, quality={quality}, language={language}, runtime={runtime} min")
    if season is not None and not is_single_episode(season, episode):
        logger.info(f"[SERIES] '{filename}' is a combined/batch file for Season {season} -> it joins the ONE Season {season} post; "
                    f"its long duration is ignored for the series runtime")

    # 1) DB FIRST: if the post already exists, skip Gemini + scrapers entirely
    cached = await find_cached_doc(movies, [local_key], [local_name], year_local, local_is_series)
    logger.info(f"[DB] Step 1 - looked up '{local_key}' in the database: "
                + (f"FOUND existing post '{cached['_id']}' -> skipping Gemini and scrapers" if cached else "not found -> will ask Gemini for the official title"))
    if cached:
        await _append_file(bot, movies, cached, make_file_data(cached.get("tag") == "#SERIES" or local_is_series), local_key, ctx)
        return

    # 2) New title -> ask Gemini (throttled, one at a time)
    identity = await resolve_identity(filename_clean, caption, file_runtime_mins, title_local, year_local, season)
    is_series = identity["is_series"]
    base_name = build_base_name(identity["title"], identity["year"], is_series, season if is_series else None)
    final_key = canon_key(identity["title"], season if is_series else None)
    logger.info(f"[IDENTIFIED] Official title='{identity['title']}' year={identity['year'] or 'unknown'} "
                f"type={'series' if is_series else 'movie'} -> post name '{base_name}' (key '{final_key}')")

    # 3) Gemini may have corrected the title -> check DB again under the final key
    if final_key != local_key:
        cached = await find_cached_doc(movies, [final_key], [base_name], identity["year"], is_series,
                                       titles=[identity["title"]])
        logger.info(f"[DB] Step 2 - re-checked database with Gemini's clean title '{final_key}': "
                    + (f"FOUND '{cached['_id']}' -> merging, no new post" if cached else "still not found -> creating a new post"))
        if cached:
            await _append_file(bot, movies, cached, make_file_data(is_series), local_key, ctx)
            return

    # 4) Truly new -> metadata + create the ONE master post
    meta = await collect_metadata(identity, filename, caption, runtime)
    file_data = make_file_data(is_series)
    file_data["ott_platform"] = meta["ott_platform"]
    new_doc = {
        "_id": base_name, "clean_title": final_key,
        "alias_keys": [local_key] if local_key != final_key else [],
        "title": identity["title"], "display_title": identity["title"],
        "files": [file_data],
        "poster_url": meta["poster_url"], "is_backdrop": meta["is_backdrop"], "is_photo": False,
        "genres": meta["genres"], "rating": meta["rating"] or "N/A", "runtime": meta["runtime"],
        "runtime_src": meta["runtime_src"],
        "imdb_url": meta["imdb_url"], "year": meta["year"],
        "tag": "#SERIES" if is_series else "#MOVIE",
        "ott_platform": meta["ott_platform"], "message_id": None,
        "meta_version": META_VERSION if identity["ai_ok"] else 0,
    }
    try:
        await movies.insert_one(new_doc)
    except DuplicateKeyError:
        await movies.update_one({"_id": base_name}, {"$push": {"files": file_data}, "$addToSet": {"alias_keys": local_key}})
        schedule_update(bot, base_name)
        return
    STATS["posts_created"] += 1
    await send_movie_update(bot, base_name)


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
                    # NEVER call update_movie_message here: the caller may already hold edit_locks[base_name]
                    # (asyncio.Lock is not re-entrant -> deadlock). A debounced edit is race-free instead.
                    schedule_update(bot, base_name, delay=1)
                    return None

                buttons = _build_buttons(movie_doc, base_name)
                poster_url = movie_doc.get("poster_url")
                photo_url = poster_url if (poster_url and str(poster_url).startswith("http")) else None     # sent exactly as stored
                is_photo, msg = False, None

                if photo_url:
                    try:
                        msg = await bot.send_photo(chat_id=MOVIE_UPDATE_CHANNEL, photo=photo_url, caption=build_post_text(movie_doc, base_name, True),
                                                   reply_markup=buttons, parse_mode=enums.ParseMode.HTML)
                        is_photo = True
                    except FloodWait:
                        raise
                    except Exception as e:
                        logger.warning(f"Photo send failed for {base_name} ({e}); sending text post instead")
                if msg is None:
                    # text-only: NO link preview (the IMDb link in the text would attach a big yellow IMDb banner)
                    msg = await bot.send_message(chat_id=MOVIE_UPDATE_CHANNEL, text=build_post_text(movie_doc, base_name, False), reply_markup=buttons,
                                                 parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)

                await db.movie_updates.update_one({"_id": base_name}, {"$set": {"message_id": msg.id, "is_photo": is_photo}})
                logger.info(f"[POST] Published '{base_name}' as a {'photo' if is_photo else 'TEXT-ONLY'} post (message id {msg.id})")
                return msg
            except FloodWait as e:
                await asyncio.sleep(e.value + 2)
            except Exception as e:
                logger.error(f"send_movie_update failed for {base_name}: {e}")
                break
        return None
    finally:
        sending_updates.discard(base_name)


def _safe_bot_username() -> str:
    """temp.U_NAME can be None in Multi-Client mode and may contain '@' -> both produce BUTTON_URL_INVALID."""
    uname = (getattr(temp, "U_NAME", None) or BOT_USERNAME_FALLBACK)
    uname = str(uname).strip().lstrip("@").strip()      # strip spaces BEFORE and after removing "@"
    if not uname or uname.lower() == "none":
        uname = BOT_USERNAME_FALLBACK
    return uname


def _build_buttons(movie_doc, base_name):
    btn_style = enums.ButtonStyle.SUCCESS if movie_doc.get("tag", "#MOVIE") == "#SERIES" else enums.ButtonStyle.PRIMARY
    # Auto-Filter must search the bare title: "Musafir Cafe Season 1" -> "Musafir Cafe"
    btn_q = SERIES_SUFFIX_RE.sub("", str(base_name or "")).strip(" :-–—,") or str(base_name or "").strip()
    # Telegram deep-link payload: only A-Z a-z 0-9 _ - and max 64 chars ("getfile-" already uses 8)
    clean_query = re.sub(r"[^A-Za-z0-9_\-]+", "-", btn_q.replace(" ", "-"))
    clean_query = re.sub(r"(?:-+(?:Season-?\d+|S\d{1,2}))+(?=-|$)", "", clean_query, flags=re.IGNORECASE)   # stray -S01 / -S1 / -Season-1
    clean_query = re.sub(r"-{2,}", "-", clean_query).strip("-")[:56].rstrip("-") or "movie"
    uname = _safe_bot_username()
    url = f"https://t.me/{uname}?start=getfile-{clean_query}"
    return InlineKeyboardMarkup([[InlineKeyboardButton("ɢᴇᴛ ғɪʟᴇs", url=url, style=btn_style)]])


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
                        await bot.edit_message_caption(chat_id=MOVIE_UPDATE_CHANNEL, message_id=message_id, caption=text,
                                                       reply_markup=buttons, parse_mode=enums.ParseMode.HTML)
                    else:
                        await bot.edit_message_text(chat_id=MOVIE_UPDATE_CHANNEL, message_id=message_id, text=text,
                                                    reply_markup=buttons, parse_mode=enums.ParseMode.HTML,
                                                    disable_web_page_preview=True)
                    return
                except MessageNotModified:      # identical content -> nothing to do, stay silent
                    return
                except FloodWait as e:
                    logger.warning(f"FloodWait {e.value}s while editing {base_name}")
                    await asyncio.sleep(e.value + 1)
                except MessageIdInvalid:        # post was deleted in the channel -> recreate once
                    await db.movie_updates.update_one({"_id": base_name}, {"$set": {"message_id": None}})
                    await send_movie_update(bot, base_name)
                    return
        except Exception as e:
            logger.error(f"Failed to update movie message for {base_name}: {e}")


def _season_int(v) -> Optional[int]:
    """Seasons are stored as int, but legacy / hand-edited docs may hold '1', '01' or junk."""
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def _expand_episode_ids(episodes) -> list:
    """['1', '3-5', 'Bonus 2', 'x', '8-1', '1-99999'] -> [1, 3, 4, 5, 1..8]. Anything that is not a plain number or a short
    ascending/descending range ('Bonus 2', 'Complete', garbage, absurd ranges) is ignored instead of crashing or exploding."""
    nums = set()
    for ep in episodes:
        m = re.fullmatch(r"\s*(\d{1,4})\s*(?:-\s*(\d{1,4}))?\s*", str(ep))
        if not m:
            continue
        a = int(m.group(1))
        b = int(m.group(2)) if m.group(2) else a
        if b < a:
            a, b = b, a
        if b - a > 300:
            continue
        nums.update(range(a, b + 1))
    return sorted(nums)


def _series_runtime(movie_doc, base_name) -> str:
    """Series: 1) TMDb episode runtime 2) IMDb 3) OMDb (all stored in the doc as metadata)
    4) average of uploaded SINGLE-episode files. Combined / batch / range / bonus files are never used."""
    cap = SERIES_EP_MAX_MINUTES
    per_episode = defaultdict(list)
    for f in (movie_doc.get("files") or []):
        rt = f.get("runtime")
        if is_single_episode(f.get("season"), f.get("episode")) and rt and str(rt).isdigit() and 0 < int(rt) <= cap:
            per_episode[(f.get("season"), str(f.get("episode")))].append(int(rt))
    ep_means = [sum(v) / len(v) for v in per_episode.values()]

    meta_min = _to_minutes(movie_doc.get("runtime"))
    meta_ok = bool(meta_min and 0 < meta_min <= cap)
    if meta_ok and movie_doc.get("runtime_src") in ("TMDb episode runtime", "TMDb", "IMDb episode runtime", "IMDb", "OMDb"):
        return str(meta_min)
    if ep_means:                                    # no trusted metadata -> calculated average
        avg = round(sum(ep_means) / len(ep_means))
        logger.info(f"[RUNTIME] '{base_name}': no metadata runtime -> calculated average of {len(ep_means)} uploaded single "
                    f"episode(s) = {avg} minutes (combined/batch files ignored)")
        return str(avg)
    if meta_ok:                                     # older documents (no runtime_src) that only have a sane stored value
        return str(meta_min)
    return "N/A"                                    # never show a misleading multi-hour value


def generate_movie_message(movie_doc, base_name, compact: int = 0):
    all_raw_qualities, all_languages, all_ott_platforms = [], set(), set()
    episodes_by_season = defaultdict(set)
    valid_file_runtimes = []

    for file in (movie_doc.get("files") or []):
        if file.get("quality") and file.get("quality") != "N/A": all_raw_qualities.append(file["quality"])
        if file.get("language") and file.get("language") != "N/A":
            for lang in file["language"].split(","):
                all_languages.add(CAPTION_LANGUAGES.get(lang.strip().lower(), lang.strip().title()))
        if file.get("ott_platform") and file.get("ott_platform") != "N/A":
            for plat in file["ott_platform"].split("|"):
                all_ott_platforms.add(OTT_PLATFORMS.get(plat.strip().lower(), plat.strip()))
        _sn = _season_int(file.get("season"))
        if _sn is not None and file.get("episode") is not None:
            episodes_by_season[_sn].add(str(file["episode"]))
        if file.get("runtime") and str(file["runtime"]).isdigit() and int(file["runtime"]) > 0:
            valid_file_runtimes.append(int(file["runtime"]))

    doc_ott = movie_doc.get("ott_platform")
    if doc_ott and doc_ott != "N/A":
        for plat in str(doc_ott).split("|"):
            if plat.strip():
                all_ott_platforms.add(plat.strip())

    primary_tag = movie_doc.get("tag", "#MOVIE")
    is_series = (primary_tag == "#SERIES")

    epi_block = ""
    if episodes_by_season and compact < 1:          # compact>=1 (caption too long): drop the episode list first
        episode_lines = []
        for season, episodes in sorted(episodes_by_season.items()):
            sorted_nums = _expand_episode_ids(episodes)
            if sorted_nums:
                collapsed = []
                start = end = sorted_nums[0]
                for num in sorted_nums[1:]:
                    if num == end + 1: end = num
                    else: collapsed.append(str(start) if start == end else f"{start}-{end}"); start = end = num
                collapsed.append(str(start) if start == end else f"{start}-{end}")
                episode_lines.append(f"S{int(season)}: {', '.join(collapsed)}")
        if episode_lines: epi_block = f"\n📺 ᴇᴘɪsᴏᴅᴇs : <b>{chr(10).join(episode_lines)}</b>"

    genre_items = clean_genres(movie_doc.get("genres"))
    if compact >= 2: genre_items = genre_items[:3]
    genres = ", ".join(genre_items) or "N/A"

    quality_str = format_movie_qualities(all_raw_qualities)
    lang_items, ott_items = sorted(all_languages), sorted(all_ott_platforms)
    if compact >= 2: lang_items, ott_items = lang_items[:3], ott_items[:3]
    language_str = ", ".join(lang_items) if lang_items else "Hindi"
    ott_str = " | ".join(ott_items) if ott_items else "N/A"

    raw_rating = str(movie_doc.get("rating") or "N/A").strip()
    clean_rating = _valid_rating(raw_rating) or "N/A"

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

    esc = lambda v: html.escape(str(v if v is not None else ""), quote=False)     # & < >  -> entities
    # no IMDb id known -> link to an IMDb SEARCH for the title (never the bare home page)
    imdb_link = movie_doc.get("imdb_url") or f"https://www.imdb.com/find/?q={quote_plus(str(display_title))}"
    text = script.MOVIE_UPDATE_NOTIFY_TXT.format(
        imdb_url=html.escape(str(imdb_link), quote=True),
        filename=esc(filename_display),
        tag=esc(primary_tag),
        genres=esc(genres),
        ott=esc(ott_str),
        runtime=runtime,
        quality=esc(quality_str),
        language=esc(language_str),
        episodes=epi_block,
        rating=clean_rating,
        search_link=html.escape(str(getattr(temp, "B_LINK", None) or "https://t.me"), quote=True)
    ).strip()
    # the template appends "/10" to the rating: "N/A/10" is a glitch -> show a plain "N/A"
    return re.sub(r"N/A\s*/\s*10", "N/A", text)


def visible_len(text: str) -> int:
    """Telegram limits captions by VISIBLE characters (tags and entities do not count)."""
    return len(html.unescape(re.sub(r"<[^>]+>", "", text or "")))


def build_post_text(movie_doc, base_name, photo: bool) -> str:
    """Photo captions are capped at 1024 characters, text posts at 4096: shrink the optional parts step by step."""
    limit = 1024 if photo else 4096
    text = generate_movie_message(movie_doc, base_name)
    for level in (1, 2):
        if visible_len(text) <= limit:
            break
        text = generate_movie_message(movie_doc, base_name, compact=level)
    if visible_len(text) > limit:
        logger.warning(f"[POST] caption for '{base_name}' is still {visible_len(text)} chars after shrinking (limit {limit})")
    return text


# =====================================================================
#  ADMIN TOOLS  (/fixpost  /mergeposts  /mergedupes  /chstats  /chconfig)
# =====================================================================
_STARTED_AT = time.time()


async def _admin_ok(message) -> bool:
    return await is_admin_user(message.from_user.id if getattr(message, "from_user", None) else None)


def _cmd_args(message) -> str:
    parts = (getattr(message, "text", "") or "").split(None, 1)
    return parts[1].strip() if len(parts) > 1 else ""


async def _reply(message, text: str):
    await message.reply_text(text, parse_mode=enums.ParseMode.HTML, disable_web_page_preview=True)


def _post_key(doc: dict) -> Tuple[str, Optional[str]]:
    """Canonical (key, year) of a stored post, derived from its _id so legacy/dirty ids ('Sardar 2 2026 ESu') group correctly."""
    pid = str(doc.get("_id") or "")
    title, year = parse_local_title(pid)
    season = extract_season_episode(pid)[0]
    is_series = doc.get("tag") == "#SERIES" or season is not None
    return canon_key(title or pid, season if is_series else None), (year or (str(doc.get("year")) if doc.get("year") else None))


async def find_posts(query: str, limit: int = 6) -> list:
    q = decode_title_escapes(query).strip()
    if not q:
        return []
    movies = db.movie_updates
    exact = await movies.find_one({"_id": q})
    if exact:
        return [exact]
    title, _y = parse_local_title(q)
    season = extract_season_episode(q)[0]
    key = canon_key(title or q, season)
    return await movies.find({"$or": [{"clean_title": {"$in": [key]}}, {"alias_keys": {"$in": [key]}}]}).to_list(length=limit)


async def refresh_post(bot, doc: dict) -> list:
    """Re-run identity + metadata for ONE post, write the fresh values and edit the channel message. Returns change descriptions."""
    base_name = doc["_id"]
    is_series = doc.get("tag") == "#SERIES"
    files = doc.get("files") or []
    first_name = (files[0].get("filename") if files else None) or base_name
    season = next((_season_int(f.get("season")) for f in files if _season_int(f.get("season")) is not None), None)
    title_local = doc.get("title") or parse_local_title(base_name)[0]
    _meta_cache.clear()
    _gemini_cache.clear()
    _refresh_tried.discard(base_name)
    identity = await resolve_identity(clean_mentions_links(first_name), "", None, title_local, doc.get("year"), season if is_series else None)
    identity["is_series"] = is_series                      # the stored post type is authoritative
    meta = await collect_metadata(identity, first_name, "", "N/A")
    new = {}
    if meta["genres"] != "N/A": new["genres"] = meta["genres"]
    elif _genres_dirty(doc): new["genres"] = ", ".join(clean_genres(doc.get("genres"))) or "N/A"
    if meta["rating"]: new["rating"] = meta["rating"]
    if meta["imdb_url"]: new["imdb_url"] = meta["imdb_url"]
    if meta["year"]: new["year"] = meta["year"]
    if meta["ott_platform"] != "N/A": new["ott_platform"] = meta["ott_platform"]
    if meta["runtime"] != "N/A" and (is_series or meta["runtime_src"] != "Telegram file duration"):
        new["runtime"], new["runtime_src"] = meta["runtime"], meta["runtime_src"]
    if meta["poster_url"]: new["poster_url"], new["is_backdrop"] = meta["poster_url"], meta["is_backdrop"]
    if identity["ai_ok"]:
        new["title"] = new["display_title"] = identity["title"]
        new["meta_version"] = META_VERSION
    changes = [f"{k}: {str(doc.get(k))[:40]} -> {str(v)[:40]}" for k, v in new.items()
               if str(doc.get(k)) != str(v) and k not in ("meta_version", "display_title", "runtime_src")]
    if new:
        await db.movie_updates.update_one({"_id": base_name}, {"$set": new})
    if doc.get("message_id"):
        await update_movie_message(bot, base_name)
    return changes


@Client.on_message(filters.command(["fixpost", "fix_post"]))
async def fixpost_handler(bot, message):
    if not await _admin_ok(message): return
    q = _cmd_args(message)
    if not q:
        return await _reply(message, "🛠 <b>Usage:</b> <code>/fixpost Sardar 2 2026</code>\nRe-scrapes metadata for ONE post and edits it.")
    docs = await find_posts(q)
    if not docs:
        return await _reply(message, f"❌ No post found for <code>{html.escape(q)}</code>")
    if len(docs) > 1:
        return await _reply(message, "⚠️ Several posts match - send the exact id:\n" + "\n".join(f"• <code>{html.escape(str(d['_id']))}</code>" for d in docs))
    try:
        changes = await refresh_post(bot, docs[0])
    except Exception as e:
        logger.exception("fixpost failed")
        return await _reply(message, f"❌ Fix failed: <code>{html.escape(str(e))}</code>")
    body = "\n".join(f"• {html.escape(c)}" for c in changes) or "• nothing changed (already up to date)"
    await _reply(message, f"✅ <b>{html.escape(str(docs[0]['_id']))}</b> refreshed\n{body}\n\n<i>A photo post keeps its original picture (Telegram cannot swap it on edit); text and caption are updated.</i>")


async def merge_posts(bot, keep_id: str, drop_id: str) -> str:
    """Merge post `drop_id` INTO `keep_id`: files (no duplicate resolutions), aliases, OTT, missing metadata. Deletes the dropped doc and its message."""
    movies = db.movie_updates
    if keep_id == drop_id:
        return "❌ Both ids are the same post."
    keep, drop = await movies.find_one({"_id": keep_id}), await movies.find_one({"_id": drop_id})
    if not keep or not drop:
        return f"❌ Post not found: <code>{html.escape(keep_id if not keep else drop_id)}</code>"
    files = list(keep.get("files") or [])
    seasons = Counter(_season_int(f.get("season")) for f in files if _season_int(f.get("season")) is not None)
    target_season = seasons.most_common(1)[0][0] if seasons else None
    keep_is_series = keep.get("tag") == "#SERIES"
    added = skipped = 0
    for f in (drop.get("files") or []):
        f = dict(f)
        if keep_is_series and _season_int(f.get("season")) is None and target_season is not None:
            f["season"] = target_season                     # the stray post held a combined / unmarked file of this season
        same_name = f.get("filename") not in (None, "", "Unknown") and any(x.get("filename") == f.get("filename") for x in files)
        if same_name or is_duplicate_resolution({"files": files}, f):
            skipped += 1
            continue
        files.append(f)
        added += 1
    aliases = set((keep.get("alias_keys") or [])) | set((drop.get("alias_keys") or [])) | {drop.get("clean_title"), _post_key(drop)[0]}
    aliases.discard(None)
    aliases.discard(keep.get("clean_title"))
    platforms = []
    for doc in (keep, drop):
        for p in str(doc.get("ott_platform") or "").split("|"):
            p = p.strip()
            if p and p.upper() != "N/A" and p not in platforms: platforms.append(p)
    upd = {"files": files, "alias_keys": sorted(aliases)}
    if platforms: upd["ott_platform"] = " | ".join(platforms)
    for fld in ("genres", "rating", "imdb_url", "poster_url"):         # fill what the kept post lacks
        if (not keep.get(fld) or str(keep.get(fld)).upper() == "N/A") and drop.get(fld) and str(drop.get(fld)).upper() != "N/A":
            upd[fld] = drop[fld]
    await movies.update_one({"_id": keep_id}, {"$set": upd})
    await movies.delete_one({"_id": drop_id})
    if drop.get("message_id"):
        try:
            await bot.delete_messages(MOVIE_UPDATE_CHANNEL, drop["message_id"])
        except Exception as e:
            logger.warning(f"[ADMIN] could not delete message {drop['message_id']} of '{drop_id}': {e}")
    if keep.get("message_id"):
        await update_movie_message(bot, keep_id)
    logger.info(f"[ADMIN] merged '{drop_id}' into '{keep_id}' (+{added} files, {skipped} duplicates skipped)")
    return f"✅ Merged <code>{html.escape(drop_id)}</code> → <code>{html.escape(keep_id)}</code> (+{added} files, {skipped} duplicate resolutions skipped)"


@Client.on_message(filters.command(["mergeposts", "merge_posts"]))
async def mergeposts_handler(bot, message):
    if not await _admin_ok(message): return
    parts = [p.strip() for p in re.split(r"\s*(?:\||->|=>)\s*", _cmd_args(message)) if p.strip()]
    if len(parts) != 2:
        return await _reply(message, "🔀 <b>Usage:</b> <code>/mergeposts KEEP_ID | DROP_ID</code>\nExample: <code>/mergeposts Musafir Cafe Season 1 | Musafir Cafe</code>\n"
                                     "DROP_ID's files move into KEEP_ID and its channel message is deleted.")
    await _reply(message, await merge_posts(bot, parts[0], parts[1]))


async def find_duplicate_groups() -> Tuple[list, list]:
    """(groups of posts that are the same title, 'stray' movie posts that look like a series season posted without its season)."""
    docs = await db.movie_updates.find({}).to_list(length=None)
    buckets = defaultdict(list)
    for d in docs:
        key, year = _post_key(d)
        buckets[key].append((d, year))
    groups = []
    for key, items in buckets.items():
        clusters = []
        for d, y in items:
            for cl in clusters:
                if d.get("tag") == "#SERIES" or all(_years_compatible(y, y2) for _, y2 in cl):
                    cl.append((d, y))
                    break
            else:
                clusters.append([(d, y)])
        groups += [[d for d, _ in cl] for cl in clusters if len(cl) > 1]
    series_bases = {canon_key(parse_local_title(str(d["_id"]))[0]) for d in docs if d.get("tag") == "#SERIES"}
    strays = [d for d in docs if d.get("tag") != "#SERIES" and canon_key(parse_local_title(str(d["_id"]))[0]) in series_bases]
    return groups, strays


@Client.on_message(filters.command(["mergedupes", "merge_dupes"]))
async def mergedupes_handler(bot, message):
    """One-time migration: merge legacy duplicate posts ('Sardar 2 2026' + 'Sardar 2 2026 ESu'). `dry` only reports."""
    if not await _admin_ok(message): return
    dry = "dry" in _cmd_args(message).lower()
    groups, strays = await find_duplicate_groups()
    lines = []
    for grp in groups:
        master = max(grp, key=lambda d: (bool(d.get("message_id")), len(d.get("files") or []), -len(str(d["_id"]))))
        others = [d for d in grp if d["_id"] != master["_id"]]
        lines.append(f"• keep <code>{html.escape(str(master['_id']))}</code> ← " + ", ".join(f"<code>{html.escape(str(d['_id']))}</code>" for d in others))
        if not dry:
            for d in others:
                await merge_posts(bot, master["_id"], d["_id"])
    backfilled = 0
    if not dry:                                                         # give legacy posts a clean_title so lookups find them
        for d in await db.movie_updates.find({}).to_list(length=None):
            if not d.get("clean_title"):
                await db.movie_updates.update_one({"_id": d["_id"]}, {"$set": {"clean_title": _post_key(d)[0]}})
                backfilled += 1
    head = f"🧹 <b>{'Dry run' if dry else 'Merged'}:</b> {len(groups)} duplicate group(s)" + ("" if dry else f", {backfilled} legacy post(s) got a clean_title")
    out = head + ("\n" + "\n".join(lines) if lines else "\nNo duplicates found.")
    if strays:
        out += "\n\n⚠️ <b>Possible strays</b> (movie-style post of a series; merge manually with /mergeposts):\n" + "\n".join(f"• <code>{html.escape(str(d['_id']))}</code>" for d in strays[:10])
    await _reply(message, out[:3900])


@Client.on_message(filters.command(["chstats", "channelstats"]))
async def chstats_handler(bot, message):
    if not await _admin_ok(message): return
    now = time.monotonic()
    breaker = "closed" if now >= _hdhub_breaker["open_until"] else f"OPEN ({int(_hdhub_breaker['open_until'] - now)}s left)"
    st = lambda *k: " | ".join(f"{n}: {STATS.get(n, 0)}" for n in k)
    up = int(time.time() - _STARTED_AT)
    text = ("📊 <b>Channel pipeline</b> (since start, up " + f"{up // 3600}h {up % 3600 // 60}m)\n"
            f"{st('files_received', 'posts_created', 'files_merged', 'files_skipped_dup_resolution')}\n"
            f"Gemini → {st('gemini_calls', 'gemini_cache_hits')}\n"
            f"HDHub4u → {st('hdhub_found', 'hdhub_notfound', 'hdhub_blocked', 'hdhub_skipped_breaker')} | breaker: {breaker}\n"
            f"Metadata lookups: {STATS.get('metadata_lookups', 0)} | cache entries: {len(_meta_cache)} (TTL {CACHE_TTL}s)\n"
            f"{html.escape(health_summary())}\n"
            f"Config overrides: {', '.join(sorted(RUNTIME_CONFIG)) or 'none'}")
    await _reply(message, text)


@Client.on_message(filters.command(["chconfig", "ch_config"]))
async def chconfig_handler(bot, message):
    """/chconfig  |  /chconfig set <key> <json>  |  /chconfig reset <key>   (settings live in db.settings['channel_config'])"""
    if not await _admin_ok(message): return
    args = _cmd_args(message)
    parts = args.split(None, 2)
    if not parts or parts[0].lower() == "show":
        await load_runtime_config(force=True)
        cur = json.dumps(RUNTIME_CONFIG, ensure_ascii=False) if RUNTIME_CONFIG else "{}"
        keys = "\n".join(f"• <code>{k}</code> ({t.__name__})" for k, t in CONFIG_KEYS.items())
        return await _reply(message, f"⚙️ <b>Channel config</b>\n<code>{html.escape(cur)}</code>\n\n<b>Keys</b>\n{keys}\n\n"
                                     "<code>/chconfig set ott_disabled [\"hbo\"]</code>\n<code>/chconfig set ott_extra {\"stage\": \"Stage\"}</code>\n"
                                     "<code>/chconfig set hdhub_slug_tails_year [\"-{y}-full-movie\", \"-{y}\"]</code>\n<code>/chconfig reset ott_disabled</code>")
    action = parts[0].lower()
    if action == "reset" and len(parts) >= 2 and parts[1] in CONFIG_KEYS:
        await db.db.settings.update_one({"_id": "channel_config"}, {"$unset": {parts[1]: ""}}, upsert=True)
        await load_runtime_config(force=True)
        return await _reply(message, f"✅ <code>{parts[1]}</code> reset to the built-in default")
    if action == "set" and len(parts) == 3 and parts[1] in CONFIG_KEYS:
        try:
            value = json.loads(parts[2])
        except json.JSONDecodeError as e:
            return await _reply(message, f"❌ Value must be JSON: <code>{html.escape(str(e))}</code>")
        if not isinstance(value, CONFIG_KEYS[parts[1]]) or isinstance(value, bool):
            return await _reply(message, f"❌ <code>{parts[1]}</code> needs a {CONFIG_KEYS[parts[1]].__name__}")
        await db.db.settings.update_one({"_id": "channel_config"}, {"$set": {parts[1]: value}}, upsert=True)
        await load_runtime_config(force=True)
        return await _reply(message, f"✅ <code>{parts[1]}</code> = <code>{html.escape(json.dumps(value, ensure_ascii=False))}</code> (applied)")
    await _reply(message, "❌ Usage: <code>/chconfig</code> · <code>/chconfig set &lt;key&gt; &lt;json&gt;</code> · <code>/chconfig reset &lt;key&gt;</code>")
