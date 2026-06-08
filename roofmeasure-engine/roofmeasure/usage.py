"""Usage logging for the engine: every measurement call gets a row in SQLite.

Captures cost (cents) per engine source. Solar API is the only paid engine
right now, but the schema accommodates future paid sources (Nearmap, HOVER, etc).

Database location: env ROOFMEASURE_USAGE_DB, default /var/lib/roofmeasure/usage.db.
The service user needs write access to the parent directory:

    sudo mkdir -p /var/lib/roofmeasure
    sudo chown roofmeasure:roofmeasure /var/lib/roofmeasure
"""
from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Dict, List, Optional

LOG = logging.getLogger(__name__)
_LOCK = threading.Lock()

# Cost defaults in cents. Override with env if pricing changes.
SOLAR_COST_CENTS = int(os.environ.get("SOLAR_API_COST_CENTS", "50"))   # ~$0.50/req
LIDAR_COST_CENTS = int(os.environ.get("LIDAR_ENGINE_COST_CENTS", "0"))  # free


def _db_path() -> str:
    return os.environ.get("ROOFMEASURE_USAGE_DB", "/var/lib/roofmeasure/usage.db")


def _get_conn() -> sqlite3.Connection:
    path = _db_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS engine_calls (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            ts           TEXT    NOT NULL,        -- ISO 8601 UTC
            ts_epoch     INTEGER NOT NULL,        -- for date math
            address_hash TEXT    NOT NULL,        -- sha1 of normalized address
            strategy     TEXT    NOT NULL,        -- requested strategy
            engine       TEXT    NOT NULL,        -- lidar | solar | hash_fallback
            success      INTEGER NOT NULL,        -- 0 | 1
            latency_ms   INTEGER NOT NULL,
            cost_cents   INTEGER NOT NULL,
            error        TEXT,                    -- error message if !success
            roof_area_sqft REAL,
            confidence   INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_engine_calls_ts ON engine_calls(ts_epoch);
        CREATE INDEX IF NOT EXISTS idx_engine_calls_engine ON engine_calls(engine);
        CREATE INDEX IF NOT EXISTS idx_engine_calls_success ON engine_calls(success);
    """)


def _hash_address(address: str) -> str:
    """Hash to avoid storing customer PII in plaintext."""
    return hashlib.sha1(address.strip().lower().encode("utf-8")).hexdigest()[:16]


@contextlib.contextmanager
def time_call(address: str, strategy: str):
    """Context manager that records a single engine call.

    Usage:
        with time_call(address, "auto") as record:
            ...
            record(engine="solar", success=True, roof_area_sqft=1895, confidence=92)
    """
    start = time.monotonic()
    captured: Dict[str, Any] = {
        "engine": "unknown",
        "success": False,
        "error": None,
        "roof_area_sqft": None,
        "confidence": None,
    }

    def record(**kwargs):
        captured.update(kwargs)

    try:
        yield record
    finally:
        latency_ms = int((time.monotonic() - start) * 1000)
        engine = captured["engine"]
        cost_cents = SOLAR_COST_CENTS if engine == "solar" else LIDAR_COST_CENTS
        if not captured["success"]:
            cost_cents = 0   # don't bill for failed calls
        _write_row({
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "ts_epoch": int(time.time()),
            "address_hash": _hash_address(address),
            "strategy": strategy,
            "engine": engine,
            "success": 1 if captured["success"] else 0,
            "latency_ms": latency_ms,
            "cost_cents": cost_cents,
            "error": captured["error"],
            "roof_area_sqft": captured["roof_area_sqft"],
            "confidence": captured["confidence"],
        })


def _write_row(row: Dict[str, Any]) -> None:
    try:
        with _LOCK:
            conn = _get_conn()
            try:
                _init_schema(conn)
                conn.execute(
                    """INSERT INTO engine_calls
                       (ts, ts_epoch, address_hash, strategy, engine, success,
                        latency_ms, cost_cents, error, roof_area_sqft, confidence)
                       VALUES (:ts, :ts_epoch, :address_hash, :strategy, :engine,
                               :success, :latency_ms, :cost_cents, :error,
                               :roof_area_sqft, :confidence)""",
                    row,
                )
            finally:
                conn.close()
    except sqlite3.OperationalError as exc:
        LOG.warning("usage log write failed: %s", exc)


# ---------------------------------------------------------------------------
# Aggregation queries for the admin dashboard
# ---------------------------------------------------------------------------

def summary(from_epoch: Optional[int] = None, to_epoch: Optional[int] = None) -> Dict[str, Any]:
    """Return an aggregated summary for the admin dashboard."""
    now = int(time.time())
    if from_epoch is None:
        # default: month-to-date in UTC
        gm = time.gmtime(now)
        from_epoch = int(time.mktime(time.struct_time(
            (gm.tm_year, gm.tm_mon, 1, 0, 0, 0, 0, 0, 0))))
    if to_epoch is None:
        to_epoch = now

    with _LOCK:
        conn = _get_conn()
        try:
            _init_schema(conn)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()

            cur.execute(
                """SELECT COUNT(*) c, COALESCE(SUM(cost_cents), 0) cents,
                          COALESCE(SUM(success), 0) ok,
                          COUNT(*) - COALESCE(SUM(success), 0) fail
                     FROM engine_calls
                    WHERE ts_epoch >= ? AND ts_epoch <= ?""",
                (from_epoch, to_epoch),
            )
            totals = dict(cur.fetchone())

            cur.execute(
                """SELECT engine, COUNT(*) c, COALESCE(SUM(cost_cents),0) cents,
                          COALESCE(AVG(latency_ms),0) avg_latency_ms,
                          COALESCE(AVG(confidence),0) avg_confidence
                     FROM engine_calls
                    WHERE ts_epoch >= ? AND ts_epoch <= ? AND success = 1
                    GROUP BY engine""",
                (from_epoch, to_epoch),
            )
            by_engine = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """SELECT substr(ts, 1, 10) day,
                          COUNT(*) c,
                          COALESCE(SUM(cost_cents),0) cents,
                          COALESCE(SUM(CASE WHEN engine='solar' THEN 1 ELSE 0 END),0) solar_calls,
                          COALESCE(SUM(CASE WHEN engine='lidar' THEN 1 ELSE 0 END),0) lidar_calls
                     FROM engine_calls
                    WHERE ts_epoch >= ? AND ts_epoch <= ?
                    GROUP BY day
                    ORDER BY day""",
                (from_epoch, to_epoch),
            )
            by_day = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """SELECT ts, address_hash, engine, error, latency_ms
                     FROM engine_calls
                    WHERE success = 0 AND ts_epoch >= ?
                    ORDER BY ts_epoch DESC LIMIT 25""",
                (from_epoch,),
            )
            recent_failures = [dict(r) for r in cur.fetchall()]

            cur.execute("SELECT COUNT(*) c FROM engine_calls")
            lifetime = cur.fetchone()["c"]
            return {
                "fromEpoch": from_epoch,
                "toEpoch": to_epoch,
                "totals": totals,
                "byEngine": by_engine,
                "byDay": by_day,
                "recentFailures": recent_failures,
                "lifetimeCalls": lifetime,
                "solarCostCentsPerCall": SOLAR_COST_CENTS,
            }
        finally:
            conn.close()
