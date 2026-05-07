import csv
import logging
from datetime import datetime, timezone

import pycountry
from fastapi import UploadFile
from models.profile import Profile
from sqlalchemy.orm import Session
from uuid6 import uuid7

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CHUNK_SIZE = 1000  # rows per DB batch
READ_SIZE = 64 * 1024  # 64 KB per file read

VALID_AGE_GROUPS = {"child", "teenager", "adult", "senior"}

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _derive_age_group(age: int) -> str:
    """Return the canonical age-group label for *age*."""
    if age < 13:
        return "child"
    if age < 18:
        return "teenager"
    if age < 65:
        return "adult"
    return "senior"


def _lookup_country_name(country_id: str) -> str:
    """Return the full country name for an ISO alpha-2 code, or the code itself."""
    try:
        result = pycountry.countries.get(alpha_2=country_id)
        if result:
            return result.name
    except Exception:
        pass
    return country_id


# ---------------------------------------------------------------------------
# Row validation
# ---------------------------------------------------------------------------


def _validate_row(row: dict) -> tuple:
    """
    Validate one CSV row dict.

    Returns
    -------
    (cleaned_dict, None)   on success
    (None, reason_str)     on failure
    """
    required = {"name", "gender", "age", "country_id"}

    # Presence check (value must be non-empty after strip)
    present = {k for k, v in row.items() if v is not None and str(v).strip()}
    if not required.issubset(present):
        return None, "missing_fields"

    name = str(row["name"]).strip().lower()
    gender = str(row["gender"]).strip().lower()
    age_raw = str(row["age"]).strip()
    country_id = str(row["country_id"]).strip().upper()

    # name must be non-empty after normalisation
    if not name:
        return None, "missing_fields"

    # --- age ---
    try:
        age = int(age_raw)
    except (ValueError, TypeError):
        return None, "invalid_age"
    if age < 0 or age > 150:
        return None, "invalid_age"

    # --- gender ---
    if gender not in {"male", "female"}:
        return None, "invalid_gender"

    # --- country_id: exactly 2 ASCII letters ---
    if len(country_id) != 2 or not country_id.isalpha():
        return None, "invalid_country"

    # --- optional floats ---
    gender_probability = 1.0
    gp_raw = row.get("gender_probability") or ""
    if str(gp_raw).strip():
        try:
            gender_probability = float(str(gp_raw).strip())
        except (ValueError, TypeError):
            pass  # keep default

    country_probability = 1.0
    cp_raw = row.get("country_probability") or ""
    if str(cp_raw).strip():
        try:
            country_probability = float(str(cp_raw).strip())
        except (ValueError, TypeError):
            pass  # keep default

    # --- age_group: use provided if valid, otherwise derive ---
    ag_raw = (row.get("age_group") or "").strip().lower()
    age_group = ag_raw if ag_raw in VALID_AGE_GROUPS else _derive_age_group(age)

    # --- country_name: use provided if non-empty, otherwise look up ---
    cn_raw = (row.get("country_name") or "").strip()
    country_name = cn_raw if cn_raw else _lookup_country_name(country_id)

    cleaned = {
        "id": str(uuid7()),
        "name": name,
        "gender": gender,
        "gender_probability": gender_probability,
        "age": age,
        "age_group": age_group,
        "country_id": country_id,
        "country_name": country_name,
        "country_probability": country_probability,
        "created_at": datetime.now(timezone.utc),
    }
    return cleaned, None


# ---------------------------------------------------------------------------
# DB batch flush
# ---------------------------------------------------------------------------


def _flush_batch(db: Session, batch: list[dict], reasons: dict) -> tuple[int, int]:
    """
    Persist *batch* to the database.

    Steps
    -----
    1. One SELECT to find names that already exist in the table.
    2. Exclude those from the insert list.
    3. Bulk-insert the rest; on any exception roll back that batch only.

    Returns
    -------
    (inserted_count, skipped_count)
    """
    if not batch:
        return 0, 0

    chunk_names = [row["name"] for row in batch]

    # --- resolve DB-level duplicates in one round-trip ---
    try:
        existing_names: set[str] = {
            r[0]
            for r in db.query(Profile.name).filter(Profile.name.in_(chunk_names)).all()
        }
    except Exception as exc:
        logger.error("Error querying existing names: %s", exc)
        existing_names = set()

    to_insert: list[dict] = []
    dup_count = 0

    for row in batch:
        if row["name"] in existing_names:
            dup_count += 1
        else:
            to_insert.append(row)

    if dup_count:
        reasons["duplicate_name"] = reasons.get("duplicate_name", 0) + dup_count

    if not to_insert:
        return 0, dup_count

    # --- bulk insert ---
    try:
        db.execute(Profile.__table__.insert(), to_insert)
        db.commit()
        return len(to_insert), dup_count
    except Exception as exc:
        db.rollback()
        logger.error("Batch insert error: %s", exc)
        err_count = len(to_insert)
        reasons["insert_error"] = reasons.get("insert_error", 0) + err_count
        return 0, dup_count + err_count


# ---------------------------------------------------------------------------
# Main entry-point
# ---------------------------------------------------------------------------


async def process_csv_upload(file: UploadFile, db: Session) -> dict:
    """
    Stream-parse *file* as CSV and bulk-insert valid rows into the DB.

    The file is never loaded entirely into memory; it is read in 64 KB chunks.
    Rows are validated one by one and accumulated into batches of CHUNK_SIZE
    before each DB round-trip.

    Returns
    -------
    {
        "status":     "success",
        "total_rows": <int>,   # every data row attempted (header excluded)
        "inserted":   <int>,
        "skipped":    <int>,
        "reasons":    {<reason_str>: <count>, ...}
    }
    """
    total_rows: int = 0
    inserted: int = 0
    skipped: int = 0
    reasons: dict = {}

    # --- streaming state ---
    partial_line: bytes = b""
    first_chunk: bool = True
    csv_header: list | None = None

    # --- current accumulation batch ---
    batch: list[dict] = []
    seen_in_batch: set[str] = set()

    # ------------------------------------------------------------------
    # Inner helpers (closures over the mutable state above)
    # ------------------------------------------------------------------

    def _add_reason(reason: str) -> None:
        reasons[reason] = reasons.get(reason, 0) + 1

    async def _maybe_flush() -> None:
        """Flush the current batch to the DB if it has reached CHUNK_SIZE."""
        nonlocal inserted, skipped, batch, seen_in_batch
        if len(batch) >= CHUNK_SIZE:
            ins, sk = _flush_batch(db, batch, reasons)
            inserted += ins
            skipped += sk
            batch = []
            seen_in_batch = set()

    async def _handle_data_line(line: str) -> None:
        """Parse, validate, and accumulate one CSV data line."""
        nonlocal total_rows, skipped

        if not line.strip():
            return  # blank line — skip silently

        total_rows += 1

        # Parse the raw text line through csv.reader so quoted fields
        # and escaped commas are handled correctly.
        try:
            row_values = next(csv.reader([line]))
        except Exception:
            skipped += 1
            _add_reason("malformed_row")
            return

        # Column-count mismatch is also a malformed row
        if len(row_values) != len(csv_header):  # type: ignore[arg-type]
            skipped += 1
            _add_reason("malformed_row")
            return

        row = dict(zip(csv_header, row_values))  # type: ignore[arg-type]

        cleaned, reason = _validate_row(row)
        if reason:
            skipped += 1
            _add_reason(reason)
            return

        # Intra-batch duplicate detection
        assert cleaned is not None  # narrowing for type checkers
        if cleaned["name"] in seen_in_batch:
            skipped += 1
            _add_reason("duplicate_name")
            return

        seen_in_batch.add(cleaned["name"])
        batch.append(cleaned)
        await _maybe_flush()

    # ------------------------------------------------------------------
    # Streaming read loop
    # ------------------------------------------------------------------

    while True:
        chunk: bytes = await file.read(READ_SIZE)

        # --- end of file ---
        if not chunk:
            # Flush any leftover partial line
            if partial_line:
                line = partial_line.decode("utf-8", errors="replace").rstrip("\r")
                if csv_header is None:
                    # Edge-case: single-line file (header only, no data)
                    try:
                        csv_header = list(next(csv.reader([line])))
                    except Exception:
                        pass
                else:
                    await _handle_data_line(line)
            break

        # --- strip UTF-8 BOM on the very first chunk ---
        if first_chunk:
            if chunk.startswith(b"\xef\xbb\xbf"):
                chunk = chunk[3:]
            first_chunk = False

        # --- combine with leftover bytes from previous chunk ---
        combined = partial_line + chunk
        lines = combined.split(b"\n")
        partial_line = lines[-1]  # last element: incomplete line (may be b"")
        complete_lines = lines[:-1]

        for line_bytes in complete_lines:
            line = line_bytes.decode("utf-8", errors="replace").rstrip("\r")

            if csv_header is None:
                # First non-empty complete line is the header
                if not line.strip():
                    continue
                try:
                    csv_header = list(next(csv.reader([line])))
                except Exception:
                    logger.error("Failed to parse CSV header line: %r", line)
                    return {
                        "status": "success",
                        "total_rows": 0,
                        "inserted": 0,
                        "skipped": 0,
                        "reasons": {"malformed_header": 1},
                    }
                continue  # header consumed — move on to data rows

            await _handle_data_line(line)

    # ------------------------------------------------------------------
    # Final batch flush
    # ------------------------------------------------------------------
    if batch:
        ins, sk = _flush_batch(db, batch, reasons)
        inserted += ins
        skipped += sk

    return {
        "status": "success",
        "total_rows": total_rows,
        "inserted": inserted,
        "skipped": skipped,
        "reasons": reasons,
    }
