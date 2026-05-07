"""
Natural-language query parser for the Insighta Labs+ API.

``parse_query`` converts a free-text search string into a structured filter
dict consumed by ``routes/profiles.py::search_profiles``.  The function is
intentionally stateless and side-effect-free so it can be called freely from
any context (route handlers, tests, background tasks).

``normalize_list_params`` normalises the structured query-parameter dict sent
to the list endpoint so that the caching layer always generates an identical
cache key for logically equivalent requests.
"""

import re
from typing import Any, Optional

import pycountry

# ---------------------------------------------------------------------------
# Demonym → country name mapping
# ---------------------------------------------------------------------------

DEMONYMS: dict[str, str] = {
    "nigerian": "nigeria",
    "ghanaian": "ghana",
    "kenyan": "kenya",
    "south african": "south africa",
    "american": "united states",
    "british": "united kingdom",
    "canadian": "canada",
    "french": "france",
    "german": "germany",
    "italian": "italy",
    "spanish": "spain",
    "chinese": "china",
    "japanese": "japan",
    "indian": "india",
    "brazilian": "brazil",
    "australian": "australia",
    "egyptian": "egypt",
    "ethiopian": "ethiopia",
    "tanzanian": "tanzania",
    "ugandan": "uganda",
    "senegalese": "senegal",
    "cameroonian": "cameroon",
    "rwandan": "rwanda",
    "malawian": "malawi",
    "zambian": "zambia",
    "zimbabwean": "zimbabwe",
    "congolese": "congo",
}

# ---------------------------------------------------------------------------
# Compiled regex patterns (module-level for performance)
# ---------------------------------------------------------------------------

# Age range: "20-30", "aged 20 to 30", "ages 20–30", "between 25—45 years"
_AGE_RANGE_RE = re.compile(
    r"(?:aged?|ages?|between)?\s*(\d+)\s*[-\u2013\u2014to]+\s*(\d+)\s*(?:years?)?"
)

# "between X and Y" or "between ages X and Y" — allows 0–2 optional words
# between the keyword and the first number (e.g. "ages", "the ages of")
_BETWEEN_AND_RE = re.compile(r"between\s+(?:\w+\s+){0,2}(\d+)\s+and\s+(\d+)")

# "above / over N"
_ABOVE_RE = re.compile(r"(?:above|over)\s*(\d+)")

# "below / under N"
_BELOW_RE = re.compile(r"(?:below|under)\s*(\d+)")

# Country via preposition: "from", "in", "living in", "based in", "located in"
# Stop words prevent the lazy group from over-consuming sibling tokens.
_COUNTRY_PREP_RE = re.compile(
    r"(?:from|in|living\s+in|based\s+in|located\s+in)\s+([a-z][a-z\s]+?)(?:\s+(?:above|below|over|under|and|who|with|aged?|between)|\s*$)"
)

# ---------------------------------------------------------------------------
# Gender alias sets
# ---------------------------------------------------------------------------

_FEMALE_ALIASES = frozenset({"female", "females", "women", "woman", "girl", "girls"})
_MALE_ALIASES = frozenset({"male", "males", "men", "man", "boy", "boys"})

# ---------------------------------------------------------------------------
# Age-group keywords (checked in order; first match wins)
# ---------------------------------------------------------------------------

_AGE_GROUPS = ("child", "teenager", "adult", "senior")

# ---------------------------------------------------------------------------
# Fields cast to int during output normalisation
# ---------------------------------------------------------------------------

_INT_FIELDS = frozenset({"min_age", "max_age", "page", "limit"})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_query(query: str) -> Optional[dict[str, Any]]:
    """Parse a natural-language *query* string into a profile filter dict.

    Returns a dict with zero or more of the following keys::

        {
            "gender":     str,   # "male" | "female"
            "country_id": str,   # ISO alpha-2, e.g. "NG"
            "age_group":  str,   # "child" | "teenager" | "adult" | "senior"
            "min_age":    int,
            "max_age":    int,
        }

    Returns ``None`` when no recognised filters could be extracted.

    The function signature and filter key names are unchanged from the
    original implementation so that ``routes/profiles.py`` requires no
    modifications.
    """
    query = query.lower().strip()
    filters: dict[str, Any] = {}

    # ------------------------------------------------------------------
    # 1. Gender — aliases checked via whole-word token set
    # ------------------------------------------------------------------
    tokens = set(re.findall(r"\w+", query))
    if tokens & _FEMALE_ALIASES:
        filters["gender"] = "female"
    elif tokens & _MALE_ALIASES:
        filters["gender"] = "male"

    # ------------------------------------------------------------------
    # 2. Age range — explicit numeric range patterns first
    #    "young" is kept as a fallback only when no range is found.
    # ------------------------------------------------------------------
    age_range_m = _AGE_RANGE_RE.search(query)
    between_and_m = _BETWEEN_AND_RE.search(query)

    if age_range_m:
        a, b = int(age_range_m.group(1)), int(age_range_m.group(2))
        filters["min_age"] = min(a, b)
        filters["max_age"] = max(a, b)
    elif between_and_m:
        a, b = int(between_and_m.group(1)), int(between_and_m.group(2))
        filters["min_age"] = min(a, b)
        filters["max_age"] = max(a, b)
    elif "young" in query:
        filters["min_age"] = 16
        filters["max_age"] = 24

    # ------------------------------------------------------------------
    # 3. Age group keywords (child / teenager / adult / senior)
    # ------------------------------------------------------------------
    for group in _AGE_GROUPS:
        if group in query:
            filters["age_group"] = group
            break

    # ------------------------------------------------------------------
    # 4. above/over and below/under — override range bounds if present
    # ------------------------------------------------------------------
    above_m = _ABOVE_RE.search(query)
    if above_m:
        filters["min_age"] = int(above_m.group(1))

    below_m = _BELOW_RE.search(query)
    if below_m:
        filters["max_age"] = int(below_m.group(1))

    # ------------------------------------------------------------------
    # 5. Country detection
    #    5a. Demonym adjectives ("Nigerian", "British", …)
    #    5b. Preposition phrases ("from Kenya", "living in Spain", …)
    # ------------------------------------------------------------------
    country_found = False

    # 5a — demonym lookup (multi-word demonyms checked before single-word)
    for demonym in sorted(DEMONYMS, key=len, reverse=True):
        if re.search(r"\b" + re.escape(demonym) + r"\b", query):
            country_name = DEMONYMS[demonym]
            try:
                results = pycountry.countries.search_fuzzy(country_name)
                if results:
                    filters["country_id"] = results[0].alpha_2
                    country_found = True
                    break
            except LookupError:
                pass

    # 5b — preposition-based extraction
    if not country_found:
        country_m = _COUNTRY_PREP_RE.search(query)
        if country_m:
            country_query = country_m.group(1).strip()
            try:
                results = pycountry.countries.search_fuzzy(country_query)
                if results:
                    filters["country_id"] = results[0].alpha_2
            except LookupError:
                pass

    # ------------------------------------------------------------------
    # 6. Output normalisation — canonical types and casing
    # ------------------------------------------------------------------
    if "gender" in filters:
        filters["gender"] = str(filters["gender"]).lower()
    if "country_id" in filters:
        filters["country_id"] = str(filters["country_id"]).upper()
    if "age_group" in filters:
        filters["age_group"] = str(filters["age_group"]).lower()
    for field in ("min_age", "max_age"):
        if field in filters:
            filters[field] = int(filters[field])

    return filters if filters else None


# ---------------------------------------------------------------------------
# Structured list-endpoint param normalisation (used by the caching layer)
# ---------------------------------------------------------------------------

_LIST_STR_LOWER_FIELDS = frozenset({"gender", "age_group", "sort_by", "order"})


def normalize_list_params(params: dict[str, Any]) -> dict[str, Any]:
    """Normalise structured list-endpoint query parameters for cache-key building.

    Takes the raw dict of query params (values may be strings, ints, ``None``,
    or ``float``) and returns a cleaned dict suitable for deterministic
    hashing:

    - ``None`` and ``""`` values are dropped.
    - ``gender``, ``age_group``, ``sort_by``, ``order`` are lowercased.
    - ``country_id`` is uppercased (ISO alpha-2).
    - ``min_age``, ``max_age``, ``page``, ``limit`` are cast to ``int``.
    - All other string values are stripped of surrounding whitespace.
    - The original dict is never mutated.

    Args:
        params: Raw query-parameter dict as received by the list route.

    Returns:
        A new normalised dict (keys are NOT sorted here — sorting is the
        responsibility of the hashing step in ``services/cache.py``).
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
                pass  # drop values that cannot be cast
        elif k in _LIST_STR_LOWER_FIELDS:
            s = str(v).strip().lower()
            if s:
                normalised[k] = s
        elif isinstance(v, str):
            s = v.strip()
            if s:
                normalised[k] = s
        else:
            normalised[k] = v
    return normalised
