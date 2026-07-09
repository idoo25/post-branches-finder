"""
Diff post_branches.db against Israel Post's live branch feed and, optionally,
rebuild the DB from the fresh data.

Source (discovered by probing the actual site's network calls — the site's own
/mypost-contentcore/branches API is blocked by Radware bot protection, but this
CDN mirror of the same data is not):

    https://mypostvouchars-prd.azureedge.net/branches/branches.json

Usage:
    python refresh_from_live.py            # report only, no writes
    python refresh_from_live.py --apply    # also rebuild post_branches.db
"""
from __future__ import annotations

import json
import sqlite3
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "post_branches.db"
LIVE_URL = "https://mypostvouchars-prd.azureedge.net/branches/branches.json"
OUT_JSON = Path.home() / "Downloads" / "israelpost_branches_full.json"


def fetch_live() -> list[dict]:
    req = urllib.request.Request(LIVE_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read())
    if payload.get("ReturnCode") != 0:
        raise RuntimeError(f"Live feed error: {payload.get('ErrorMessage')}")
    return payload["Result"]


def hhmm(s: str | None) -> str | None:
    return s[:5] if s else None


def normalize_live_branch(b: dict, service_names: dict[int, tuple[str, str | None]]) -> dict:
    hours = {}
    for h in b.get("hours", []):
        day = int(h["dayofweek"])
        morning = f'{hhmm(h["openhour1"])} - {hhmm(h["closehour1"])}' if h.get("openhour1") else None
        afternoon = f'{hhmm(h["openhour2"])} - {hhmm(h["closehour2"])}' if h.get("openhour2") else None
        closed = not h.get("openhour1") and not h.get("openhour2")
        hours[day] = (morning, afternoon, closed)

    service_ids = set(int(s) for s in (b.get("Services") or []))
    accessibility = set(a["accessiblitytypeid"] for a in (b.get("accessibility") or []) if a.get("accessiblitytypeid"))
    extra = set(b.get("ExtraServices") or [])

    return {
        "branch_number": int(b["branchnumber"]),
        "branch_name": b.get("branchname"),
        "branch_type": b.get("branchtypename"),
        "region": b.get("region"),
        "area": b.get("area"),
        "city": b.get("city"),
        "street": b.get("street"),
        "house": b.get("house"),
        "zip": b.get("zip"),
        "address_extra": b.get("addressdesc"),
        "latitude": b.get("geocode_latitude"),
        "longitude": b.get("geocode_longitude"),
        "telephone": b.get("telephone"),
        "hours": hours,
        "service_ids": service_ids,
        "extra_services": extra,
        "accessibility": accessibility,
        "_raw": b,
    }


def load_db_state(conn: sqlite3.Connection) -> dict[int, dict]:
    cur = conn.cursor()
    state: dict[int, dict] = {}
    for row in cur.execute(
        "SELECT branch_number, branch_name, branch_type, region, area, city, street, "
        "house, zip, address_extra, latitude, longitude, telephone FROM branches"
    ):
        (bn, name, btype, region, area, city, street, house, zip_, extra, lat, lng, tel) = row
        state[bn] = {
            "branch_number": bn, "branch_name": name, "branch_type": btype,
            "region": region, "area": area, "city": city, "street": street,
            "house": house, "zip": zip_, "address_extra": extra,
            "latitude": lat, "longitude": lng, "telephone": tel,
            "hours": {}, "service_ids": set(), "extra_services": set(), "accessibility": set(),
        }
    for bn, day, mo, mc, ao, ac, closed in cur.execute(
        "SELECT branch_number, day_num, morning_open, morning_close, afternoon_open, afternoon_close, closed FROM branch_hours"
    ):
        morning = f"{mo} - {mc}" if mo else None
        afternoon = f"{ao} - {ac}" if ao else None
        state[bn]["hours"][day] = (morning, afternoon, bool(closed))
    for bn, sid in cur.execute("SELECT branch_number, service_id FROM branch_services"):
        state[bn]["service_ids"].add(sid)
    for bn, extra in cur.execute("SELECT branch_number, extra FROM branch_extra_services"):
        state[bn]["extra_services"].add(extra)
    for bn, acc in cur.execute("SELECT branch_number, accessibility_type FROM branch_accessibility"):
        state[bn]["accessibility"].add(acc)
    return state


def diff_branch(before: dict, after: dict) -> list[str]:
    changes = []
    for field in ("branch_name", "branch_type", "region", "area", "city", "street",
                  "house", "zip", "address_extra", "telephone"):
        if (before.get(field) or None) != (after.get(field) or None):
            changes.append(f'{field}: "{before.get(field)}" -> "{after.get(field)}"')
    try:
        if abs(float(before["latitude"]) - float(after["latitude"])) > 1e-5 or \
           abs(float(before["longitude"]) - float(after["longitude"])) > 1e-5:
            changes.append(f'coords: ({before["latitude"]},{before["longitude"]}) -> ({after["latitude"]},{after["longitude"]})')
    except (TypeError, ValueError):
        pass
    if before["hours"] != after["hours"]:
        for day in range(1, 8):
            if before["hours"].get(day) != after["hours"].get(day):
                changes.append(f'hours day{day}: {before["hours"].get(day)} -> {after["hours"].get(day)}')
    added_svc = after["service_ids"] - before["service_ids"]
    removed_svc = before["service_ids"] - after["service_ids"]
    if added_svc:
        changes.append(f"services added: {sorted(added_svc)}")
    if removed_svc:
        changes.append(f"services removed: {sorted(removed_svc)}")
    added_extra = after["extra_services"] - before["extra_services"]
    removed_extra = before["extra_services"] - after["extra_services"]
    if added_extra:
        changes.append(f"extra_services added: {sorted(added_extra)}")
    if removed_extra:
        changes.append(f"extra_services removed: {sorted(removed_extra)}")
    added_acc = after["accessibility"] - before["accessibility"]
    removed_acc = before["accessibility"] - after["accessibility"]
    if added_acc:
        changes.append(f"accessibility added: {sorted(added_acc)}")
    if removed_acc:
        changes.append(f"accessibility removed: {sorted(removed_acc)}")
    return changes


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    apply = "--apply" in sys.argv

    print("Fetching live feed...")
    live_raw = fetch_live()
    print(f"Live feed: {len(live_raw)} branches")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    service_names = {}
    for sid, name, cat in conn.execute("SELECT service_id, service_name, category_name FROM services"):
        service_names[sid] = (name, cat)

    before = load_db_state(conn)
    after = {}
    for b in live_raw:
        nb = normalize_live_branch(b, service_names)
        after[nb["branch_number"]] = nb

    before_nums = set(before.keys())
    after_nums = set(after.keys())
    added = after_nums - before_nums
    removed = before_nums - after_nums
    common = before_nums & after_nums

    print(f"\nDB currently has: {len(before_nums)} branches")
    print(f"Live feed has   : {len(after_nums)} branches")
    print(f"\n=== ADDED branches ({len(added)}) ===")
    for bn in sorted(added):
        b = after[bn]
        print(f"  +{bn}: {b['branch_name']} — {b['city']}")

    print(f"\n=== REMOVED branches ({len(removed)}) ===")
    for bn in sorted(removed):
        b = before[bn]
        print(f"  -{bn}: {b['branch_name']} — {b['city']}")

    changed_branches = {}
    for bn in sorted(common):
        changes = diff_branch(before[bn], after[bn])
        if changes:
            changed_branches[bn] = changes

    print(f"\n=== CHANGED branches ({len(changed_branches)} of {len(common)} common) ===")
    for bn, changes in list(changed_branches.items())[:40]:
        b = after[bn]
        print(f"  #{bn} {b['branch_name']} ({b['city']}):")
        for c in changes:
            print(f"      - {c}")
    if len(changed_branches) > 40:
        print(f"  ... and {len(changed_branches) - 40} more changed branches (see full report)")

    # unknown service ids seen live but not in our services dictionary
    all_live_service_ids = set()
    for b in after.values():
        all_live_service_ids |= b["service_ids"]
    unknown_ids = sorted(all_live_service_ids - set(service_names.keys()))
    if unknown_ids:
        print(f"\n=== Unrecognized service IDs in live feed (not in local `services` table): {unknown_ids} ===")

    report_path = HERE / "refresh_report.json"
    report_path.write_text(json.dumps({
        "db_count": len(before_nums),
        "live_count": len(after_nums),
        "added": sorted(added),
        "removed": sorted(removed),
        "changed": {str(k): v for k, v in changed_branches.items()},
        "unknown_service_ids": unknown_ids,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nFull report written to {report_path}")

    if not apply:
        print("\n(dry run — pass --apply to rebuild post_branches.db from the live feed)")
        conn.close()
        return 0

    conn.close()

    # ---- Apply: rebuild the source JSON (build_db.py's expected shape) + rebuild DB ----
    print("\nApplying: writing fresh source JSON and rebuilding DB...")
    out_branches = []
    for b in live_raw:
        nb = normalize_live_branch(b, service_names)
        services_list = []
        for sid in sorted(nb["service_ids"]):
            name, cat = service_names.get(sid, (f"שירות #{sid}", None))
            services_list.append({"id": sid, "name": name, "category": cat})
        hours_list = []
        day_names = {1: "ראשון", 2: "שני", 3: "שלישי", 4: "רביעי", 5: "חמישי", 6: "שישי", 7: "שבת"}
        for day in range(1, 8):
            morning, afternoon, closed = nb["hours"].get(day, (None, None, True))
            hours_list.append({
                "day_num": day, "day_name": day_names[day],
                "morning": morning, "afternoon": afternoon, "closed": closed,
            })
        out_branches.append({
            "branch_number": nb["branch_number"],
            "branch_name": nb["branch_name"],
            "branch_type": nb["branch_type"],
            "region": nb["region"],
            "area": nb["area"],
            "city": nb["city"],
            "street": nb["street"],
            "house": nb["house"],
            "zip": nb["zip"],
            "address_extra": nb["address_extra"],
            "latitude": nb["latitude"],
            "longitude": nb["longitude"],
            "telephone": nb["telephone"],
            "hours": hours_list,
            "services": services_list,
            "extra_services": sorted(nb["extra_services"]),
            "accessibility": sorted(nb["accessibility"]),
        })

    if OUT_JSON.exists():
        backup = OUT_JSON.with_name(OUT_JSON.stem + "_backup" + OUT_JSON.suffix)
        backup.write_bytes(OUT_JSON.read_bytes())
        print(f"Backed up previous source JSON to {backup}")

    OUT_JSON.write_text(json.dumps(out_branches, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(out_branches)} branches to {OUT_JSON}")

    import subprocess
    result = subprocess.run([sys.executable, str(HERE / "build_db.py"), str(OUT_JSON)],
                             cwd=HERE, capture_output=True, text=True, encoding="utf-8")
    print(result.stdout)
    if result.returncode != 0:
        print(result.stderr, file=sys.stderr)
        return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
