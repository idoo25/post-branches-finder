-- Seed routing/geocoding/navigation providers.
-- Edit `is_enabled` to switch which providers your app may use.
-- Notes column captures legal/quota constraints you'll otherwise forget.

INSERT OR REPLACE INTO providers
    (name, display_name, kind, api_base_url, default_cache_ttl_seconds,
     supports_matrix, supports_traffic, is_self_hosted, is_enabled, notes)
VALUES
    ('google_routes',
     'Google Maps Routes API (Compute Route Matrix)',
     'routing',
     'https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix',
     300,             -- 5 min — traffic data decays fast; Google ToS forbids long storage
     1, 1, 0, 0,
     'Best traffic. Pricing per element. ToS forbids long-term storage of routing results.'),

    ('google_distance_matrix',
     'Google Distance Matrix API (legacy)',
     'routing',
     'https://maps.googleapis.com/maps/api/distancematrix/json',
     300,             -- 5 min
     1, 1, 0, 0,
     'Legacy API. Same ToS caching restrictions as Routes API.'),

    ('mapbox_matrix',
     'Mapbox Matrix API',
     'routing',
     'https://api.mapbox.com/directions-matrix/v1',
     86400,           -- 1 day OK; no traffic
     1, 0, 0, 0,
     'No live traffic. Cheaper than Google. Good middle option.'),

    ('here_matrix',
     'HERE Matrix Routing API v8',
     'routing',
     'https://matrix.router.hereapi.com/v8/matrix',
     300,             -- 5 min: traffic-aware results decay quickly
     1, 1, 0, 0,
     'Live traffic by default. ~2,500 free transactions/month, then ~$5/1000. 1 call = 1 transaction regardless of N×M.'),

    ('osrm',
     'OSRM (self-hosted or demo)',
     'routing',
     'https://router.project-osrm.org/table/v1',
     604800,          -- 1 week — graph rarely changes
     1, 0, 1, 1,
     'Free, no traffic. Public demo — self-throttled via provider_quotas below; self-host for real production load.'),

    ('valhalla',
     'Valhalla (self-hosted)',
     'routing',
     NULL,
     604800,
     1, 0, 1, 0,
     'Self-hosted alternative to OSRM. Better routing quality, isochrones supported.'),

    ('openrouteservice',
     'openrouteservice',
     'routing',
     'https://api.openrouteservice.org/v2/matrix',
     604800,
     1, 0, 0, 0,
     'Free tier: 500 requests/day, max 3500 routes/matrix. No traffic.'),

    ('waze_deeplink',
     'Waze deep link (navigation only)',
     'navigation_link',
     'https://waze.com/ul',
     0,
     0, 0, 0, 1,
     'Opens Waze app to start navigation. Does NOT return travel time. Use as "Navigate" button after ranking with another provider.'),

    ('waze_iframe',
     'Waze iframe embed (display only)',
     'navigation_link',
     'https://embed.waze.com/iframe',
     0,
     0, 0, 0, 1,
     'Embeds a live Waze map in your page. Does NOT expose travel time programmatically.'),

    ('waze_transport_sdk',
     'Waze Transport SDK',
     'routing',
     NULL,
     1800,
     1, 1, 0, 0,
     'Partner-only API. Requires application & approval from Waze. Has traffic.'),

    -- Geocoding providers (for converting "address typed by user" to lat/lng)
    ('google_geocoding',
     'Google Geocoding API',
     'geocoding',
     'https://maps.googleapis.com/maps/api/geocode/json',
     2592000,         -- 30 days
     0, 0, 0, 0,
     'Highest quality. ToS allows storing place_id but not raw lat/lng beyond 30 days.'),

    ('mapbox_geocoding',
     'Mapbox Geocoding',
     'geocoding',
     'https://api.mapbox.com/geocoding/v5/mapbox.places',
     7776000,         -- 90 days
     0, 0, 0, 0,
     'Permanent geocoding requires "permanent" plan; otherwise 90-day cache limit.'),

    ('nominatim',
     'Nominatim (OpenStreetMap)',
     'geocoding',
     'https://nominatim.openstreetmap.org/search',
     31536000,        -- 1 year
     0, 0, 1, 1,
     'Free. Fair-use policy: 1 req/sec. Self-host for any real volume.'),

    -- Synthetic providers — production writes travel_time_cache rows under
    -- 'mock_haversine' (the offline estimate-fallback tier, see server.py's
    -- _ESTIMATE_PROVIDER / MockHaversineProvider) and tests use 'mock_traffic'
    -- (MockHaversineProvider with traffic_multiplier set). Both must be
    -- registered here or the provider FK on travel_time_cache/geocode_cache
    -- rejects the insert once PRAGMA foreign_keys = ON is actually enforced.
    ('mock_haversine',
     'Mock straight-line/constant-speed provider (offline estimate fallback)',
     'routing',
     NULL,
     86400,
     1, 0, 0, 1,
     'SYNTHETIC — not a real API. Pure haversine + constant avg_kmh, used when no real routing provider is configured. Not for production accuracy claims.'),

    ('mock_traffic',
     'Mock traffic-aware provider (tests only)',
     'routing',
     NULL,
     300,
     1, 1, 0, 0,
     'SYNTHETIC — not a real API. MockHaversineProvider with a traffic_multiplier, used by tests to exercise rerank_with_traffic. Disabled: do not enable in production.');

-- ============================================================================
-- Quota policy (per provider × endpoint).
-- Numbers below match the openrouteservice free tier the user posted.
-- Adjust for the plan you actually have.
-- ============================================================================
INSERT OR REPLACE INTO provider_quotas (provider_name, endpoint, daily_limit, per_minute_limit, notes) VALUES
    -- openrouteservice free tier
    ('openrouteservice', 'directions',   2000,  40,  'Routes V2'),
    ('openrouteservice', 'export',        100,   5,  'Export V2'),
    ('openrouteservice', 'isochrones',    500,  20,  'Isochrones V2'),
    ('openrouteservice', 'matrix',        500,  40,  'Matrix V2 — guards find_nearest()'),
    ('openrouteservice', 'snap',         2000, 100,  'Snap V2'),
    ('openrouteservice', 'elevation',    2000,  40,  ''),
    ('openrouteservice', 'geocode',      3000, 100,  'Pelias-based — guards user address geocoding'),
    ('openrouteservice', 'optimization',  500,  40,  ''),
    ('openrouteservice', 'pois',          500,  60,  ''),

    -- Nominatim — fair-use is "1 req/sec" with no daily cap (but be polite)
    ('nominatim',        'geocode',      NULL,  60,  '~1 req/sec is policy. Self-host removes the cap.'),

    -- OSRM public demo — no published daily cap; self-throttled per-minute
    -- so we stay a good citizen of the shared, volunteer-run OSM infra.
    ('osrm',             'matrix',       NULL,  20,  'router.project-osrm.org demo server, self-throttled.'),

    -- Defaults for the commercial providers — fill in when you sign up.
    ('google_routes',           'matrix',  NULL, 600, 'Pricing per element. No hard cap by default.'),
    ('google_distance_matrix',  'matrix',  NULL, 600, ''),
    ('google_geocoding',        'geocode', NULL, 3000, ''),
    ('mapbox_matrix',           'matrix',  100000, 60, 'Free tier: 100K req/month.'),

    -- HERE Base Plan: ~2,500 matrix transactions / month → ~80/day soft cap.
    ('here_matrix',             'matrix',  80, 60, 'Free Base Plan ~2500/month → ~80/day. 1 call = 1 transaction (1×15 destinations is one element).');
