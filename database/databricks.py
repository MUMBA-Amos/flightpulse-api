from databricks import sql
from queue import Empty, Queue
import os
import threading
import time


# Opening a Databricks connection takes ~2s, while the queries themselves take
# ~0.5s, so connections are kept open and reused instead of opened per request.
# Query results are also cached briefly, since the gold tables change slowly.
CACHE_TTL_SECONDS = 60

_idle_connections = Queue()
_cache = {}
_cache_lock = threading.Lock()


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
    """Run a query and return its rows as dicts, cached for CACHE_TTL_SECONDS."""
    key = (query, tuple(params) if params else None)
    now = time.monotonic()

    with _cache_lock:
        cached = _cache.get(key)

    if cached and now - cached[0] < CACHE_TTL_SECONDS:
        return cached[1]

    result = _execute_pooled(query, params)

    with _cache_lock:
        _cache[key] = (now, result)

    return result
