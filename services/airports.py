import threading

import httpx


# Airport coordinates from the Aviation Weather Center (free, no key). Airports
# don't move, so each one is fetched once and kept for the life of the process.
AIRPORT_URL = "https://aviationweather.gov/api/data/airport"
BATCH_SIZE = 50

_coordinates = {}
_lock = threading.Lock()


def coordinates_for(*icao_codes):
    """{icao: (latitude, longitude)} for the given airports; unknown airports are left out."""
    wanted = list(dict.fromkeys(code for code in icao_codes if code))
    with _lock:
        missing = [code for code in wanted if code not in _coordinates]

    for start in range(0, len(missing), BATCH_SIZE):
        batch = missing[start:start + BATCH_SIZE]
        try:
            response = httpx.get(AIRPORT_URL, params={"ids": ",".join(batch), "format": "json"}, timeout=10)
            airports = response.json() if response.status_code == 200 else []
        except (httpx.HTTPError, ValueError):
            airports = []
        with _lock:
            for airport in airports:
                if airport.get("icaoId") and airport.get("lat") is not None and airport.get("lon") is not None:
                    _coordinates[airport["icaoId"]] = (airport["lat"], airport["lon"])

    with _lock:
        return {code: _coordinates[code] for code in wanted if code in _coordinates}
