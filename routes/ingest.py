"""
routes/ingest.py – Bulk CSV upload endpoint for profile records.

POST /profiles/upload
    • Authenticated (any role)
    • Rate-limited: 10 requests / minute
    • Reads the uploaded file once into memory, then drives a lazy
      csv.DictReader so row dicts are not all materialised at once.
    • Validates each row individually; invalid rows are skipped and
      their failure reason is recorded.
    • Accumulates valid rows into batches of CHUNK_SIZE (1 000) and
      bulk-inserts each batch with PostgreSQL's INSERT … ON CONFLICT DO NOTHING.
    • Duplicate names (UNIQUE index violation) are counted via
      len(batch) - result.rowcount rather than a pre-flight SELECT.
    • Commits per chunk so partial failures preserve already-inserted rows.
    • Invalidates the query cache after the upload completes.
"""

import csv
import io
import logging
from datetime import datetime, timezone
from typing import Any

import pycountry
from database import get_db
from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import JSONResponse
from limiter import limiter
from middleware.auth import get_current_user
from models.profile import Profile
from services.cache import delete_by_prefix
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session
from uuid6 import uuid7

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/profiles")

CHUNK_SIZE = 1_000
VALID_GENDERS = {"male", "female"}

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def derive_age_group(age: int) -> str:
    """Map a numeric age to its canonical age-group label."""
    if age < 13:
        return "child"
    elif age < 18:
        return "teenager"
    elif age < 60:
        return "adult"
    else:
        return "senior"


def get_country_name(alpha_2: str) -> str:
    """Return the full country name for an ISO 3166-1 alpha-2 code.

    Falls back to the raw code string if pycountry has no match.
    """
    try:
        country = pycountry.countries.get(alpha_2=alpha_2.upper())
        return country.name if country else alpha_2
    except Exception:
        return alpha_2


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.post("/upload", dependencies=[Depends(get_current_user)])
@limiter.limit("10/minute")
async def upload_profiles(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Bulk-ingest a CSV file of profile records.

    The file must have a header row containing at least the columns:
    ``name``, ``gender``, ``age``, ``country_id``.

    Optional columns: ``gender_probability``, ``country_name``,
    ``country_probability``.

    Returns a JSON summary of the operation:
    ::

        {
            "status":     "success",
            "total_rows": <int>,
            "inserted":   <int>,
            "skipped":    <int>,
            "reasons":    { <reason_key>: <count>, ... }
        }

    Only reason keys with a count > 0 are included.
    """

    # ------------------------------------------------------------------
    # 1. Read the file content
    # ------------------------------------------------------------------
    try:
        raw_bytes: bytes = await file.read()
    except Exception as exc:
        logger.error("upload_profiles: failed to read uploaded file – %s", exc)
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "Could not read the uploaded file."},
        )

    # ------------------------------------------------------------------
    # 2. Decode to Unicode string
    # ------------------------------------------------------------------
    text: str = raw_bytes.decode("utf-8", errors="replace")

    # Strip the UTF-8 BOM that Excel sometimes inserts
    if text.startswith("\ufeff"):
        text = text[1:]

    # ------------------------------------------------------------------
    # 3. Fast-path: empty file
    # ------------------------------------------------------------------
    if not text.strip():
        return {
            "status": "success",
            "total_rows": 0,
            "inserted": 0,
            "skipped": 0,
            "reasons": {},
        }

    # ------------------------------------------------------------------
    # 4. Build the lazy DictReader
    # ------------------------------------------------------------------
    try:
        reader = csv.DictReader(io.StringIO(text))
        # Access fieldnames eagerly so a completely header-less file is
        # caught before we enter the row loop.
        _ = reader.fieldnames
    except Exception as exc:
        logger.error("upload_profiles: failed to parse CSV – %s", exc)
        return JSONResponse(
            status_code=400,
            content={"status": "error", "message": "The file is not valid CSV."},
        )

    # ------------------------------------------------------------------
    # 5. Counters and accumulators
    # ------------------------------------------------------------------
    total_rows: int = 0
    inserted: int = 0
    reasons: dict[str, int] = {}
    valid_batch: list[dict[str, Any]] = []

    def _add_reason(key: str) -> None:
        reasons[key] = reasons.get(key, 0) + 1

    # ------------------------------------------------------------------
    # 6. Batch-flush helper (synchronous – uses a sync SQLAlchemy session)
    # ------------------------------------------------------------------
    def _flush_batch() -> None:
        """Bulk-insert the current batch; counts duplicates via rowcount."""
        nonlocal inserted, valid_batch

        if not valid_batch:
            return

        try:
            stmt = pg_insert(Profile.__table__).values(valid_batch)
            stmt = stmt.on_conflict_do_nothing(index_elements=["name"])
            result = db.execute(stmt)
            db.commit()

            batch_inserted: int = result.rowcount
            batch_duplicates: int = len(valid_batch) - batch_inserted

            inserted += batch_inserted
            if batch_duplicates > 0:
                # Accumulate the exact count directly – no need for _add_reason here
                # because we already know the precise duplicate tally from rowcount.
                reasons["duplicate_name"] = (
                    reasons.get("duplicate_name", 0) + batch_duplicates
                )
        except Exception as exc:
            logger.error("upload_profiles: batch insert error – %s", exc)
            db.rollback()
            # Rows in this batch will be reflected in `skipped` via
            # total_rows - inserted, which is intentional per spec.

        valid_batch = []

    # ------------------------------------------------------------------
    # 7. Row iteration and validation
    # ------------------------------------------------------------------
    try:
        for row in reader:
            total_rows += 1

            # ---- 7a. Malformed: extra columns (DictReader puts them under
            #          the None restkey) ---------------------------------
            if None in row:
                _add_reason("malformed")
                continue

            # ---- 7b. Missing required fields ---------------------------
            name_raw = (row.get("name") or "").strip()
            gender_raw = (row.get("gender") or "").strip()
            age_raw = (row.get("age") or "").strip()
            country_id_raw = (row.get("country_id") or "").strip()

            if not name_raw or not gender_raw or not age_raw or not country_id_raw:
                _add_reason("missing_fields")
                continue

            # ---- 7c. Age validation ------------------------------------
            try:
                age = int(age_raw)
            except (ValueError, TypeError):
                _add_reason("invalid_age")
                continue

            if age < 0 or age > 150:
                _add_reason("invalid_age")
                continue

            # ---- 7d. Gender validation ----------------------------------
            gender = gender_raw.lower()
            if gender not in VALID_GENDERS:
                _add_reason("invalid_gender")
                continue

            # ---- 7e. Country-ID validation ------------------------------
            country_id = country_id_raw.upper()
            if len(country_id) != 2 or not country_id.isalpha():
                _add_reason("invalid_country")
                continue

            # ---- 7f. Optional float fields (clamp to [0.0, 1.0]) --------
            gp_raw = (row.get("gender_probability") or "").strip()
            try:
                gender_probability = (
                    max(0.0, min(1.0, float(gp_raw))) if gp_raw else 1.0
                )
            except (ValueError, TypeError):
                gender_probability = 1.0

            cp_raw = (row.get("country_probability") or "").strip()
            try:
                country_probability = (
                    max(0.0, min(1.0, float(cp_raw))) if cp_raw else 1.0
                )
            except (ValueError, TypeError):
                country_probability = 1.0

            # ---- 7g. Derived / looked-up fields -------------------------
            age_group = derive_age_group(age)

            cn_raw = (row.get("country_name") or "").strip()
            country_name = cn_raw if cn_raw else get_country_name(country_id)

            # ---- 7h. Accumulate validated row ---------------------------
            valid_batch.append(
                {
                    "id": str(uuid7()),
                    "name": name_raw.lower(),
                    "gender": gender,
                    "gender_probability": gender_probability,
                    "age": age,
                    "age_group": age_group,
                    "country_id": country_id,
                    "country_name": country_name,
                    "country_probability": country_probability,
                    "created_at": datetime.now(timezone.utc),
                }
            )

            # ---- 7i. Flush when the batch is full -----------------------
            if len(valid_batch) >= CHUNK_SIZE:
                _flush_batch()

    except Exception as exc:
        logger.error("upload_profiles: unexpected error during CSV iteration – %s", exc)
        # Flush whatever we managed to accumulate before the crash
        _flush_batch()
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": "Error while processing the CSV file.",
            },
        )

    # ------------------------------------------------------------------
    # 8. Final flush for the last (possibly partial) batch
    # ------------------------------------------------------------------
    _flush_batch()

    # ------------------------------------------------------------------
    # 9. Cache invalidation
    # ------------------------------------------------------------------
    for prefix in ("profiles:list", "profiles:search", "profiles:count"):
        try:
            delete_by_prefix(prefix)
        except Exception as exc:
            logger.warning(
                "upload_profiles: cache invalidation failed for '%s' – %s", prefix, exc
            )

    # ------------------------------------------------------------------
    # 10. Build and return the summary
    # ------------------------------------------------------------------
    skipped = total_rows - inserted

    return {
        "status": "success",
        "total_rows": total_rows,
        "inserted": inserted,
        "skipped": skipped,
        # Spec: only include keys with count > 0
        "reasons": {k: v for k, v in reasons.items() if v > 0},
    }
