"""
Databricks access for the API, built as a small serving layer.

Visitors never wait on Databricks: every query's result is saved on disk, and
requests are answered from that copy. A background task refreshes the saved
queries once an hour, all in one burst, so the SQL warehouse wakes briefly
once an hour however many people visit. That keeps the site within
Databricks' free daily compute allowance, and keeps it working (with the last
good data) while Databricks is unavailable.

Only a query the API has never seen (e.g. the first lookup of a particular
flight) goes to Databricks directly; after that it joins the hourly refresh.
Queries nobody has asked for in a day are dropped from the refresh.
"""

from databricks import sql
from decimal import Decimal
from pathlib import Path
from queue import Empty, Queue
import fcntl
import hashlib
import json
import logging
import os
import threading
import time


# The pipeline updates aircraft hourly and flights once a day.
REFRESH_SECONDS = 60 * 60
# Saved queries nobody has requested for this long stop being refreshed.
UNUSED_AFTER_SECONDS = 24 * 60 * 60
# How often a query's "last used" time is written back to disk.
TOUCH_EVERY_SECONDS = 10 * 60
# The API uses about 25 distinct queries. The cap is a safety net: if a future
# endpoint let visitors create new queries, it stops them from multiplying the
# hourly refresh and spending Databricks' daily allowance.
MAX_SAVED_QUERIES = 100

STORE_DIR = Path(__file__).resolve().parent.parent / ".cache" / "queries"
LOCK_FILE = STORE_DIR.parent / "refresh.lock"
LAST_REFRESH_FILE = STORE_DIR.parent / "last-refresh"

_idle_connections = Queue()
# key -> {"rows", "mtime" (of the file it came from), "touched" (last time last_used was saved)}
_memory = {}
_memory_lock = threading.Lock()
_log = logging.getLogger("uvicorn.error")


def get_connection():
    return sql.connect(
        server_hostname=os.getenv("DATABRICKS_SERVER_HOSTNAME"),
        http_path=os.getenv("DATABRICKS_HTTP_PATH"),
        access_token=os.getenv("DATABRICKS_TOKEN")
    )


def run_query(query, params=None):
    """
    Rows (as dicts) for a query, from the saved copy. Only a query that has
    never been run before goes to Databricks, and raises if Databricks fails.
    """
    params = list(params) if params else None
    key = _key(query, params)

    rows = _saved_rows(key)
    if rows is not None:
        return rows

    if _saved_count() >= MAX_SAVED_QUERIES:
        raise RuntimeError("Too many distinct saved queries; refusing to add another")

    rows = _execute_pooled(query, params)
    _save(key, query, params, rows, last_used=time.time())
    return rows


def start_refresher():
    """
    Refreshes every saved query once an hour, in a background thread. Only runs
    where FLIGHTPULSE_REFRESH=on (set on the server), so a local copy of the API
    doesn't spend Databricks' daily allowance too.
    """
    if os.getenv("FLIGHTPULSE_REFRESH", "").lower() != "on":
        return
    threading.Thread(target=_refresh_loop, name="databricks-refresh", daemon=True).start()


# ---------- Saved copies ----------

def _key(query, params):
    return hashlib.sha256(json.dumps([query, params]).encode()).hexdigest()


def _path(key):
    return STORE_DIR / f"{key}.json"


def _saved_rows(key):
    """The saved rows for a key, from memory or, if newer (another worker refreshed), from disk."""
    path = _path(key)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = None

    with _memory_lock:
        entry = _memory.get(key)

    if mtime is not None and (entry is None or mtime > entry["mtime"]):
        saved = _read(path)
        if saved is not None:
            entry = {"rows": saved["rows"], "mtime": mtime, "touched": entry["touched"] if entry else 0}
            with _memory_lock:
                _memory[key] = entry

    if entry is None:
        return None

    if time.time() - entry["touched"] > TOUCH_EVERY_SECONDS:
        _touch(key)
    return entry["rows"]


def _saved_count():
    try:
        return sum(1 for _ in STORE_DIR.glob("*.json"))
    except OSError:
        return 0


def _save(key, query, params, rows, last_used):
    try:
        STORE_DIR.mkdir(parents=True, exist_ok=True)
        path = _path(key)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(
            {"query": query, "params": params, "rows": rows, "saved_at": time.time(), "last_used": last_used},
            default=_to_json,
        ))
        temporary.replace(path)
        saved = _read(path)
        with _memory_lock:
            _memory[key] = {"rows": saved["rows"], "mtime": path.stat().st_mtime, "touched": time.time()}
    except OSError as error:
        _log.warning("Couldn't save a query result: %s", error)


def _touch(key):
    """Record that the query is still in use, so the refresh keeps it."""
    with _memory_lock:
        if key in _memory:
            _memory[key]["touched"] = time.time()
    path = _path(key)
    saved = _read(path)
    if saved is None:
        return
    saved["last_used"] = time.time()
    try:
        # Keep the file's modification time: it marks when the data was refreshed.
        stat = path.stat()
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(saved))
        temporary.replace(path)
        os.utime(path, (stat.st_atime, stat.st_mtime))
    except OSError:
        pass


def _read(path):
    """A saved query as a dict with "query", "params" and "rows", or None if missing or unreadable."""
    try:
        saved = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return saved if isinstance(saved, dict) and "rows" in saved and "query" in saved else None


def _to_json(value):
    """Dates and times as ISO strings and decimals as numbers, as the API would send them."""
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


# ---------- Hourly refresh ----------

def _refresh_loop():
    while True:
        time.sleep(REFRESH_SECONDS / 4)
        try:
            _refresh_if_due()
        except Exception:
            _log.exception("Refreshing saved queries failed")


def _refresh_if_due():
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "w") as lock:
        # Each API worker runs this loop; the lock lets only one refresh at a time.
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return

        try:
            last = float(LAST_REFRESH_FILE.read_text())
        except (OSError, ValueError):
            last = 0
        if time.time() - last < REFRESH_SECONDS:
            return

        refreshed, dropped = _refresh_all()
        LAST_REFRESH_FILE.write_text(str(time.time()))
        _log.info("Refreshed %d saved queries from Databricks (%d unused dropped)", refreshed, dropped)


def _refresh_all():
    refreshed = dropped = 0
    for path in sorted(STORE_DIR.glob("*.json")):
        saved = _read(path)
        if saved is None:
            continue
        if time.time() - saved.get("last_used", 0) > UNUSED_AFTER_SECONDS:
            path.unlink(missing_ok=True)
            dropped += 1
            continue
        try:
            rows = _execute_pooled(saved["query"], saved["params"])
        except Exception as error:
            message = str(error).splitlines()[0][:200]
            _log.warning("Couldn't refresh a saved query, keeping the old result: %s", message)
            if "daily limit" in message.lower():
                break
            continue
        _save(path.stem, saved["query"], saved["params"], rows, saved.get("last_used", time.time()))
        refreshed += 1
    return refreshed, dropped


# ---------- Databricks ----------

def _execute(connection, query, params):
    with connection.cursor() as cursor:
        cursor.execute(query, tuple(params) if params else None)

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
