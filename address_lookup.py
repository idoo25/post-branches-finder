"""
Canonicalize a free-text Israeli address against a verified address dataset
(city/street/house-number data sourced from Israel Post + Ministry of
Interior) before handing it to a geocoding provider.

Why: Nominatim (OSM) sometimes fails or mismatches on Hebrew addresses due to
spelling variants, alternate street names, or missing/rare entries.
The dataset has 1,583 cities (+525 aliases), 63,813 streets (+92,956 aliases),
and 716,207 verified house-level (city, street, house_number) combinations.
If the free-text input matches, we rewrite it to the OFFICIAL spelling before
geocoding — same free Nominatim call, better hit rate. Pure local SQLite
lookup, no network call. Falls back to the original text (returns None) on
any mismatch, missing file, or ambiguity — never raises.

Bundled as "addresses_lite.db" (~25MB, committed to this repo) rather than
the full ~425MB source DB it's derived from — it keeps only the tables and
columns this module actually reads (cities/city_aliases/streets/
street_aliases/zip_codes, house-existence check only), dropping unrelated
ETL/crawl bookkeeping tables and per-row metadata the lookup never touches.
This keeps it small enough to ship with the app (including to Render), so
address canonicalization works identically in production, not just locally.
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import threading
from pathlib import Path

from address_norm import normalize as normalize_address

logger = logging.getLogger(__name__)

ADDRESSES_DB = Path(os.environ.get("ADDRESSES_DB_PATH", str(Path(__file__).resolve().parent / "addresses_lite.db")))

_HOUSE_NUM_RE = re.compile(r"^(.*?)\s*(\d+)\s*$")

# Common Hebrew settlement-type descriptors people prepend redundantly
# ("קיבוץ אפיקים" for the kibbutz officially just named "אפיקים"). Left in,
# these confuse free-text geocoders — e.g. Nominatim matches "קיבוץ אפיקים"
# to an unrelated "נווה אפיקים" neighborhood in Rosh Ha'ayin instead of the
# real kibbutz. Stripped here whenever the *rest* of the input is otherwise
# just the verified city name (not a street+house address).
_SETTLEMENT_PREFIXES = {
    normalize_address(w) for w in
    ("קיבוץ", "מושב", "מושבה", "יישוב", "כפר", "שכונת", "מעלה", "אזור התעשייה")
}


class AddressLookup:
    """Loads the small dictionaries (cities/streets + aliases) into memory
    once; house-number verification stays a per-call SQLite query since the
    zip_codes table is too large (716K rows) to usefully preload."""

    def __init__(self, db_path: Path = ADDRESSES_DB):
        self.conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._load()

    def _load(self) -> None:
        cur = self.conn.cursor()

        # normalized city/alias name -> (city_id, official display name)
        self.city_by_name: dict[str, tuple[int, str]] = {}
        all_cities: list[tuple[int, str]] = []
        for cid, name in cur.execute("SELECT id, name_he FROM cities WHERE name_he IS NOT NULL"):
            self.city_by_name.setdefault(normalize_address(name), (cid, name))
            all_cities.append((cid, name))
        for cid, alias in cur.execute("SELECT city_id, alias FROM city_aliases"):
            key = normalize_address(alias)
            if key not in self.city_by_name:
                row = cur.execute("SELECT name_he FROM cities WHERE id=?", (cid,)).fetchone()
                if row:
                    self.city_by_name[key] = (cid, row[0])

        # Official names with " - " (e.g. "תל אביב - יפו", "פוריה - נווה עובד") are
        # often typed without the second half. Register that shorthand too, but
        # only when it's unambiguous — some pairs (e.g. two "פוריה - ..." cities)
        # would collide, so leave those unresolved rather than guess wrong.
        first_half_counts: dict[str, list[tuple[int, str]]] = {}
        for cid, name in all_cities:
            if " - " in name:
                head = normalize_address(name.split(" - ", 1)[0])
                first_half_counts.setdefault(head, []).append((cid, name))
        for head, candidates in first_half_counts.items():
            if len(candidates) == 1 and head not in self.city_by_name:
                self.city_by_name[head] = candidates[0]

        # longest-name-first, so "תל אביב" matches before a shorter false-positive substring
        self._city_keys_sorted = sorted(self.city_by_name.keys(), key=len, reverse=True)

        # official street name per (city_id, street_code) — for display after a match
        self._street_name = {}
        for cid, code, name in cur.execute("SELECT city_id, street_code, name_he FROM streets"):
            self._street_name[(cid, code)] = name

        # (city_id, normalized street/alias name) -> street_code
        self.street_by_city_name: dict[tuple[int, str], int] = {}
        for cid, code, name in cur.execute("SELECT city_id, street_code, name_he FROM streets"):
            self.street_by_city_name[(cid, normalize_address(name))] = code
        for cid, code, alias in cur.execute("SELECT city_id, street_code, alias FROM street_aliases"):
            self.street_by_city_name.setdefault((cid, normalize_address(alias)), code)

    def _match_city(self, text: str) -> tuple[int, str, str] | None:
        """Longest known city name/alias that is a suffix of `text`.
        Returns (city_id, official_city_name, remainder_before_city)."""
        for key in self._city_keys_sorted:
            if text == key or text.endswith(" " + key):
                remainder = text[: -len(key)].strip() if text != key else ""
                cid, official = self.city_by_name[key]
                return cid, official, remainder
        return None

    def canonicalize(self, raw_address: str) -> str | None:
        """Best-effort: return an official '<street> <house>, <city>' string
        if the address matches a *verified* (existing) house entry, else None."""
        norm = normalize_address(raw_address)
        if not norm:
            return None

        match = self._match_city(norm)
        if match is None:
            return None
        city_id, official_city, remainder = match
        if not remainder:
            return None  # bare city name — nothing to canonicalize

        if remainder in _SETTLEMENT_PREFIXES:
            # e.g. "קיבוץ אפיקים" -> "אפיקים": nothing left but a redundant
            # settlement-type word, so this is just a city query in disguise.
            return official_city

        m = _HOUSE_NUM_RE.match(remainder)
        if not m:
            return None
        street_part, house_num = m.group(1).strip(), int(m.group(2))
        if not street_part:
            return None

        street_code = self.street_by_city_name.get((city_id, street_part))
        if street_code is None:
            return None

        exists = self.conn.execute(
            "SELECT 1 FROM zip_codes WHERE city_id=? AND street_code=? AND house_number=? LIMIT 1",
            (city_id, street_code, house_num),
        ).fetchone()
        if exists is None:
            return None  # house number not verified — don't force a possibly-wrong form

        official_street = self._street_name.get((city_id, street_code), street_part)
        return f"{official_street} {house_num}, {official_city}"


_singleton: "AddressLookup | None" = None
_load_failed = False
_singleton_lock = threading.Lock()


def get_lookup() -> "AddressLookup | None":
    """Returns a shared AddressLookup instance, or None if addresses.db is
    unavailable (moved/deleted) — callers should treat None as 'skip'."""
    global _singleton, _load_failed
    if _singleton is not None:
        return _singleton
    if _load_failed:
        return None
    with _singleton_lock:
        # Re-check inside the lock: another thread may have already built
        # (or failed to build) the singleton while we were waiting for it.
        if _singleton is not None:
            return _singleton
        if _load_failed:
            return None
        try:
            _singleton = AddressLookup()
        except Exception:
            _load_failed = True
            logger.warning(
                "AddressLookup unavailable — tried %s; address canonicalization is disabled.",
                ADDRESSES_DB,
                exc_info=True,
            )
            return None
        return _singleton


def canonicalize(raw_address: str) -> str | None:
    lookup = get_lookup()
    if lookup is None:
        return None
    return lookup.canonicalize(raw_address)
