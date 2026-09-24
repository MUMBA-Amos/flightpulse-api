from databricks import sql
from decimal import Decimal
from pathlib import Path
from queue import Empty, Queue
import hashlib
import json
import logging
import os
import threading
import time


# Opening a Databricks connection takes ~2s, while the queries themselves take
# ~0.5s, so connections are kept open and reused instead of opened per request.
# The data changes at most every 30 minutes (aircraft) or once a day (flights),
# so results are cached for a while, which also keeps Databricks' free daily
# compute allowance from being used up by visitors.
CACHE_TTL_SECONDS = 15 * 60

# The last good result of every query is also kept on disk. When Databricks
# can't answer (daily limit reached, warehouse busy, outage), that copy is
# served instead of an error, so the site keeps working with older data.
# It survives restarts and deploys.
FALLBACK_DIR = Path(__file__).resolve().parent.parent / ".cache" / "queries"

_idle_connections = Queue()
_cache = {}
_cache_lock = threading.Lock()
_log = logging.getLogger("uvicorn.error")


def get_connection():
    return sql.connect(
        server_hostname=os.getenv("DATABRICKS_SERVER_HOSTNAME"),
        http_path=os.getenv("DATABRICKS_HTTP_PATH"),
        access_token=os.getenv("DATABRICKS_TOKEN")
    )


def _execute(connection, query, params):
    with connection.cursor() as cursor:
        cursor.execute(query, params)

        columns = [column[0] for column in cursor.description]
        rows = cursor.fetchall()

        return [dict(zip(columns, row)) for row in rows]


def _execute_pooled(query, params):
    try:
        connection = _idle_connections.get_nowait()
        reused = True
    except Empty:
        connection = get_connection()
        reused = False

    try:
        result = _execute(connection, query, params)
    except Exception:
        connection.close()

        if not reused:
            raise

        # The pooled connection may have expired on the Databricks side,
        # so retry once on a fresh one.
        connection = get_connection()

        try:
            result = _execute(connection, query, params)
        except Exception:
            connection.close()
            raise

    _idle_connections.put(connection)
    return result


def run_query(query, params=None):
    """
    Run a query and return its rows as dicts, cached for CACHE_TTL_SECONDS.
    If Databricks fails, returns the last good result (from memory, then disk);
    raises only when there has never been one.
    """
    key = (query, tuple(params) if params else None)
    now = time.monotonic()

    with _cache_lock:
        cached = _cache.get(key)

    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    try:
        result = _execute_pooled(query, params)
    except Exception as error:
        fallback = cached[1] if cached else _read_fallback(key)
        if fallback is None:
            raise
        _log.warning("Databricks query failed, serving the last good result: %s", str(error).splitlines()[0][:200])
        return fallback

    with _cache_lock:
        _cache[key] = (now, result)
    _write_fallback(key, result)

    return result


def _fallback_path(key):
    digest = hashlib.sha256(json.dumps(key, default=str).encode()).hexdigest()
    return FALLBACK_DIR / f"{digest}.json"


def _to_json(value):
    """Dates and times as ISO strings and decimals as numbers, as the API would send them."""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def _write_fallback(key, result):
    try:
        FALLBACK_DIR.mkdir(parents=True, exist_ok=True)
        path = _fallback_path(key)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(result, default=_to_json))
        temporary.replace(path)
    except OSError as error:
        _log.warning("Couldn't save the fallback copy of a query result: %s", error)


def _read_fallback(key):
    try:
        return json.loads(_fallback_path(key).read_text())
    except (OSError, ValueError):
        return None
