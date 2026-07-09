-- Israel Post branches DB
-- Designed to support "nearest branch by real travel time" with pluggable routing providers.

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;       -- WAL safe and ~3× faster than FULL
PRAGMA temp_store = MEMORY;
PRAGMA cache_size = -65536;        -- 64 MiB page cache
PRAGMA mmap_size = 268435456;      -- 256 MiB mmap window for hot pages

-- ============================================================================
-- REFERENCE TABLES
-- ============================================================================

CREATE TABLE IF NOT EXISTS providers (
    name                       TEXT PRIMARY KEY,
    display_name               TEXT NOT NULL,
    kind                       TEXT NOT NULL CHECK (kind IN ('routing','geocoding','navigation_link')),
    api_base_url               TEXT,
    default_cache_ttl_seconds  INTEGER NOT NULL DEFAULT 86400,
    supports_matrix            INTEGER NOT NULL DEFAULT 1,
    supports_traffic           INTEGER NOT NULL DEFAULT 0,
    is_self_hosted             INTEGER NOT NULL DEFAULT 0,
    is_enabled                 INTEGER NOT NULL DEFAULT 1,
    notes                      TEXT
);

CREATE TABLE IF NOT EXISTS services (
    service_id     INTEGER PRIMARY KEY,
    service_name   TEXT NOT NULL,
    category_name  TEXT,
    link           TEXT
);

-- ============================================================================
-- BRANCHES
-- ============================================================================

CREATE TABLE IF NOT EXISTS branches (
    branch_number   INTEGER PRIMARY KEY,
    branch_name     TEXT NOT NULL,
    branch_type     TEXT,
    region          TEXT,
    area            TEXT,
    city            TEXT,
    street          TEXT,
    house           INTEGER,
    zip             TEXT,
    address_extra   TEXT,
    full_address    TEXT NOT NULL,    -- composed for display & geocoding fallback
    latitude        REAL NOT NULL,
    longitude       REAL NOT NULL,
    -- Pre-computed for haversine: skips trig in hot loop, ~2× faster ranking
    lat_rad         REAL NOT NULL,
    lng_rad         REAL NOT NULL,
    sin_lat         REAL NOT NULL,
    cos_lat         REAL NOT NULL,
    telephone       TEXT
);

CREATE INDEX IF NOT EXISTS idx_branches_city   ON branches(city);
CREATE INDEX IF NOT EXISTS idx_branches_region ON branches(region);

-- R-Tree spatial index → fast bounding-box "near me" candidate query
-- before we spend money on a routing API.
CREATE VIRTUAL TABLE IF NOT EXISTS branches_rtree USING rtree(
    branch_number,
    min_lat, max_lat,
    min_lng, max_lng
);

-- Many-to-many: which services each branch offers
CREATE TABLE IF NOT EXISTS branch_services (
    branch_number INTEGER NOT NULL REFERENCES branches(branch_number) ON DELETE CASCADE,
    service_id    INTEGER NOT NULL REFERENCES services(service_id)    ON DELETE CASCADE,
    PRIMARY KEY (branch_number, service_id)
);
CREATE INDEX IF NOT EXISTS idx_bs_service ON branch_services(service_id);

-- Free-text extras (e.g. "החזרות", "Click2Post")
CREATE TABLE IF NOT EXISTS branch_extra_services (
    branch_number INTEGER NOT NULL REFERENCES branches(branch_number) ON DELETE CASCADE,
    extra         TEXT NOT NULL,
    PRIMARY KEY (branch_number, extra)
);

CREATE TABLE IF NOT EXISTS branch_accessibility (
    branch_number       INTEGER NOT NULL REFERENCES branches(branch_number) ON DELETE CASCADE,
    accessibility_type  TEXT NOT NULL,
    PRIMARY KEY (branch_number, accessibility_type)
);

-- 7 rows per branch (one per day)
-- "סגור" = closed=1 + nulls. Times stored as 'HH:MM' strings.
CREATE TABLE IF NOT EXISTS branch_hours (
    branch_number     INTEGER NOT NULL REFERENCES branches(branch_number) ON DELETE CASCADE,
    day_num           INTEGER NOT NULL CHECK (day_num BETWEEN 1 AND 7),  -- 1=Sun ... 7=Sat
    morning_open      TEXT,
    morning_close     TEXT,
    afternoon_open    TEXT,
    afternoon_close   TEXT,
    closed            INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (branch_number, day_num)
);

-- ============================================================================
-- CACHES — the heart of the multi-provider, cost-aware design
-- ============================================================================

-- Geocoding cache: "Tel Aviv, Dizengoff 50" -> (lat,lng)
-- Two keys:
--   address_normalized : strict normalized form (Hebrew niqqud removed, punctuation
--                        stripped, spaces collapsed, lowercased) — the cache hit key.
--   address_raw        : whatever the user typed — kept for debugging.
-- Lookup is O(1) by PK; an additional INDEX on normalized form would be redundant.
CREATE TABLE IF NOT EXISTS geocode_cache (
    address_normalized TEXT PRIMARY KEY,
    address_raw        TEXT NOT NULL,
    latitude           REAL NOT NULL,
    longitude          REAL NOT NULL,
    formatted_address  TEXT,
    provider           TEXT NOT NULL REFERENCES providers(name),
    fetched_at         TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at         TIMESTAMP,
    lookup_count       INTEGER NOT NULL DEFAULT 1,    -- popularity (LRU/LFU eviction)
    last_used_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_geocache_lastused ON geocode_cache(last_used_at);

-- Travel-time cache: (user_origin → branch) for each (mode, provider).
-- Origin is rounded to 5 decimals (~1.1m precision) to make the cache hit-able
-- even if the user's GPS jitters by a meter.
CREATE TABLE IF NOT EXISTS travel_time_cache (
    id                            INTEGER PRIMARY KEY AUTOINCREMENT,
    origin_lat_e5                 INTEGER NOT NULL,                                  -- round(lat * 1e5)
    origin_lng_e5                 INTEGER NOT NULL,
    branch_number                 INTEGER NOT NULL REFERENCES branches(branch_number) ON DELETE CASCADE,
    mode                          TEXT NOT NULL DEFAULT 'driving',                   -- driving|walking|transit|bicycling
    provider                      TEXT NOT NULL REFERENCES providers(name),
    duration_seconds              INTEGER,                                           -- baseline (no traffic)
    duration_in_traffic_seconds   INTEGER,                                           -- when provider supports it
    distance_meters               INTEGER,
    raw_response_json             TEXT,                                              -- debug breadcrumb
    fetched_at                    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at                    TIMESTAMP,
    UNIQUE (origin_lat_e5, origin_lng_e5, branch_number, mode, provider)
);

CREATE INDEX IF NOT EXISTS idx_ttc_origin ON travel_time_cache(origin_lat_e5, origin_lng_e5, mode, provider);
CREATE INDEX IF NOT EXISTS idx_ttc_expiry ON travel_time_cache(expires_at);

-- Audit log for cost tracking & debugging
CREATE TABLE IF NOT EXISTS routing_requests_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    provider          TEXT NOT NULL REFERENCES providers(name),
    request_type      TEXT NOT NULL,       -- matrix|route|geocode
    origin_lat        REAL,
    origin_lng        REAL,
    num_destinations  INTEGER,
    mode              TEXT,
    status_code       INTEGER,
    elements_billed   INTEGER,
    duration_ms       INTEGER,
    error_message     TEXT,
    requested_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_rrl_provider_time ON routing_requests_log(provider, requested_at);
-- Composite for the hot quota-window query: WHERE provider=? AND request_type=? AND requested_at>?
CREATE INDEX IF NOT EXISTS idx_rrl_quota_window ON routing_requests_log(provider, request_type, requested_at);

-- ============================================================================
-- QUOTA POLICY — limits enforced *before* any outbound API call.
-- Live usage is computed from routing_requests_log over a sliding window.
-- ============================================================================
CREATE TABLE IF NOT EXISTS provider_quotas (
    provider_name      TEXT NOT NULL REFERENCES providers(name) ON DELETE CASCADE,
    endpoint           TEXT NOT NULL,         -- 'matrix' | 'directions' | 'geocode' | 'isochrone' | 'snap' | 'optimization' | 'pois' | 'elevation' | 'export'
    daily_limit        INTEGER,               -- NULL = no daily cap
    per_minute_limit   INTEGER,               -- NULL = no per-minute cap
    notes              TEXT,
    PRIMARY KEY (provider_name, endpoint)
);

-- ============================================================================
-- METADATA
-- ============================================================================

CREATE TABLE IF NOT EXISTS db_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
