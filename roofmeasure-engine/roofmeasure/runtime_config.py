"""Runtime-mutable engine configuration, persisted to disk.

This lets an admin flip the strategy (LiDAR / Solar / Auto) WITHOUT restarting
the systemd service. The engine reads the persisted file on startup and
overlays it on top of the env-var defaults.

Config file location (set via ROOFMEASURE_CONFIG_FILE env, default
/var/lib/roofmeasure/runtime.json). The service user must have write access
to this directory:

    sudo mkdir -p /var/lib/roofmeasure
    sudo chown roofmeasure:roofmeasure /var/lib/roofmeasure
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

LOG = logging.getLogger(__name__)

_DEFAULT_PATH = "/var/lib/roofmeasure/runtime.json"
_LOCK = threading.Lock()
_CACHED: Optional[Dict[str, Any]] = None
_CACHED_MTIME: float = 0.0


def _config_path() -> str:
    return os.environ.get("ROOFMEASURE_CONFIG_FILE", _DEFAULT_PATH)


def get_runtime_config() -> Dict[str, Any]:
    """Return the current runtime config, reading from disk if changed since last call.

    Falls back to an empty dict if the file is missing or unreadable - in which
    case the engine uses its env-var defaults.
    """
    global _CACHED, _CACHED_MTIME
    path = _config_path()
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return {}
    except OSError as exc:
        LOG.warning("cannot stat %s: %s", path, exc)
        return {}

    with _LOCK:
        if _CACHED is not None and st.st_mtime == _CACHED_MTIME:
            return _CACHED
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                LOG.warning("runtime config %s is not a JSON object", path)
                return {}
            _CACHED = data
            _CACHED_MTIME = st.st_mtime
            return data
        except (json.JSONDecodeError, OSError) as exc:
            LOG.warning("cannot read %s: %s", path, exc)
            return {}


def set_runtime_config(updates: Dict[str, Any], who: str = "unknown") -> Dict[str, Any]:
    """Merge `updates` into the persisted config. Returns the new full config."""
    global _CACHED, _CACHED_MTIME
    path = _config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _LOCK:
        current = get_runtime_config()
        merged = dict(current)
        merged.update(updates)
        merged["_updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        merged["_updatedBy"] = who
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2)
        os.replace(tmp, path)
        _CACHED = merged
        _CACHED_MTIME = os.stat(path).st_mtime
        LOG.info("runtime config updated by %s: %s", who, list(updates.keys()))
        return merged


def get_strategy() -> str:
    """Resolve the effective strategy: persisted -> env -> default 'auto'."""
    cfg = get_runtime_config()
    if "strategy" in cfg:
        return str(cfg["strategy"])
    return os.environ.get("ROOFMEASURE_STRATEGY", "auto")


VALID_STRATEGIES = {"auto", "lidar_only", "solar_only", "solar_first"}
