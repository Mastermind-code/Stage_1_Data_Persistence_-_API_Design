"""
Stage 4B — Manual test script
Tests all three implemented features against the live deployment.

Usage:
    python test_stage4b.py

The script fetches its own test token so you don't need to paste anything.
"""

import csv
import io
import json
import time

import requests

BASE = "https://stage-1-data-persistence-api-design.vercel.app/api/v1"
HEADERS = {"X-API-Version": "1"}


# ── helpers ────────────────────────────────────────────────────────────────


def get_token() -> str:
    """Fetch a fresh admin test token."""
    r = requests.get(f"{BASE}/auth/github/callback", params={"code": "test_code"})
    r.raise_for_status()
    token = r.json()["access_token"]
    print(f"  token: {token[:40]}...")
    return token


def auth(token: str) -> dict:
    return {**HEADERS, "Authorization": f"Bearer {token}"}


def sep(title: str):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print("=" * 60)


def ok(label: str, value=None):
    suffix = f"  →  {value}" if value is not None else ""
    print(f"  ✓  {label}{suffix}")


def fail(label: str, detail=None):
    suffix = f"  →  {detail}" if detail is not None else ""
    print(f"  ✗  {label}{suffix}")


# ── Test 1: Query performance (cache miss vs cache hit) ────────────────────


def test_performance(token: str):
    sep("TEST 1 — Query Performance (cache miss vs cache hit)")

    params = {"gender": "male", "country_id": "NG", "limit": 10}

    # First request — cache miss, hits the database
    t0 = time.perf_counter()
    r1 = requests.get(f"{BASE}/profiles", headers=auth(token), params=params)
    t1 = (time.perf_counter() - t0) * 1000

    if r1.status_code != 200:
        fail("First request failed", f"{r1.status_code} {r1.text[:200]}")
        return
    ok(f"1st request (cache miss / DB hit)", f"{t1:.0f} ms  —  status {r1.status_code}")

    # Second request — should be a cache hit (Redis)
    t0 = time.perf_counter()
    r2 = requests.get(f"{BASE}/profiles", headers=auth(token), params=params)
    t2 = (time.perf_counter() - t0) * 1000

    ok(f"2nd request (cache hit / no DB)", f"{t2:.0f} ms  —  status {r2.status_code}")

    # Compare and verify results are identical
    if r1.json().get("data") == r2.json().get("data"):
        ok("Results are identical on both requests (cache is correct)")
    else:
        fail("Results differ between cached and uncached responses!")

    speedup = t1 / t2 if t2 > 0 else float("inf")
    print(f"\n  Summary: {t1:.0f}ms → {t2:.0f}ms  ({speedup:.1f}x faster on cache hit)")

    # Third request with a different filter to show DB is still working
    t0 = time.perf_counter()
    r3 = requests.get(
        f"{BASE}/profiles",
        headers=auth(token),
        params={"gender": "female", "country_id": "GH", "limit": 10},
    )
    t3 = (time.perf_counter() - t0) * 1000
    ok(f"3rd request (different filter — new cache miss)", f"{t3:.0f} ms")


# ── Test 2: Query normalisation ────────────────────────────────────────────


def test_normalisation(token: str):
    sep("TEST 2 — Query Normalisation (equivalent queries → same results)")

    query_pairs = [
        (
            "Nigerian females between ages 20 and 45",
            "Women aged 20-45 living in Nigeria",
        ),
        (
            "young males from kenya",
            "men aged 16-24 in Kenya",
        ),
        (
            "adult males",
            "men adult",
        ),
    ]

    for q1, q2 in query_pairs:
        r1 = requests.get(
            f"{BASE}/profiles/search", headers=auth(token), params={"q": q1}
        )
        r2 = requests.get(
            f"{BASE}/profiles/search", headers=auth(token), params={"q": q2}
        )

        if r1.status_code not in (200, 422) or r2.status_code not in (200, 422):
            fail(
                f"Unexpected status",
                f"'{q1}' → {r1.status_code}, '{q2}' → {r2.status_code}",
            )
            continue

        d1 = r1.json()
        d2 = r2.json()

        # Both uninterpretable
        if r1.status_code == 422 and r2.status_code == 422:
            ok(f"Both queries correctly returned 422 (uninterpretable)")
            continue

        # Compare totals (same filter → same total row count)
        total1 = d1.get("total")
        total2 = d2.get("total")

        if total1 == total2:
            ok(f"Same total ({total1}) for both queries")
            print(f'     Q1: "{q1}"')
            print(f'     Q2: "{q2}"')
        else:
            fail(f"Different totals: {total1} vs {total2}")
            print(f'     Q1: "{q1}"')
            print(f'     Q2: "{q2}"')

        # Second call should now be faster (cache hit from first call)
        t0 = time.perf_counter()
        requests.get(f"{BASE}/profiles/search", headers=auth(token), params={"q": q1})
        t_cached = (time.perf_counter() - t0) * 1000
        ok(f"  Re-running Q1 (cache hit): {t_cached:.0f}ms")


# ── Test 3: CSV ingestion ──────────────────────────────────────────────────


def make_csv(rows: list[dict]) -> bytes:
    """Build a CSV file as bytes from a list of row dicts."""
    fields = [
        "name",
        "gender",
        "age",
        "country_id",
        "gender_probability",
        "country_probability",
    ]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode()


def test_ingestion_happy_path(token: str):
    sep("TEST 3a — CSV Ingestion: valid rows")

    rows = [
        {
            "name": f"test_upload_user_{i:04d}",
            "gender": "male",
            "age": 25 + (i % 30),
            "country_id": "NG",
            "gender_probability": 0.95,
            "country_probability": 0.90,
        }
        for i in range(50)
    ]

    csv_bytes = make_csv(rows)
    files = {"file": ("test_upload.csv", csv_bytes, "text/csv")}

    t0 = time.perf_counter()
    r = requests.post(f"{BASE}/profiles/upload", headers=auth(token), files=files)
    elapsed = (time.perf_counter() - t0) * 1000

    if r.status_code != 200:
        fail(f"Upload failed", f"{r.status_code} — {r.text[:300]}")
        return

    data = r.json()
    print(f"\n  Response ({elapsed:.0f}ms):")
    print(json.dumps(data, indent=4))

    assert data["status"] == "success", "status should be success"
    assert data["total_rows"] == 50, f"expected 50 rows, got {data['total_rows']}"
    ok(f"total_rows = {data['total_rows']}")
    ok(f"inserted   = {data['inserted']}")
    ok(f"skipped    = {data['skipped']}")
    if data.get("reasons"):
        ok(f"reasons    = {data['reasons']}")


def test_ingestion_with_bad_rows(token: str):
    sep("TEST 3b — CSV Ingestion: mixed valid + invalid rows")

    rows = [
        # Valid
        {
            "name": "valid_upload_alice",
            "gender": "female",
            "age": 28,
            "country_id": "GH",
        },
        {"name": "valid_upload_bob", "gender": "male", "age": 34, "country_id": "KE"},
        # Invalid age (negative)
        {"name": "bad_age_user", "gender": "male", "age": -5, "country_id": "NG"},
        # Invalid gender
        {"name": "bad_gender_user", "gender": "unknown", "age": 22, "country_id": "NG"},
        # Missing country_id
        {"name": "missing_country", "gender": "female", "age": 30, "country_id": ""},
        # Valid
        {
            "name": "valid_upload_charlie",
            "gender": "male",
            "age": 41,
            "country_id": "ZA",
        },
    ]

    csv_bytes = make_csv(rows)
    files = {"file": ("mixed.csv", csv_bytes, "text/csv")}

    r = requests.post(f"{BASE}/profiles/upload", headers=auth(token), files=files)

    if r.status_code != 200:
        fail(f"Upload failed", f"{r.status_code} — {r.text[:300]}")
        return

    data = r.json()
    print(f"\n  Response:")
    print(json.dumps(data, indent=4))

    assert data["total_rows"] == 6, f"expected 6, got {data['total_rows']}"
    assert data["skipped"] >= 3, f"expected at least 3 skipped, got {data['skipped']}"
    ok(f"total_rows correctly = {data['total_rows']}")
    ok(f"skipped correctly >= 3  (got {data['skipped']})")
    ok(f"reasons breakdown  = {data.get('reasons', {})}")


def test_ingestion_duplicate_names(token: str):
    sep("TEST 3c — CSV Ingestion: duplicate names are skipped, not errored")

    # Upload the same two names twice — second run should insert 0, skip 2
    rows = [
        {
            "name": "duplicate_test_dave",
            "gender": "male",
            "age": 29,
            "country_id": "NG",
        },
        {
            "name": "duplicate_test_eve",
            "gender": "female",
            "age": 26,
            "country_id": "GH",
        },
    ]
    csv_bytes = make_csv(rows)

    # First upload
    r1 = requests.post(
        f"{BASE}/profiles/upload",
        headers=auth(token),
        files={"file": ("dup1.csv", csv_bytes, "text/csv")},
    )

    # Second upload — same rows
    r2 = requests.post(
        f"{BASE}/profiles/upload",
        headers=auth(token),
        files={"file": ("dup2.csv", csv_bytes, "text/csv")},
    )

    d1, d2 = r1.json(), r2.json()

    print(
        f"\n  First upload:  inserted={d1.get('inserted')}, skipped={d1.get('skipped')}"
    )
    print(
        f"  Second upload: inserted={d2.get('inserted')}, skipped={d2.get('skipped')}"
    )

    if d2.get("inserted") == 0 and d2.get("skipped") == 2:
        ok("Duplicate names correctly skipped on second upload (not errored)")
        ok(f"reasons = {d2.get('reasons', {})}")
    else:
        fail("Unexpected duplicate handling", d2)


def test_ingestion_empty_file(token: str):
    sep("TEST 3d — CSV Ingestion: empty file")

    files = {"file": ("empty.csv", b"", "text/csv")}
    r = requests.post(f"{BASE}/profiles/upload", headers=auth(token), files=files)

    data = r.json()
    print(f"\n  Response: {data}")

    if data.get("total_rows") == 0:
        ok("Empty file returns total_rows=0 (fast-path works)")
    else:
        fail("Unexpected response for empty file", data)


# ── Run all tests ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("\nInsighta Labs+ — Stage 4B Test Suite")
    print(f"Target: {BASE}\n")

    print("Fetching test token...")
    token = get_token()

    test_performance(token)

    # Refresh token halfway through (access token expires in 3 min)
    print("\nRefreshing token...")
    token = get_token()

    test_normalisation(token)

    print("\nRefreshing token...")
    token = get_token()

    test_ingestion_happy_path(token)
    test_ingestion_with_bad_rows(token)
    test_ingestion_duplicate_names(token)
    test_ingestion_empty_file(token)

    sep("ALL TESTS COMPLETE")
