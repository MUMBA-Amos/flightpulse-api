import re
import threading
import time

from fastapi import APIRouter, HTTPException, Query
import httpx


router = APIRouter(
    prefix="/weather",
    tags=["Weather"]
)


# METARs are issued every 30-60 minutes and the Aviation Weather Center limits
# how fast one address may ask, so each airport's latest report is reused for a
# while instead of being fetched again for every visitor.
CACHE_SECONDS = 10 * 60
METAR_URL = "https://aviationweather.gov/api/data/metar"
ICAO_PATTERN = re.compile(r"^[A-Z0-9]{3,4}$")
MAX_BATCH = 100

_cache = {}
_cache_lock = threading.Lock()

# TAFs (forecasts) are issued every 6 hours, with occasional amendments.
TAF_CACHE_SECONDS = 30 * 60
TAF_URL = "https://aviationweather.gov/api/data/taf"

_taf_cache = {}
_taf_cache_lock = threading.Lock()


@router.get("/")
def get_weather_batch(
    ids: str = Query(..., description=f"Comma-separated ICAO codes, up to {MAX_BATCH}, e.g. WMKK,WSSS"),
):
    """Latest METAR for several airports at once: {"weather": {icao: metar or null}}."""
    codes = list(dict.fromkeys(code.strip().upper() for code in ids.split(",") if code.strip()))
    codes = [code for code in codes if ICAO_PATTERN.match(code)][:MAX_BATCH]
    return {"weather": latest_metars(codes)}


def latest_metars(codes):
    """{icao: latest METAR or None}, from the cache where possible. Raises HTTPException if the AWC fails."""
    now = time.monotonic()
    found = {}
    missing = []
    with _cache_lock:
        for code in codes:
            cached = _cache.get(code)
            if cached and now < cached[0]:
                found[code] = cached[1]
            else:
                missing.append(code)

    if missing:
        # One request for every airport not already cached.
        try:
            response = httpx.get(METAR_URL, params={"ids": ",".join(missing), "format": "json"}, timeout=15)
            response.raise_for_status()
            metars = response.json() if response.status_code != 204 else []
        except httpx.RequestError:
            raise HTTPException(status_code=503, detail="Unable to connect to Aviation Weather Center")
        except httpx.HTTPStatusError:
            raise HTTPException(status_code=response.status_code, detail="Aviation Weather Center returned an error")

        latest = {}
        for metar in metars:
            latest.setdefault(metar.get("icaoId"), metar)

        with _cache_lock:
            if len(_cache) > 5000:
                _cache.clear()
            for code in missing:
                result = _result(code, latest.get(code))
                _cache[code] = (now + CACHE_SECONDS, result)
                found[code] = result

    return {code: (found[code]["weather"] or [None])[0] for code in codes}


def latest_tafs(codes):
    """{icao: latest raw TAF or None}, from the cache where possible. Airports without a TAF get None."""
    now = time.monotonic()
    found = {}
    with _taf_cache_lock:
        for code in codes:
            cached = _taf_cache.get(code)
            if cached and now < cached[0]:
                found[code] = cached[1]
    missing = [code for code in codes if code not in found]

    if missing:
        try:
            response = httpx.get(TAF_URL, params={"ids": ",".join(missing), "format": "json"}, timeout=15)
            response.raise_for_status()
            tafs = response.json() if response.status_code != 204 else []
        except (httpx.HTTPError, ValueError):
            # Forecasts are extra detail: show the rest without them rather than fail.
            return {code: found.get(code) for code in codes}

        latest = {}
        for taf in tafs:
            latest.setdefault(taf.get("icaoId"), taf.get("rawTAF"))

        with _taf_cache_lock:
            if len(_taf_cache) > 5000:
                _taf_cache.clear()
            for code in missing:
                found[code] = latest.get(code)
                _taf_cache[code] = (now + TAF_CACHE_SECONDS, found[code])

    return {code: found[code] for code in codes}


@router.get("/{airport_icao}")
def get_weather(airport_icao: str):
    airport_icao = airport_icao.upper()

    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(airport_icao)
    if cached and now < cached[0]:
        return cached[1]

    result = _fetch_weather(airport_icao)

    with _cache_lock:
        if len(_cache) > 5000:
            _cache.clear()
        _cache[airport_icao] = (now + CACHE_SECONDS, result)

    return result


def _result(airport_icao, metar):
    """The single-airport response shape, which is also what the cache holds."""
    if metar is None:
        return {
            "airport_icao": airport_icao,
            "weather": None,
            "message": "No recent METAR available for this airport"
        }
    return {"airport_icao": airport_icao, "weather": [metar]}


def _fetch_weather(airport_icao):
    url = METAR_URL

    params = {
        "ids": airport_icao,
        "format": "json"
    }

    try:
        response = httpx.get(
            url,
            params=params,
            timeout=10
        )

        if response.status_code == 204:
            return {
                "airport_icao": airport_icao,
                "weather": None,
                "message": "No recent METAR available for this airport"
            }

        response.raise_for_status()

        return {
            "airport_icao": airport_icao,
            "weather": response.json()
        }

    except httpx.RequestError:
        raise HTTPException(
            status_code=503,
            detail="Unable to connect to Aviation Weather Center"
        )

    except httpx.HTTPStatusError:
        raise HTTPException(
            status_code=response.status_code,
            detail="Aviation Weather Center returned an error"
        )
