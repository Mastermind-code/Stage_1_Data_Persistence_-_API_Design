"""
Upstash Redis HTTP cache layer for the Insighta Labs+ API.

Uses the Upstash Redis REST API (HTTP GET-based) so it works in serverless
environments like Vercel where persistent TCP connections are not available.

When REDIS_REST_URL and REDIS_REST_TOKEN are not set, all cache operations
silently no-op so the application continues to function without Redis.
"""

import hashlib
import json
import logging
import os
from typing import Any, Optional
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal configuration helpers
# ---------------------------------------------------------------------------


def _get_config() -> tuple[str, str] | tuple[None, None]:
    """Return (rest_url, token) from env vars, or (None, None) if unconfigured."""
    url = os.getenv("REDIS_REST_URL", "").strip().rstrip("/")
    token = os.getenv("REDIS_REST_TOKEN", "").strip()
    if not url or not token:
        return None, None
    return url, token


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Public cache operations
# ---------------------------------------------------------------------------


def get_cached(key: str) -> Optional[str]:
    """GET a key from Upstash.

    Returns the raw string value stored under *key*, or ``None`` on a cache
    miss, a null result, or any network / parse error.
    """
    url, token = _get_config()
    if url is None or token is None:
        return None
    try:
        resp = requests.get(
            f"{url}/get/{quote(key, safe=':')}",
            headers=_headers(token),
            timeout=2,
        )
        data = resp.json()
        result = data.get("result")
        # Upstash returns JSON null for a miss; ignore it
        return result if isinstance(result, str) else None
    except Exception as exc:  # noqa: BLE001
        logger.debug("cache.get_cached(%s) error: %s", key, exc)
        return None


def set_cached(key: str, value: str, ttl_seconds: int = 300) -> None:
    """SETEX *key* → *value* in Upstash with the given TTL (seconds).

    *value* is URL-encoded so arbitrary JSON strings are safe to store.
    Errors are silently swallowed.
    """
    url, token = _get_config()
    if url is None or token is None:
        return
    try:
        # Upstash REST SETEX: GET /setex/{key}/{ttl}/{value}
        encoded_value = quote(value, safe="")
        requests.get(
            f"{url}/setex/{quote(key, safe=':')}/{ttl_seconds}/{encoded_value}",
            headers=_headers(token),
            timeout=2,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("cache.set_cached(%s) error: %s", key, exc)


def delete_by_prefix(prefix: str) -> None:
    """Delete all keys whose names begin with *prefix*.

    Uses cursor-based SCAN to avoid blocking Redis on large keyspaces, then
    issues a single DEL per batch.  Cursor loop runs until Upstash returns
    cursor ``"0"`` (full iteration complete).  All errors are silently ignored.
    """
    url, token = _get_config()
    if url is None or token is None:
        return
    try:
        # The asterisk is the Redis glob wildcard — must NOT be percent-encoded.
        pattern = quote(prefix, safe=":*") + "*"
        cursor = "0"
        while True:
            resp = requests.get(
                f"{url}/scan/{cursor}/match/{pattern}/count/100",
                headers=_headers(token),
                timeout=2,
            )
            data = resp.json()
            result = data.get("result", ["0", []])
            next_cursor = str(result[0])
            keys: list[Any] = result[1] if len(result) > 1 else []

            if keys:
                # DEL multiple keys in one REST call: /del/{key1}/{key2}/...
                keys_path = "/".join(quote(k, safe=":") for k in keys)
                requests.get(
                    f"{url}/del/{keys_path}",
                    headers=_headers(token),
                    timeout=2,
                )

            cursor = next_cursor
            if cursor == "0":
                break
    except Exception as exc:  # noqa: BLE001
        logger.debug("cache.delete_by_prefix(%s) error: %s", prefix, exc)


# ---------------------------------------------------------------------------
# Parameter normalisation (shared by all key-builders)
# ---------------------------------------------------------------------------

_INT_FIELDS = frozenset({"min_age", "max_age", "page", "limit"})


def _normalize_params(params: dict[str, Any]) -> dict[str, Any]:
    """Return a normalised, deterministically-sorted copy of *params*.

    Rules applied:
    - ``None`` and ``""`` values are dropped entirely.
    - ``country_id`` is stripped and uppercased (ISO alpha-2 convention).
    - Fields in ``_INT_FIELDS`` are cast to ``int``; failures are dropped.
    - All other string values are stripped and lowercased.
    - Keys are sorted alphabetically so ``json.dumps`` is byte-for-byte
      identical for logically equivalent parameter sets.
    """
    normalised: dict[str, Any] = {}
    for k, v in params.items():
        if v is None or v == "":
            continue
        if k == "country_id":
            s = str(v).strip().upper()
            if s:
                normalised[k] = s
        elif k in _INT_FIELDS:
            try:
                normalised[k] = int(v)
            except (ValueError, TypeError):
                pass  # drop unparseable ints
        elif isinstance(v, str):
            s = v.strip().lower()
            if s:
                normalised[k] = s
        else:
            normalised[k] = v
    return dict(sorted(normalised.items()))


def _hash_params(params: dict[str, Any]) -> str:
    """Return the first 16 hex chars of SHA-256(json(normalised params))."""
    normalised = _normalize_params(params)
    raw = json.dumps(normalised, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Public key-builder helpers
# ---------------------------------------------------------------------------


def make_list_key(params: dict[str, Any]) -> str:
    """Deterministic cache key for the paginated list endpoint.

    Example::

        make_list_key({"gender": "Female", "page": 1})
        # -> "profiles:list:3f2a8c1b04e67d91"
    """
    return f"profiles:list:{_hash_params(params)}"


def make_search_key(filters: dict[str, Any]) -> str:
    """Deterministic cache key for the natural-language search endpoint."""
    return f"profiles:search:{_hash_params(filters)}"


def make_profile_key(profile_id: str) -> str:
    """Cache key for a single profile look-up by ID."""
    return f"profiles:get:{profile_id}"


def make_count_key(params: dict[str, Any]) -> str:
    """Deterministic cache key for an aggregate count query."""
    return f"profiles:count:{_hash_params(params)}"
