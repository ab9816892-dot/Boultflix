import re
import asyncio
import aiohttp
import logging
from difflib import SequenceMatcher
from datetime import datetime
from info import TMDB_API_KEY, MAX_LIST_ELM

logger = logging.getLogger(__name__)

LONG_IMDB_DESCRIPTION = False

TMDB_BEARER_TOKEN = 'eyJhbGciOiJIUzI1NiJ9.eyJhdWQiOiI2ZGU3YTIyZGU1YjE5YTFjNmUyZGU5ZWEyMzE2ZmQxMCIsIm5iZiI6MTc0NTMyMjQ2Mi41MzMsInN1YiI6IjY4MDc4MWRlYzVjODAzNWZiMDhhNjExNCIsInNjb3BlcyI6WyJhcGlfcmVhZCJdLCJ2ZXJzaW9uIjoxfQ.rMMJ2-PBIv8Y7ybxPIEpIlzTEXzuwrm9ruKxAUCAsbw'
TMDB_BASE_URL = 'https://api.themoviedb.org/3'
TMDB_IMAGE_BASE_URL = 'https://image.tmdb.org/t/p/original'
MIN_RUNTIME = 40

_session: aiohttp.ClientSession | None = None

async def get_session():
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10)
        )
    return _session

async def fetch_image(url, size=(860, 1200)):
    # Direct URL delivery avoids consuming RAM on hosting platforms like Render
    return url

async def close_session():
    global _session
    if _session and not _session.closed:
        await _session.close()

def _list_to_str_tmdb(data_list, limit=10, key=None):
    if not data_list or not isinstance(data_list, list):
        return None
    items = data_list[:limit]
    if key:
        return ", ".join(str(item.get(key, '')) for item in items if item)
    return ", ".join(str(item) for item in items if item)

def _extract_title_and_year(query: str):
    match = re.search(r'^(.*?)(?:\s+(\d{4}))?$', query.strip())
    if match:
        title, year_str = match.groups()
        year = int(year_str) if year_str and year_str.isdigit() else None
        return title.strip(), year
    return query.strip(), None

async def _tmdb_get(path, params=None, api_key=None):
    url = f"{TMDB_BASE_URL}/{path.lstrip('/')}"
    _params = params.copy() if params else {}
    _headers = {}

    if api_key:
        _params['api_key'] = api_key
    elif TMDB_BEARER_TOKEN:
        _headers = {
            'Authorization': f'Bearer {TMDB_BEARER_TOKEN}',
            'Content-Type': 'application/json;charset=utf-8'
        }

    session = await get_session()
    async with session.get(url, params=_params, headers=_headers, ssl=False) as resp:
        resp.raise_for_status()
        return await resp.json()

async def _fetch_media_details(media_type: str, media_id: int, api_key=None):
    params = {'append_to_response': 'credits,external_ids,alternative_titles,release_dates,images'}
    return await _tmdb_get(f"{media_type}/{media_id}", params=params, api_key=api_key)

async def _search_media_id(query: str, api_key=None):
    title, year = _extract_title_and_year(query)
    multi_results = []
    words = title.split()

    queries_to_try = [title]
    if len(words) > 2:
        queries_to_try.append(" ".join(words[:-1]))
        queries_to_try.append(words[0])
    elif len(words) == 2:
        queries_to_try.append(words[0])

    queries_to_try = list(dict.fromkeys(queries_to_try))[:3]

    for target_query in queries_to_try:
        if not target_query:
            continue
        params = {'query': target_query, 'language': 'en-US', 'page': 1, 'include_adult': 'false'}
        try:
            result = await _tmdb_get('search/multi', params=params, api_key=api_key)
            multi_results = result.get('results', [])
            if multi_results:
                break
        except Exception:
            continue

    def get_ratio(s1, s2):
        if not s1 or not s2:
            return 0
        return SequenceMatcher(None, s1.lower(), s2.lower()).ratio()

    scored_results = []
    for r in multi_results:
        ratio = get_ratio(r.get('title') or r.get('name'), title)
        if ratio >= 0.5:
            scored_results.append((r, ratio))

    if not scored_results:
        scored_results = [(r, get_ratio(r.get('title') or r.get('name'), title)) for r in multi_results[:10]]

    today = datetime.now().date()
    candidates_past, candidates_upcoming = [], []
    for r, ratio in scored_results:
        mtype = r.get('media_type')
        rd_str = r.get('release_date') or r.get('first_air_date')
        if not (rd_str and mtype in ['movie', 'tv']):
            continue
        try:
            rd_date = datetime.strptime(rd_str, '%Y-%m-%d').date()
        except ValueError:
            continue
        if year and abs(rd_date.year - year) > 1:
            continue
        if mtype == 'movie':
            try:
                details = await _fetch_media_details(mtype, r['id'], api_key=api_key)
                runtime = details.get('runtime')
                is_video = details.get('video', False)
                if is_video or (runtime and runtime < MIN_RUNTIME):
                    continue
            except Exception:
                continue
        candidate = {'type': mtype, 'id': r['id'], 'date': rd_date, 'score': r.get('popularity', 0), 'ratio': ratio}
        (candidates_upcoming if rd_date > today else candidates_past).append(candidate)

    candidates_past.sort(key=lambda x: (x['ratio'], x['date'], x['score']), reverse=True)
    candidates_upcoming.sort(key=lambda x: (x['ratio'], x['date'], x['score']), reverse=True)
    final = candidates_past or candidates_upcoming
    if not final:
        return None, None
    top = final[0]
    return top['type'], top['id']

def _process_images(images_data):
    posters_by_lang, backdrops_by_lang = {}, {}
    if not images_data:
        return {'posters': posters_by_lang, 'backdrops': backdrops_by_lang}

    for p in images_data.get('posters', []):
        lang = p.get('iso_639_1') or 'xx'
        path = p.get('file_path')
        if path:
            posters_by_lang.setdefault(lang, []).append(f"{TMDB_IMAGE_BASE_URL}{path}")

    for b in images_data.get('backdrops', []):
        lang = b.get('iso_639_1') or 'no_lang'
        path = b.get('file_path')
        if path:
            backdrops_by_lang.setdefault(lang, []).append(f"{TMDB_IMAGE_BASE_URL}{path}")

    return {'posters': posters_by_lang, 'backdrops': backdrops_by_lang}

async def _fetch_tmdb_data(query: str, api_key=None):
    media_type, media_id = await _search_media_id(query, api_key=api_key)
    if not media_id:
        return None

    data = await _fetch_media_details(media_type, media_id, api_key=api_key)
    if not data:
        return None

    is_movie = (media_type == 'movie')
    release_date_str = data.get('release_date') if is_movie else data.get('first_air_date')
    year = None
    if release_date_str:
        try:
            year = datetime.strptime(release_date_str, '%Y-%m-%d').year
        except ValueError:
            pass

    runtime = None
    if is_movie:
        runtime = f"{data.get('runtime', 'N/A')} min"
    else:
        ep_runtimes = data.get('episode_run_time', [])
        runtime = f"{ep_runtimes[0]} min" if ep_runtimes else "N/A"

    genres = _list_to_str_tmdb(data.get('genres', []), key='name')
    spoken_langs = _list_to_str_tmdb(data.get('spoken_languages', []), key='english_name')
    countries = _list_to_str_tmdb(data.get('production_countries', []), key='name')
    credits = data.get('credits', {})
    cast = _list_to_str_tmdb(credits.get('cast', []), limit=15, key='name')
    crew = credits.get('crew', [])

    def get_crew_by_job(job_name):
        return _list_to_str_tmdb([c for c in crew if c.get('job') == job_name], key='name')

    director = get_crew_by_job('Director')
    writer = _list_to_str_tmdb([c for c in crew if c.get('job') in ['Screenplay', 'Writer']], key='name')
    producer = get_crew_by_job('Producer')
    composer = get_crew_by_job('Original Music Composer')
    cinematographer = get_crew_by_job('Director of Photography')

    external_ids = data.get('external_ids', {})
    imdb_id = external_ids.get('imdb_id')
    raw_poster = data.get('poster_path')
    poster_url = f"{TMDB_IMAGE_BASE_URL}{raw_poster}" if raw_poster else None

    processed_images = _process_images(data.get('images', {}))
    processed_images['original_language'] = data.get('original_language')

    vote_avg = data.get('vote_average')
    rating = f"{vote_avg:.1f}" if vote_avg is not None else "N/A"

    return {
        'title': data.get('title') if is_movie else data.get('name'),
        'year': year,
        'rating': rating,
        'votes': data.get('vote_count', 0),
        'genres': genres,
        'runtime': runtime,
        'plot': data.get('overview'),
        'poster_url': poster_url,
        'url': f"https://www.themoviedb.org/{media_type}/{media_id}",
        'imdb_id': imdb_id,
        'tmdb_id': media_id,
        'images': processed_images,
        'languages': spoken_langs,
        'countries': countries,
        'cast': cast,
        'director': director,
        'writer': writer,
        'producer': producer,
        'composer': composer,
        'cinematographer': cinematographer,
        'release_date': release_date_str,
    }

async def get_movie_details(query):
    # IMDb suggestions JSON API (clean, fast, free, no scraping required)
    try:
        session = await get_session()
        clean_q = re.sub(r'[^a-zA-Z0-9\s]', '', str(query)).strip()
        first_letter = clean_q[0].lower() if clean_q else 'a'
        url = f"https://v3.sg.media-imdb.com/suggestion/x/{first_letter}/{clean_q.replace(' ', '_')}.json"
        async with session.get(url, timeout=5) as resp:
            if resp.status == 200:
                data = await resp.json()
                items = data.get('d', [])
                if items:
                    item = items[0]
                    imdb_id = item.get('id', '')
                    title = item.get('l', query)
                    year = item.get('y', None)
                    img = item.get('i', {}).get('imageUrl')
                    return {
                        'title': title,
                        'year': year,
                        'rating': 'N/A',
                        'genres': 'N/A',
                        'runtime': 'N/A',
                        'plot': '',
                        'poster_url': img,
                        'url': f"https://www.imdb.com/title/{imdb_id}/" if imdb_id else "",
                        'imdb_id': imdb_id
                    }
    except Exception as e:
        logger.debug(f"IMDb suggestion lookup error: {e}")
    return None

async def get_movie_detailsx(query, id=False, file=None):
    q = str(query).strip()
    try:
        data = await _fetch_tmdb_data(q, api_key=TMDB_API_KEY or None)
        if data:
            return data
    except Exception as e:
        logger.debug(f"TMDB direct call failed: {e}")

    fallback = await get_movie_details(q)
    return fallback
