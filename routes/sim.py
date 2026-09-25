import math
import re
import threading
import time
from typing import Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Query

from routes.aircraft import CALLSIGN_PATTERN, _route_from_adsbdb, cached_route
from routes.weather import latest_metars, latest_tafs
from services.airports import details_for


router = APIRouter(
    prefix="/sim",
    tags=["Flight sim"]
)


# Everything a flight simmer needs to recreate a live flight: the aircraft, both
# airports' weather, forecast, runways and frequencies, the runway the wind
# favours, and a SimBrief link with the flight filled in. All of it comes from
# free outside services, never from Databricks.
ADSBDB_AIRCRAFT_URL = "https://api.adsbdb.com/v0/aircraft/{}"
# Second source for aircraft adsbdb doesn't know (free, no key; rejects requests without a user agent).
HEXDB_AIRCRAFT_URL = "https://hexdb.io/api/v1/aircraft/{}"
USER_AGENT = "FlightPulse/1.0 (+https://flightpulse-frontend.vercel.app)"
SIMBRIEF_URL = "https://dispatch.simbrief.com/options/custom"
AIRCRAFT_CACHE_SECONDS = 24 * 60 * 60
ICAO24_PATTERN = re.compile(r"^[0-9a-f]{6}$")
# An airline callsign: 3-letter ICAO airline code, then the flight number (e.g. AXM310).
AIRLINE_CALLSIGN = re.compile(r"^([A-Z]{3})(\d[A-Z0-9]*)$")
# Below this, the wind doesn't favour any runway.
CALM_WIND_KT = 3
# Below this, a level aircraft is more likely holding or on approach than cruising.
MIN_CRUISE_FT = 10000
# SimBrief dispatch parameters we fill in, and how the page describes them.
SIMBRIEF_LABELS = {
    "orig": "Departure",
    "dest": "Destination",
    "airline": "Airline",
    "fltnum": "Flight number",
    "callsign": "Callsign",
    "type": "Aircraft type",
    "reg": "Registration",
    "fl": "Cruise altitude",
    "origrwy": "Departure runway",
    "destrwy": "Arrival runway",
}

_aircraft_cache = {}
_aircraft_cache_lock = threading.Lock()


@router.get("/{callsign}")
def get_sim_briefing(
    callsign: str,
    icao24: Optional[str] = Query(None, description="The aircraft's transponder code, for its type and registration"),
    cruise_ft: Optional[int] = Query(None, ge=0, le=60000, description="Current altitude, when the aircraft is level at cruise"),
):
    """A flight-sim briefing for a live flight: aircraft, airports, weather, likely runways and a SimBrief link."""
    callsign = callsign.strip().upper()
    if not CALLSIGN_PATTERN.match(callsign):
        raise HTTPException(status_code=404, detail="Not a valid callsign")
    icao24 = (icao24 or "").strip().lower()

    route = _route(callsign)
    aircraft = _aircraft(icao24) if ICAO24_PATTERN.match(icao24) else None

    ends = [route.get("origin"), route.get("destination")]
    codes = [airport["icao"] for airport in ends if airport and airport.get("icao")]
    details = details_for(*codes)
    try:
        metars = latest_metars(codes)
    except HTTPException:
        metars = {}
    tafs = latest_tafs(codes)

    origin, destination = (_briefing_airport(airport, details, metars, tafs) for airport in ends)
    simbrief = _simbrief_fields(callsign, origin, destination, aircraft, cruise_ft)
    return {
        "callsign": callsign,
        "airline": route.get("airline"),
        "route_source": route.get("source"),
        "aircraft": aircraft,
        "origin": origin,
        "destination": destination,
        "cruise_level": simbrief.get("fl"),
        "simbrief_url": f"{SIMBRIEF_URL}?{urlencode(simbrief)}" if simbrief else None,
        # What the SimBrief link fills in, for the page to show before it's opened.
        "simbrief_fields": [{"label": SIMBRIEF_LABELS[key], "value": value} for key, value in simbrief.items()],
    }


def _route(callsign):
    """
    The route the globe already looked up for this callsign, otherwise adsbdb's.
    Skips Databricks, so the briefing never waits on it (a slow or unreachable
    warehouse can hold a query for minutes).
    """
    return cached_route(callsign) or _route_from_adsbdb(callsign) or {}


def _aircraft(icao24):
    """
    Type and registration from adsbdb, or hexdb.io when adsbdb doesn't know the
    aircraft; cached for a day. None when neither knows it or the lookups fail.
    """
    now = time.monotonic()
    with _aircraft_cache_lock:
        cached = _aircraft_cache.get(icao24)
    if cached and now < cached[0]:
        return cached[1]

    aircraft = _aircraft_from_adsbdb(icao24)
    if not aircraft:
        from_hexdb = _aircraft_from_hexdb(icao24)
        if from_hexdb is None or aircraft is None:
            # A lookup failed rather than finding nothing, so don't remember the miss.
            return from_hexdb or None
        aircraft = from_hexdb or None

    with _aircraft_cache_lock:
        if len(_aircraft_cache) > 5000:
            _aircraft_cache.clear()
        _aircraft_cache[icao24] = (now + AIRCRAFT_CACHE_SECONDS, aircraft)
    return aircraft


def _aircraft_from_adsbdb(icao24):
    """The aircraft, {} if adsbdb doesn't know it, or None if the lookup failed."""
    try:
        response = httpx.get(ADSBDB_AIRCRAFT_URL.format(icao24), timeout=5)
        data = response.json().get("response")
    except (httpx.HTTPError, ValueError):
        return None
    found = data.get("aircraft") if isinstance(data, dict) else None
    if not found:
        return {}
    return {
        "type": found.get("type"),
        "icao_type": found.get("icao_type"),
        "manufacturer": found.get("manufacturer"),
        "registration": found.get("registration"),
        "owner": found.get("registered_owner"),
        "photo": found.get("url_photo_thumbnail"),
    }


def _aircraft_from_hexdb(icao24):
    """The aircraft, {} if hexdb.io doesn't know it, or None if the lookup failed."""
    try:
        response = httpx.get(HEXDB_AIRCRAFT_URL.format(icao24), headers={"User-Agent": USER_AGENT}, timeout=5)
        found = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(found, dict) or not found.get("ICAOTypeCode"):
        return {} if response.status_code == 404 or isinstance(found, dict) else None
    return {
        "type": found.get("Type"),
        "icao_type": found.get("ICAOTypeCode"),
        "manufacturer": found.get("Manufacturer"),
        "registration": found.get("Registration"),
        "owner": found.get("RegisteredOwners"),
        "photo": None,
    }


def _briefing_airport(airport, details, metars, tafs):
    if not airport:
        return None
    icao = airport.get("icao")
    info = details.get(icao) or {}
    metar = metars.get(icao)
    return {
        "icao": icao,
        "iata": airport.get("iata"),
        "name": airport.get("name") or info.get("name"),
        "city": airport.get("city"),
        "elevation_ft": info.get("elevation_ft"),
        "runways": [runway["id"] for runway in info.get("runways", [])],
        "frequencies": info.get("frequencies", []),
        "metar": metar.get("rawOb") if metar else None,
        "flight_category": metar.get("fltCat") if metar else None,
        "wind": _wind(metar),
        "taf": tafs.get(icao),
        "likely_runway": _likely_runway(info.get("runways", []), metar),
    }


def _wind(metar):
    if not metar or metar.get("wspd") is None:
        return None
    direction = metar.get("wdir")
    return {
        "direction": direction if isinstance(direction, (int, float)) else None,
        "variable": not isinstance(direction, (int, float)),
        "speed_kt": metar.get("wspd"),
        "gust_kt": metar.get("wgst"),
    }


def _likely_runway(runways, metar):
    """
    The runway end(s) pointing most directly into the wind, with the head- and
    crosswind on them. A best guess: air traffic control also weighs noise
    rules, traffic and preferred runways.
    """
    wind = _wind(metar)
    ends = [end for runway in runways for end in _runway_ends(runway["id"])]
    if not wind or not ends:
        return None
    if wind["variable"] or wind["speed_kt"] < CALM_WIND_KT:
        return {"ends": [], "headwind_kt": None, "crosswind_kt": None, "note": "Light or variable wind, so no runway is favoured"}

    def angle_off(end):
        return abs((wind["direction"] - end[1] + 180) % 360 - 180)

    best = min(angle_off(end) for end in ends)
    chosen = [name for name, heading in ends if angle_off((name, heading)) == best]
    radians = math.radians(best)
    return {
        "ends": chosen,
        "headwind_kt": round(wind["speed_kt"] * math.cos(radians)),
        "crosswind_kt": round(abs(wind["speed_kt"] * math.sin(radians))),
        "note": None,
    }


def _runway_ends(runway_id):
    """"14L/32R" -> [("14L", 140), ("32R", 320)]. Helipads and unnumbered runways are skipped."""
    ends = []
    for end in runway_id.split("/"):
        match = re.match(r"^(\d{1,2})[LCR]?$", end.strip())
        if match and 1 <= int(match.group(1)) <= 36:
            ends.append((end.strip(), int(match.group(1)) * 10))
    return ends


def _simbrief_fields(callsign, origin, destination, aircraft, cruise_ft):
    """
    SimBrief dispatch parameters for the flight, or {} without both airports.
    The waypoint route is left to SimBrief, which builds one; no free source has
    the route actually filed.
    """
    if not (origin and origin["icao"] and destination and destination["icao"]):
        return {}
    fields = {"orig": origin["icao"], "dest": destination["icao"]}
    airline_flight = AIRLINE_CALLSIGN.match(callsign)
    if airline_flight:
        fields["airline"], fields["fltnum"] = airline_flight.groups()
    fields["callsign"] = callsign
    if aircraft and aircraft.get("icao_type"):
        fields["type"] = aircraft["icao_type"]
    if aircraft and aircraft.get("registration"):
        fields["reg"] = aircraft["registration"]
    if cruise_ft and cruise_ft >= MIN_CRUISE_FT:
        fields["fl"] = f"FL{round(cruise_ft / 1000) * 10:03d}"
    for key, airport in (("origrwy", origin), ("destrwy", destination)):
        runway = airport.get("likely_runway")
        if runway and runway["ends"]:
            fields[key] = runway["ends"][0]
    return fields
