"""
Query normalization for the Insighta Labs+ API.

Two natural-language queries that express the same intent often arrive with
differently-capitalised or differently-ordered filter values.  For example,
"Nigerian females between 20 and 45" and "Women aged 20-45 living in Nigeria"
both parse to an equivalent filter dict; without normalization the raw dict
may still differ in key ordering or string casing, producing *different* cache
keys for what is logically the *same* query.

This module solves that by applying deterministic, field-aware rules to every
filter value before hashing:

* String fields are stripped of surrounding whitespace and lowercased (or
  uppercased for ISO alpha-2 country codes).
* Numeric fields are cast to their canonical types (``int`` / ``float``).
* ``float`` probability fields are rounded to 4 decimal places so that
  0.8 and 0.8000 hash identically.
* ``None`` and empty-string values are excluded from the output so that
  an absent filter and an explicitly null filter are indistinguishable.
* The output dict always has its keys in sorted order, so
  ``{"gender": "female", "page": 1}`` and ``{"page": 1, "gender": "female"}``
  produce the same JSON serialisation and therefore the same hash.

The result: identical logical queries always produce identical cache keys,
regardless of how they were constructed or capitalised by the caller.
"""

import hashlib
import json
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Field normalisation rules
# ---------------------------------------------------------------------------

# Each entry: field_name -> callable that converts a raw value to its
# canonical form, or returns None to signal "drop this field".
# Rules are only applied when the incoming value is not None.


def _str_lower(v: Any) -> Optional[str]:
    """Strip and lowercase a string value; drop empty strings."""
    s = str(v).strip().lower()
    return s if s else None


def _str_upper(v: Any) -> Optional[str]:
    """Strip and uppercase a string value (used for ISO country codes)."""
    s = str(v).strip().upper()
    return s if s else None


def _to_int(v: Any) -> Optional[int]:
    """Cast to int; return None if conversion fails."""
    try:
        return int(v)
    except (ValueError, TypeError):
        return None


def _to_float_4dp(v: Any) -> Optional[float]:
    """Cast to float and round to 4 decimal places."""
    try:
        return round(float(v), 4)
    except (ValueError, TypeError):
        return None


# Map of known profile filter fields to their normalisation callables.
_FIELD_RULES: dict = {
    "gender": _str_lower,
    "country_id": _str_upper,
    "age_group": _str_lower,
    "min_age": _to_int,
    "max_age": _to_int,
    "min_gender_probability": _to_float_4dp,
    "min_country_probability": _to_float_4dp,
    "sort_by": _str_lower,
    "order": _str_lower,
    "page": _to_int,
    "limit": _to_int,
}

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def normalize_filters(raw: dict) -> dict:
    """Return a normalised, sorted copy of *raw* — never mutates the input.

    Fields listed in ``_FIELD_RULES`` are transformed according to their
    individual rule.  Unrecognised fields are passed through with their
    original values (stripped of whitespace if they happen to be strings).
    ``None`` values and empty strings are always excluded from the output.

    The returned dict has its keys in alphabetical order so that
    ``json.dumps`` always produces a byte-for-byte identical serialisation
    for logically equivalent filter sets.

    Args:
        raw: Arbitrary dict of filter parameters as received from the caller.

    Returns:
        A new dict with normalised values and sorted keys, ready for hashing.
    """
    normalised: dict = {}

    for key, value in raw.items():
        # Always drop explicit None — treat it the same as "not provided"
        if value is None:
            continue

        rule = _FIELD_RULES.get(key)
        if rule is not None:
            canonical = rule(value)
        else:
            # Unknown field: at minimum strip whitespace from strings
            if isinstance(value, str):
                canonical: Any = value.strip() or None
            else:
                canonical = value

        # Drop falsy results from rules (None, empty string "")
        # but keep legitimate falsy numerics like 0
        if canonical is None:
            continue
        if canonical == "":
            continue

        normalised[key] = canonical

    # Return with sorted keys for a deterministic JSON serialisation
    return dict(sorted(normalised.items()))


def make_cache_key(prefix: str, params: dict) -> str:
    """Return a short, stable cache key for *prefix* + *params*.

    Applies :func:`normalize_filters` first so that logically equivalent
    filter dicts always hash to the same key, then serialises the result
    with ``json.dumps(sort_keys=True)`` and returns the first 16 hex digits
    of the SHA-256 digest.

    This function has **no dependency on** ``cache.py`` — it imports only
    the Python standard library so it can be used safely in any context
    (tests, scripts, background tasks) without side-effects.

    Example::

        make_cache_key("profiles", {"Gender": "Female ", "page": "1"})
        # -> "insighta:profiles:<16-char-hex>"

    Args:
        prefix: A short logical namespace, e.g. ``"profiles"`` or ``"stats"``.
        params: Raw (un-normalised) filter parameters.

    Returns:
        A Redis-friendly string of the form ``"insighta:{prefix}:{digest[:16]}"``.
    """
    normalised = normalize_filters(params)
    raw: str = json.dumps(normalised, sort_keys=True)
    digest: str = hashlib.sha256(raw.encode()).hexdigest()
    return f"insighta:{prefix}:{digest[:16]}"
