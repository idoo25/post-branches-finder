"""
Build the post-branches SQLite database from the verified JSON file.
Idempotent — safe to re-run; rebuilds the branch tables in a transaction.

Usage:
    python build_db.py [path-to-json]   # default: israelpost_branches_full.json in Downloads
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "post_branches.db"
SCHEMA_PATH = HERE / "schema.sql"
SEED_PROVIDERS_PATH = HERE / "seed_providers.sql"
DEFAULT_JSON = Path.home() / "Downloads" / "israelpost_branches_full.json"


def open_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def apply_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.executescript(SEED_PROVIDERS_PATH.read_text(encoding="utf-8"))


def compose_full_address(b: dict) -> str:
    parts = []
    if b.get("street") and b.get("house"):
        parts.append(f"{b['street']} {b['house']}")
    elif b.get("street"):
        parts.append(b["street"])
    if b.get("city"):
        parts.append(b["city"])
    addr = ", ".join(parts) if parts else (b.get("city") or b["branch_name"])
    if b.get("address_extra"):
        addr += f" ({b['address_extra']})"
    return addr


def load_branches(conn: sqlite3.Connection, branches: list[dict]) -> dict:
    cur = conn.cursor()
    # Wipe branch-derived child tables (services dictionary kept). None of these
    # tables are referenced by travel_time_cache's FK, so fully clearing and
    # reinserting them on every rebuild is harmless.
    for tbl in ("branch_hours", "branch_services", "branch_extra_services",
                "branch_accessibility", "branches_rtree"):
        cur.execute(f"DELETE FROM {tbl}")

    # IMPORTANT: do NOT "DELETE FROM branches" unconditionally here.
    # travel_time_cache.branch_number references branches(branch_number)
    # ON DELETE CASCADE — deleting every branch row (even to immediately
    # reinsert identical data) silently wipes the entire travel-time cache,
    # including still-valid ORS/OSRM entries within their 7-day TTL, on
    # every single rebuild. Instead we diff against what's already on disk:
    # only branch_numbers truly absent from the new dataset (real closures)
    # get deleted; everything else is UPSERTed in place below so its row
    # (and thus its cascade-linked cache rows) is never removed.
    new_branch_numbers = {int(b["branch_number"]) for b in branches}
    cur.execute("SELECT branch_number FROM branches")
    existing_branch_numbers = {row[0] for row in cur.fetchall()}
    removed_branch_numbers = existing_branch_numbers - new_branch_numbers
    if removed_branch_numbers:
        cur.executemany(
            "DELETE FROM branches WHERE branch_number = ?",
            [(bn,) for bn in removed_branch_numbers],
        )

    # Build a unique services dictionary from the data
    seen_services: dict[int, dict] = {}
    for b in branches:
        for s in b.get("services", []):
            if s.get("name"):
                sid = int(s["id"])
                if sid not in seen_services:
                    seen_services[sid] = {
                        "id": sid,
                        "name": s["name"],
                        "category": s.get("category"),
                    }
    cur.executemany(
        "INSERT OR REPLACE INTO services(service_id, service_name, category_name) VALUES (?, ?, ?)",
        [(s["id"], s["name"], s["category"]) for s in seen_services.values()],
    )

    inserted = 0
    for b in branches:
        bn = int(b["branch_number"])
        full_addr = compose_full_address(b)
        lat = float(b["latitude"])
        lng = float(b["longitude"])
        lat_rad = math.radians(lat)
        lng_rad = math.radians(lng)
        cur.execute(
            """INSERT INTO branches
               (branch_number, branch_name, branch_type, region, area, city,
                street, house, zip, address_extra, full_address,
                latitude, longitude, lat_rad, lng_rad, sin_lat, cos_lat, telephone)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(branch_number) DO UPDATE SET
                   branch_name   = excluded.branch_name,
                   branch_type   = excluded.branch_type,
                   region        = excluded.region,
                   area          = excluded.area,
                   city          = excluded.city,
                   street        = excluded.street,
                   house         = excluded.house,
                   zip           = excluded.zip,
                   address_extra = excluded.address_extra,
                   full_address  = excluded.full_address,
                   latitude      = excluded.latitude,
                   longitude     = excluded.longitude,
                   lat_rad       = excluded.lat_rad,
                   lng_rad       = excluded.lng_rad,
                   sin_lat       = excluded.sin_lat,
                   cos_lat       = excluded.cos_lat,
                   telephone     = excluded.telephone""",
            (
                bn, b["branch_name"], b.get("branch_type"), b.get("region"),
                b.get("area"), b.get("city"), b.get("street"),
                int(b["house"]) if b.get("house") not in (None, "") else None,
                b.get("zip"), b.get("address_extra"),
                full_addr, lat, lng, lat_rad, lng_rad,
                math.sin(lat_rad), math.cos(lat_rad),
                b.get("telephone") or None,
            ),
        )
        cur.execute(
            "INSERT INTO branches_rtree(branch_number, min_lat, max_lat, min_lng, max_lng) VALUES (?, ?, ?, ?, ?)",
            (bn, b["latitude"], b["latitude"], b["longitude"], b["longitude"]),
        )

        for s in b.get("services", []):
            cur.execute(
                "INSERT OR IGNORE INTO branch_services(branch_number, service_id) VALUES (?, ?)",
                (bn, int(s["id"])),
            )

        for ex in (b.get("extra_services") or []):
            cur.execute(
                "INSERT OR IGNORE INTO branch_extra_services(branch_number, extra) VALUES (?, ?)",
                (bn, ex),
            )

        for acc in (b.get("accessibility") or []):
            cur.execute(
                "INSERT OR IGNORE INTO branch_accessibility(branch_number, accessibility_type) VALUES (?, ?)",
                (bn, acc),
            )

        for h in b.get("hours", []):
            morning_open = morning_close = afternoon_open = afternoon_close = None
            if h.get("morning"):
                a, _, c = h["morning"].partition(" - ")
                morning_open, morning_close = a.strip(), c.strip()
            if h.get("afternoon"):
                a, _, c = h["afternoon"].partition(" - ")
                afternoon_open, afternoon_close = a.strip(), c.strip()
            cur.execute(
                """INSERT INTO branch_hours
                   (branch_number, day_num, morning_open, morning_close,
                    afternoon_open, afternoon_close, closed)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (bn, int(h["day_num"]), morning_open, morning_close,
                 afternoon_open, afternoon_close, 1 if h.get("closed") else 0),
            )
        inserted += 1

    cur.execute(
        "INSERT OR REPLACE INTO db_meta(key, value) VALUES ('branches_loaded_at', ?)",
        (str(int(time.time())),),
    )
    cur.execute(
        "INSERT OR REPLACE INTO db_meta(key, value) VALUES ('branches_count', ?)",
        (str(inserted),),
    )
    return {"branches": inserted, "services": len(seen_services)}


def main() -> int:
    json_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_JSON
    if not json_path.exists():
        print(f"ERROR: JSON not found at {json_path}", file=sys.stderr)
        return 2

    print(f"Source : {json_path}")
    print(f"Target : {DB_PATH}")
    branches = json.loads(json_path.read_text(encoding="utf-8"))

    conn = open_db(DB_PATH)
    try:
        apply_schema(conn)
        with conn:
            stats = load_branches(conn, branches)
        # ANALYZE so the query planner picks the right indexes
        conn.execute("ANALYZE")
        conn.execute("VACUUM")
        print(f"Loaded {stats['branches']} branches, {stats['services']} services.")
        # Quick sanity check
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM branches_rtree")
        print(f"R-Tree entries: {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM branch_hours")
        print(f"Hour rows    : {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM branch_services")
        print(f"Branch×Svc   : {cur.fetchone()[0]}")
        cur.execute("SELECT COUNT(*) FROM providers WHERE is_enabled=1")
        print(f"Providers ON : {cur.fetchone()[0]}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
