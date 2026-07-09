"""
Routing- and geocoding-provider abstractions.

Design goals
------------
* **Versatile** — same calling code works against Google Routes, Google Distance
  Matrix, Mapbox, OSRM, openrouteservice, Valhalla, Waze SDK, …
* **Honest about capabilities** — each provider declares what it supports via
  class-level boolean flags (`supports_traffic`, `supports_isochrone`, …).
  Higher layers can pick a provider that satisfies a query, or fall back.
* **One options object, many providers** — `RoutingOptions` carries the
  *intent* (avoid these polygons, prefer this routing strategy, vehicle weight,
  …). Each provider translates only the fields it understands and silently
  ignores the rest (or warns), so the orchestrator can pass the same options
  to any provider without conditionals.

Adding a new provider = subclass + flags + implement `matrix(...)` (and any
optional methods you can support). Done.
"""
from __future__ import annotations

import socket
import threading
import time
import warnings
from dataclasses import dataclass, field
from typing import Protocol, Sequence


# ============================================================================
# Common value types
# ============================================================================

@dataclass(slots=True, frozen=True)
class Coordinate:
    lat: float
    lng: float


@dataclass(slots=True)
class TravelLeg:
    """One origin→destination measurement returned by a routing provider."""
    duration_seconds: int
    duration_in_traffic_seconds: int | None     # None when provider has no traffic
    distance_meters: int
    geometry_encoded: str | None = None         # encoded polyline, when provided
    raw: dict | None = None


@dataclass(slots=True)
class IsochroneResult:
    """One isochrone polygon returned for a (range, location) pair."""
    range_seconds: int
    geometry_geojson: dict                      # GeoJSON Polygon or MultiPolygon
    properties: dict | None = None              # area, total_pop, etc. (provider-specific)


@dataclass(slots=True)
class RoutingOptions:
    """Provider-agnostic request options.

    Each routing call accepts an instance of this. Each provider translates the
    fields it supports and warns (or silently ignores) the rest.
    """
    # Avoid arbitrary polygons (closed roads, banned zones). GeoJSON dict.
    avoid_polygons: dict | None = None
    # Avoid generic features by name. Provider-portable values:
    #   'tollways' (or 'tolls'), 'highways' (or 'motorways'), 'ferries',
    #   'tunnels', 'borders'. Each provider maps to its own enum.
    avoid_features: list[str] | None = None
    # Routing preference: 'fastest' | 'shortest' | 'recommended'
    preference: str | None = None
    # Vehicle constraints (HGV: weight, height, length, axleload, hazmat).
    profile_params: dict | None = None
    # Departure timestamp (epoch seconds) — for traffic-aware providers.
    depart_at: int | None = None
    # Provider-specific raw extras for power users — merged into the request body.
    extra: dict = field(default_factory=dict)


# ============================================================================
# Capability-aware Protocols
# ============================================================================

class RoutingProvider(Protocol):
    """Interface satisfied by every routing provider."""
    name: str
    # Capability flags — single source of truth for what the provider can do.
    supports_matrix:           bool
    supports_isochrone:        bool
    supports_route:            bool
    supports_traffic:          bool
    supports_avoid_polygons:   bool
    supports_avoid_features:   bool
    supports_profile_params:   bool

    def matrix(
        self,
        origin: Coordinate,
        destinations: Sequence[Coordinate],
        *,
        mode: str = "driving",
        options: RoutingOptions | None = None,
    ) -> list[TravelLeg | None]: ...

    def isochrone(
        self,
        origin: Coordinate,
        ranges_seconds: Sequence[int],
        *,
        mode: str = "driving",
        options: RoutingOptions | None = None,
    ) -> list[IsochroneResult]: ...


@dataclass(slots=True)
class GeocodeResult:
    lat: float
    lng: float
    formatted_address: str | None
    raw: dict | None = None


class GeocodingProvider(Protocol):
    name: str
    def geocode(self, address: str) -> GeocodeResult | None: ...


# ============================================================================
# Capability-aware base — defaults to "supports nothing optional"
# ============================================================================

class _ProviderBase:
    """Default capability flags. Override in subclasses."""
    name: str = "base"
    supports_matrix:           bool = False
    supports_isochrone:        bool = False
    supports_route:            bool = False
    supports_traffic:          bool = False
    supports_avoid_polygons:   bool = False
    supports_avoid_features:   bool = False
    supports_profile_params:   bool = False
    # Hard cap from the provider on destinations *per single matrix call*.
    # The orchestrator splits larger requests into multiple batches and
    # charges quota per batch.
    max_destinations_per_matrix_call: int = 25

    # Default no-op implementations — override per provider.
    def matrix(self, origin, destinations, *, mode="driving", options=None):
        raise NotImplementedError(f"{self.name}: matrix() not supported")

    def isochrone(self, origin, ranges_seconds, *, mode="driving", options=None):
        raise NotImplementedError(f"{self.name}: isochrone() not supported")

    def route(self, origin, destination, *, mode="driving", options=None):
        raise NotImplementedError(f"{self.name}: route() not supported")

    # Helper: warn about RoutingOptions fields the provider can't honour.
    def _warn_unsupported(self, options: RoutingOptions | None) -> None:
        if options is None:
            return
        if options.avoid_polygons is not None and not self.supports_avoid_polygons:
            warnings.warn(f"{self.name}: avoid_polygons not supported — ignored", stacklevel=3)
        if options.avoid_features and not self.supports_avoid_features:
            warnings.warn(f"{self.name}: avoid_features not supported — ignored", stacklevel=3)
        if options.profile_params and not self.supports_profile_params:
            warnings.warn(f"{self.name}: profile_params not supported — ignored", stacklevel=3)


# ============================================================================
# OPENROUTESERVICE — fully implemented
# ============================================================================

class OpenRouteServiceProvider(_ProviderBase):
    """openrouteservice — Pelias geocoder + routing.

    Auth: Authorization: <KEY>  (no Bearer prefix), or ?api_key=KEY for GETs
    Coords convention: [lng, lat] (GeoJSON / WGS 84).
    """
    name = "openrouteservice"
    supports_matrix          = True
    supports_isochrone       = True
    supports_route           = True
    supports_traffic         = False
    supports_avoid_polygons  = True
    supports_avoid_features  = True
    supports_profile_params  = True
    # ORS free tier: 25 locations max per matrix request including the origin.
    # 1 origin + 24 destinations = 25 total.
    max_destinations_per_matrix_call = 24

    BASE_URL = "https://api.openrouteservice.org"

    _MODE_TO_PROFILE = {
        "driving":          "driving-car",
        "driving-car":      "driving-car",
        "driving-hgv":      "driving-hgv",
        "walking":          "foot-walking",
        "foot-walking":     "foot-walking",
        "foot-hiking":      "foot-hiking",
        "bicycling":        "cycling-regular",
        "cycling-regular":  "cycling-regular",
        "cycling-road":     "cycling-road",
        "cycling-mountain": "cycling-mountain",
        "cycling-electric": "cycling-electric",
        "wheelchair":       "wheelchair",
    }

    # ORS-specific names for the portable avoid_features values.
    _AVOID_FEATURE_MAP = {
        "tolls": "tollways", "tollways": "tollways",
        "highways": "highways", "motorways": "highways",
        "ferries": "ferries",
        "tunnels": "tunnels",
        "borders": "borders",
        "fords": "fords",
        "steps": "steps",
    }

    def __init__(self, api_key: str, base_url: str | None = None, timeout: float = 15.0):
        self.api_key = api_key
        self.base_url = (base_url or self.BASE_URL).rstrip("/")
        self.timeout = timeout

    # ----- internals -----
    def _profile(self, mode: str) -> str:
        try:
            return self._MODE_TO_PROFILE[mode]
        except KeyError as e:
            raise ValueError(f"Unknown mode {mode!r} for {self.name}") from e

    def _build_options_block(self, options: RoutingOptions | None) -> dict | None:
        if options is None:
            return None
        block: dict = {}
        if options.avoid_polygons is not None:
            block["avoid_polygons"] = options.avoid_polygons
        if options.avoid_features:
            mapped = []
            for f in options.avoid_features:
                tgt = self._AVOID_FEATURE_MAP.get(f)
                if tgt:
                    mapped.append(tgt)
                else:
                    warnings.warn(f"{self.name}: unknown avoid_feature {f!r}", stacklevel=4)
            if mapped:
                block["avoid_features"] = mapped
        if options.profile_params:
            block["profile_params"] = options.profile_params
        block.update(options.extra.get("ors_options", {}))
        return block or None

    def _post_json(self, path: str, body: dict) -> dict:
        import json as _json
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError, URLError

        req = Request(
            f"{self.base_url}{path}",
            data=_json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": self.api_key,
                "Content-Type":  "application/json",
                "Accept":        "application/json, application/geo+json",
            },
            method="POST",
        )
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                return _json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            raise RuntimeError(f"ORS {path} HTTP {e.code}: {e.read().decode('utf-8','replace')[:300]}") from e
        except (URLError, socket.timeout) as e:
            raise RuntimeError(f"ORS {path} network error: {e}") from e

    # ----- public API -----
    def matrix(self, origin, destinations, *, mode="driving", options=None):
        # ORS Matrix endpoint does NOT accept the options block (no avoid_polygons).
        # Warn so the caller knows.
        if options is not None and (options.avoid_polygons or options.avoid_features
                                    or options.profile_params or options.preference):
            warnings.warn(
                f"{self.name}: /v2/matrix doesn't support options.{{avoid_polygons,avoid_features,"
                "profile_params,preference}} — those fields are honoured only by /v2/directions. "
                "Use route() if you need them.",
                stacklevel=2,
            )

        locs = [[origin.lng, origin.lat]] + [[d.lng, d.lat] for d in destinations]
        body = {
            "locations":    locs,
            "sources":      [0],
            "destinations": list(range(1, len(locs))),
            "metrics":      ["duration", "distance"],
            "units":        "m",
        }
        payload = self._post_json(f"/v2/matrix/{self._profile(mode)}", body)
        durations = (payload.get("durations") or [[]])[0]
        distances = (payload.get("distances") or [[]])[0]
        legs: list[TravelLeg | None] = []
        for i in range(len(destinations)):
            dur = durations[i] if i < len(durations) else None
            dist = distances[i] if i < len(distances) else None
            if dur is None or dist is None:
                legs.append(None)
                continue
            legs.append(TravelLeg(
                duration_seconds=int(dur),
                duration_in_traffic_seconds=None,
                distance_meters=int(dist),
            ))
        return legs

    def isochrone(self, origin, ranges_seconds, *, mode="driving", options=None):
        body = {
            "locations":  [[origin.lng, origin.lat]],
            "range":      list(ranges_seconds),
            "range_type": "time",
            "attributes": ["area", "total_pop"],
        }
        opts = self._build_options_block(options)
        if opts:
            body["options"] = opts
        payload = self._post_json(f"/v2/isochrones/{self._profile(mode)}", body)
        out: list[IsochroneResult] = []
        for feat in (payload.get("features") or []):
            props = feat.get("properties") or {}
            out.append(IsochroneResult(
                range_seconds=int(props.get("value", 0)),
                geometry_geojson=feat.get("geometry") or {},
                properties=props,
            ))
        out.sort(key=lambda r: r.range_seconds)
        return out

    def route(self, origin, destination, *, mode="driving", options=None):
        """Single-route call honouring all RoutingOptions fields."""
        body = {
            "coordinates": [[origin.lng, origin.lat], [destination.lng, destination.lat]],
            "preference":  (options.preference if options and options.preference else "recommended"),
            "geometry":    True,
        }
        opts = self._build_options_block(options)
        if opts:
            body["options"] = opts
        payload = self._post_json(f"/v2/directions/{self._profile(mode)}/json", body)
        if not payload.get("routes"):
            return None
        r = payload["routes"][0]
        summary = r.get("summary") or {}
        return TravelLeg(
            duration_seconds=int(summary.get("duration", 0)),
            duration_in_traffic_seconds=None,
            distance_meters=int(summary.get("distance", 0)),
            geometry_encoded=r.get("geometry"),
        )


class OpenRouteServiceGeocodingProvider:
    """Forward geocoding + autocomplete via Pelias-backed /geocode/*.
    Registers under the same provider name as routing — quotas in
    `provider_quotas` are keyed per (provider, endpoint).
    """
    name = "openrouteservice"
    BASE_URL = "https://api.openrouteservice.org"

    def __init__(self, api_key: str, country: str = "ISR", timeout: float = 15.0):
        self.api_key = api_key
        self.country = country
        self.timeout = timeout

    def _get_json(self, path: str, params: dict) -> dict:
        import json as _json
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError, URLError
        req = Request(f"{self.BASE_URL}{path}?{urlencode(params)}", headers={
            "Authorization": self.api_key,
            "Accept":        "application/geo+json, application/json",
        })
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                return _json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            raise RuntimeError(f"ORS {path} HTTP {e.code}: {e.read().decode('utf-8','replace')[:300]}") from e
        except (URLError, socket.timeout) as e:
            raise RuntimeError(f"ORS {path} network error: {e}") from e

    def geocode(self, address: str) -> GeocodeResult | None:
        payload = self._get_json("/geocode/search",
                                 {"text": address, "boundary.country": self.country, "size": 1})
        features = payload.get("features") or []
        if not features:
            return None
        f = features[0]
        coords = f.get("geometry", {}).get("coordinates")
        if not coords or len(coords) < 2:
            return None
        props = f.get("properties") or {}
        if props.get("match_type") == "fallback":
            # Pelias couldn't find the requested street/house and silently
            # substituted a coarser match (city/locality centroid). Treat as
            # a miss rather than return a misleadingly "successful" result
            # that's actually just the middle of the city.
            return None
        return GeocodeResult(
            lat=float(coords[1]), lng=float(coords[0]),
            formatted_address=props.get("label"),
            raw=f,
        )

    def autocomplete(self, text: str, size: int = 5) -> list[GeocodeResult]:
        """Type-ahead suggestions. Per ORS docs the endpoint is asynchronous and
        should be throttled — debounce on the client side."""
        if not text or len(text.strip()) < 2:
            return []
        payload = self._get_json("/geocode/autocomplete",
                                 {"text": text, "boundary.country": self.country})
        out = []
        for f in (payload.get("features") or [])[:size]:
            coords = f.get("geometry", {}).get("coordinates")
            if not coords or len(coords) < 2:
                continue
            out.append(GeocodeResult(
                lat=float(coords[1]), lng=float(coords[0]),
                formatted_address=(f.get("properties") or {}).get("label"),
                raw=f,
            ))
        return out


# ============================================================================
# MAPBOX MATRIX — stub (real impl one HTTP call away)
# ============================================================================

class MapboxMatrixProvider(_ProviderBase):
    name = "mapbox_matrix"
    supports_matrix          = True
    supports_isochrone       = True   # /isochrone/v1 endpoint exists
    supports_route           = True
    supports_traffic         = True   # via mapbox/driving-traffic profile
    supports_avoid_polygons  = False  # Mapbox has no `exclude=polygon`
    supports_avoid_features  = True   # `exclude=toll,ferry,motorway`
    supports_profile_params  = False
    # Mapbox: 25 coordinates total per call; 10 for driving-traffic profile.
    max_destinations_per_matrix_call = 24

    BASE_URL = "https://api.mapbox.com"

    def __init__(self, access_token: str, timeout: float = 15.0):
        self.token = access_token
        self.timeout = timeout

    def matrix(self, origin, destinations, *, mode="driving", options=None):
        # GET /directions-matrix/v1/mapbox/{driving|driving-traffic|cycling|walking}/{semicolon-coords}
        # ?annotations=duration,distance&sources=0&destinations=1;2;3&access_token=...
        # exclude=toll,ferry,motorway
        self._warn_unsupported(options)
        raise NotImplementedError("MapboxMatrixProvider.matrix — wire the GET when you have a token.")

    def isochrone(self, origin, ranges_seconds, *, mode="driving", options=None):
        # GET /isochrone/v1/mapbox/{profile}/{lng},{lat}?contours_minutes=5,15,30
        self._warn_unsupported(options)
        raise NotImplementedError("MapboxMatrixProvider.isochrone — wire the GET when you have a token.")


# ============================================================================
# GOOGLE MAPS — stubs (rich capabilities, paid API)
# ============================================================================

class GoogleRoutesProvider(_ProviderBase):
    name = "google_routes"
    supports_matrix          = True
    supports_isochrone       = False  # not natively in Routes API
    supports_route           = True
    supports_traffic         = True
    supports_avoid_polygons  = False
    supports_avoid_features  = True   # routeModifiers.avoidTolls/Highways/Ferries
    supports_profile_params  = False
    # Google Routes API: 625 elements (25×25). For 1×N: up to 625 destinations.
    max_destinations_per_matrix_call = 625

    URL = "https://routes.googleapis.com/distanceMatrix/v2:computeRouteMatrix"

    def __init__(self, api_key: str, timeout: float = 15.0):
        self.api_key = api_key
        self.timeout = timeout

    def matrix(self, origin, destinations, *, mode="driving", options=None):
        # POST with X-Goog-Api-Key header.
        # body.travelMode = "DRIVE"|"WALK"|"BICYCLE"|"TWO_WHEELER"|"TRANSIT"
        # body.routingPreference = "TRAFFIC_AWARE"|"TRAFFIC_AWARE_OPTIMAL"|"TRAFFIC_UNAWARE"
        # body.routeModifiers = {avoidTolls, avoidHighways, avoidFerries}
        self._warn_unsupported(options)
        raise NotImplementedError("GoogleRoutesProvider.matrix — wire when you have a key.")


class GoogleDistanceMatrixProvider(_ProviderBase):
    name = "google_distance_matrix"
    supports_matrix          = True
    supports_isochrone       = False
    supports_route           = False
    supports_traffic         = True
    supports_avoid_polygons  = False
    supports_avoid_features  = True   # avoid=tolls|highways|ferries|indoor
    supports_profile_params  = False
    # Google DM: 25 origins × 25 destinations, max 100 elements per request.
    # For 1×N: up to 25 destinations per call (100/4 elements rule doesn't apply).
    max_destinations_per_matrix_call = 25

    URL = "https://maps.googleapis.com/maps/api/distancematrix/json"

    _MODE_MAP = {
        "driving": "driving", "driving-car": "driving",
        "walking": "walking", "foot-walking": "walking",
        "bicycling": "bicycling", "cycling-regular": "bicycling",
        "transit": "transit",
    }

    _AVOID_MAP = {
        "tolls": "tolls", "tollways": "tolls",
        "highways": "highways", "motorways": "highways",
        "ferries": "ferries",
        "indoor": "indoor",
    }

    def __init__(self, api_key: str, timeout: float = 15.0):
        self.api_key = api_key
        self.timeout = timeout

    def matrix(self, origin, destinations, *, mode="driving", options=None):
        import json as _json
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError, URLError

        self._warn_unsupported(options)

        params = {
            "origins":        f"{origin.lat},{origin.lng}",
            "destinations":   "|".join(f"{d.lat},{d.lng}" for d in destinations),
            "mode":           self._MODE_MAP.get(mode, mode),
            "units":          "metric",
            "key":            self.api_key,
            # `departure_time=now` is what unlocks duration_in_traffic in the
            # response. Required for live-traffic ranking.
            "departure_time": "now",
            "traffic_model":  "best_guess",  # | "pessimistic" | "optimistic"
        }

        if options and options.avoid_features:
            avoid = [self._AVOID_MAP[f] for f in options.avoid_features if f in self._AVOID_MAP]
            if avoid:
                params["avoid"] = "|".join(avoid)
        if options and options.depart_at and options.depart_at > int(time.time()):
            # User-specified future departure: send as epoch seconds.
            params["departure_time"] = str(options.depart_at)

        url = f"{self.URL}?{urlencode(params)}"
        req = Request(url, headers={"Accept": "application/json"})
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                payload = _json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            raise RuntimeError(f"Google DM HTTP {e.code}: {e.read().decode('utf-8','replace')[:300]}") from e
        except (URLError, socket.timeout) as e:
            raise RuntimeError(f"Google DM network error: {e}") from e

        if payload.get("status") != "OK":
            raise RuntimeError(
                f"Google DM error: {payload.get('status')} - {payload.get('error_message', '')}"
            )

        legs: list[TravelLeg | None] = []
        elements = (payload.get("rows") or [{}])[0].get("elements") or []
        for el in elements:
            if el.get("status") != "OK":
                legs.append(None)
                continue
            legs.append(TravelLeg(
                duration_seconds=int(el["duration"]["value"]),
                duration_in_traffic_seconds=(int(el["duration_in_traffic"]["value"])
                                              if "duration_in_traffic" in el else None),
                distance_meters=int(el["distance"]["value"]),
            ))
        return legs


# ============================================================================
# HERE Matrix Routing API v8 — fully implemented
# ============================================================================

class HEREMatrixProvider(_ProviderBase):
    """HERE Matrix Routing v8 — N×M travel times + distances with live traffic.

    Endpoint  : POST https://matrix.router.hereapi.com/v8/matrix?async=false
    Auth      : ?apiKey=<KEY>  (or Authorization: Bearer <oauth_token>)
    Coords    : {"lat": x, "lng": y}  ← `lng`, NOT `lon`
    Region    : non-`world` regionDefinition unlocks live traffic. We default
                to a bounding box covering Israel.
    Layout    : matrix.travelTimes / matrix.distances are row-major flat arrays
                length numOrigins × numDestinations. With 1 origin and N dests
                this is just an N-element array.
    Traffic   : on by default when departureTime is unset or near-now. Set
                departureTime="any" to force free-flow (no traffic).
    """
    name = "here_matrix"
    supports_matrix          = True
    supports_isochrone       = False    # different endpoint (/v8/isolines)
    supports_route           = False    # use /v8/routes if you ever need it
    supports_traffic         = True
    supports_avoid_polygons  = False
    supports_avoid_features  = True
    supports_profile_params  = True

    # API allows up to 10,000 destinations per call. We cap at 100 to keep the
    # payload reasonable and stay well within request-body limits.
    max_destinations_per_matrix_call = 100

    BASE_URL = "https://matrix.router.hereapi.com"

    # `autoCircle` makes HERE compute a circle around the actual origins +
    # destinations of each request. This:
    #   • stays under the 400 km diameter limit no matter where in Israel
    #     the user is (we'd hit it with a country-wide boundingBox),
    #   • enables live traffic (any non-`world` region does),
    #   • takes only a margin (extra metres beyond the data points).
    # Fallback bounding box is provided in case a caller wants the static form.
    DEFAULT_REGION = {"type": "autoCircle", "margin": 1000}
    ISRAEL_BBOX = {"type": "boundingBox",
                   "north": 33.4, "south": 31.5, "west": 34.2, "east": 35.9}

    _MODE_MAP = {
        "driving":          "car",
        "driving-car":      "car",
        "driving-hgv":      "truck",
        "truck":            "truck",
        "walking":          "pedestrian",
        "foot-walking":     "pedestrian",
        "bicycling":        "bicycle",
        "cycling-regular":  "bicycle",
        "taxi":             "taxi",
        "bus":              "bus",
        "scooter":          "scooter",
    }

    _AVOID_MAP = {
        "tolls":     "tollRoad",      "tollways":  "tollRoad",
        "highways":  "controlledAccessHighway",
        "motorways": "controlledAccessHighway",
        "ferries":   "ferry",
        "tunnels":   "tunnel",
        "dirtRoad":  "dirtRoad",
    }

    def __init__(
        self,
        api_key: str,
        region_definition: dict | None = None,
        timeout: float = 30.0,
        routing_mode: str = "fast",          # "fast" | "short"
    ):
        self.api_key      = api_key
        self.region       = region_definition or self.DEFAULT_REGION
        self.timeout      = timeout
        self.routing_mode = routing_mode

    def _transport_mode(self, mode: str) -> str:
        return self._MODE_MAP.get(mode, "car")

    def matrix(self, origin, destinations, *, mode="driving", options=None):
        import json as _json
        from datetime import datetime, timezone
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError, URLError

        self._warn_unsupported(options)

        body: dict = {
            "origins":          [{"lat": origin.lat, "lng": origin.lng}],
            "destinations":     [{"lat": d.lat, "lng": d.lng} for d in destinations],
            "regionDefinition": self.region,
            "transportMode":    self._transport_mode(mode),
            "routingMode":      self.routing_mode,
            "matrixAttributes": ["travelTimes", "distances"],
        }

        # User-specified departure time — send as ISO 8601 with timezone.
        # Without this field HERE uses live traffic for "now".
        if options and options.depart_at:
            iso = datetime.fromtimestamp(options.depart_at, tz=timezone.utc) \
                          .isoformat(timespec="seconds")
            body["departureTime"] = iso

        if options and options.avoid_features:
            mapped = [self._AVOID_MAP[f] for f in options.avoid_features
                      if f in self._AVOID_MAP]
            if mapped:
                body["avoid"] = {"features": mapped}

        # Power-user escape hatch: caller can inject HERE-specific fields
        # (vehicle, ev, exclude, …) via options.extra["here_extras"].
        if options and options.extra:
            for k, v in options.extra.get("here_extras", {}).items():
                body[k] = v

        url = f"{self.BASE_URL}/v8/matrix?async=false&apiKey={self.api_key}"
        req = Request(
            url,
            data=_json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Accept":       "application/json"},
            method="POST",
        )

        try:
            with urlopen(req, timeout=self.timeout) as resp:
                payload = _json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            try:
                err_body = e.read().decode("utf-8", "replace")[:400]
            except Exception:
                err_body = ""
            raise RuntimeError(f"HERE Matrix HTTP {e.code}: {err_body}") from e
        except (URLError, socket.timeout) as e:
            raise RuntimeError(f"HERE Matrix network error: {e}") from e

        m = payload.get("matrix") or {}
        durations = m.get("travelTimes") or []
        distances = m.get("distances")  or []
        errors    = m.get("errorCodes") or []

        # 1 origin → N destinations: row-major layout reduces to N entries each.
        legs: list[TravelLeg | None] = []
        for i in range(len(destinations)):
            err  = errors[i] if i < len(errors) else 0
            dur  = durations[i] if i < len(durations) else None
            dist = distances[i] if i < len(distances) else None
            if err != 0 or dur is None:
                legs.append(None)
                continue
            legs.append(TravelLeg(
                duration_seconds=int(dur),
                duration_in_traffic_seconds=int(dur),  # HERE: traffic-aware by default
                distance_meters=int(dist) if dist is not None else 0,
            ))
        return legs


# ============================================================================
# OSRM (self-hosted or demo)
# ============================================================================

class OSRMProvider(_ProviderBase):
    name = "osrm"
    supports_matrix          = True
    supports_isochrone       = False  # OSRM doesn't ship isochrones
    supports_route           = True
    supports_traffic         = False
    supports_avoid_polygons  = False
    supports_avoid_features  = False
    supports_profile_params  = False
    # OSRM has no per-call API cap; URL length is the practical limit (GET).
    max_destinations_per_matrix_call = 99

    _MODE_MAP = {
        "driving": "driving", "driving-car": "driving", "driving-hgv": "driving",
        "walking": "foot", "foot-walking": "foot",
        "bicycling": "bike", "cycling-regular": "bike",
    }

    def __init__(self, base_url: str = "https://router.project-osrm.org",
                 profile: str = "driving", timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.profile = profile
        self.timeout = timeout

    def matrix(self, origin, destinations, *, mode="driving", options=None):
        # GET /table/v1/{profile}/{lng,lat;lng,lat;...}?sources=0&destinations=1;2;...&annotations=duration,distance
        # Table Service coordinates are lng,lat (NOT lat,lng) — the opposite
        # order from HERE/ORS. Origin goes first (index 0), destinations follow.
        import json as _json
        import socket
        from urllib.error import HTTPError, URLError
        from urllib.request import Request, urlopen

        self._warn_unsupported(options)

        profile = self._MODE_MAP.get(mode, self.profile)
        coords = [f"{origin.lng},{origin.lat}"] + [f"{d.lng},{d.lat}" for d in destinations]
        dest_indices = ";".join(str(i) for i in range(1, len(destinations) + 1))
        url = (f"{self.base_url}/table/v1/{profile}/{';'.join(coords)}"
               f"?sources=0&destinations={dest_indices}&annotations=duration,distance")

        req = Request(url, headers={"User-Agent": "post-branches-finder/1.0"})
        try:
            with urlopen(req, timeout=self.timeout) as resp:
                payload = _json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            try:
                err_body = e.read().decode("utf-8", "replace")[:400]
            except Exception:
                err_body = ""
            raise RuntimeError(f"OSRM HTTP {e.code}: {err_body}") from e
        except (URLError, socket.timeout) as e:
            # A read-timeout on resp.read() (as opposed to a connect-timeout)
            # raises a bare socket.timeout, not a URLError — must be caught
            # separately or it escapes the tier-fallback chain uncaught.
            reason = getattr(e, "reason", e)
            raise RuntimeError(f"OSRM unreachable: {reason}") from e

        if payload.get("code") != "Ok":
            raise RuntimeError(f"OSRM error: {payload.get('code')} — {payload.get('message', '')}")

        durations = (payload.get("durations") or [[]])[0]
        distances = (payload.get("distances") or [[]])[0]

        legs: list[TravelLeg | None] = []
        for i in range(len(destinations)):
            dur = durations[i] if i < len(durations) else None
            dist = distances[i] if i < len(distances) else None
            if dur is None:
                legs.append(None)
                continue
            legs.append(TravelLeg(
                duration_seconds=int(dur),
                duration_in_traffic_seconds=None,   # OSRM has no live traffic
                distance_meters=int(dist) if dist is not None else 0,
            ))
        return legs


# ============================================================================
# VALHALLA (self-hosted) — has isochrones natively
# ============================================================================

class ValhallaProvider(_ProviderBase):
    name = "valhalla"
    supports_matrix          = True
    supports_isochrone       = True   # /isochrone endpoint
    supports_route           = True
    supports_traffic         = False  # base build; some forks have it
    supports_avoid_polygons  = True   # /route accepts avoid_polygons
    supports_avoid_features  = True
    supports_profile_params  = True   # vehicle costing options
    # Valhalla self-hosted: configurable, default ~200.
    max_destinations_per_matrix_call = 199

    def __init__(self, base_url: str, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def matrix(self, origin, destinations, *, mode="driving", options=None):
        # POST /sources_to_targets  body={sources, targets, costing, costing_options}
        self._warn_unsupported(options)
        raise NotImplementedError("ValhallaProvider.matrix — wire the POST when you have a host.")

    def isochrone(self, origin, ranges_seconds, *, mode="driving", options=None):
        # POST /isochrone  body={locations, contours:[{time:5},{time:10}], polygons:true}
        self._warn_unsupported(options)
        raise NotImplementedError("ValhallaProvider.isochrone — wire the POST when you have a host.")


# ============================================================================
# WAZE — navigation-link only (NOT a routing API)
# ============================================================================

class WazeDeepLinkProvider(_ProviderBase):
    """Doesn't return travel time. Provides a deep-link to start navigation
    in the Waze app. Use as the 'Navigate' button after ranking."""
    name = "waze_deeplink"
    supports_matrix          = False
    supports_isochrone       = False
    supports_route           = False

    def deep_link(self, destination: Coordinate, *, navigate: bool = True) -> str:
        return f"https://waze.com/ul?ll={destination.lat},{destination.lng}&navigate={'yes' if navigate else 'no'}"


# ============================================================================
# MOCK — straight-line / constant-speed approximation
# ============================================================================

class MockHaversineProvider(_ProviderBase):
    """Pure-math 'routing' assuming constant `avg_kmh`. Great for unit tests
    and offline development."""
    name = "mock_haversine"
    supports_matrix          = True
    supports_isochrone       = True   # synthetic circles
    supports_route           = True
    supports_traffic         = False
    supports_avoid_polygons  = False
    supports_avoid_features  = False
    supports_profile_params  = False
    max_destinations_per_matrix_call = 1000   # no real cap

    def __init__(self, avg_kmh: float = 50.0, *,
                 traffic_multiplier: float | None = None,
                 name_override: str | None = None):
        self.avg_kmh = avg_kmh
        self.traffic_multiplier = traffic_multiplier
        if traffic_multiplier is not None:
            # Instance-level override of the class flag → behaves as a
            # traffic-aware provider (different cache namespace too).
            self.supports_traffic = True
            self.name = name_override or "mock_traffic"

    def _haversine_m(self, c1: Coordinate, c2: Coordinate) -> int:
        from geo_utils import haversine_m
        return int(haversine_m(c1.lng, c1.lat, c2.lng, c2.lat))

    def matrix(self, origin, destinations, *, mode="driving", options=None):
        self._warn_unsupported(options)
        legs = []
        for d in destinations:
            dist = self._haversine_m(origin, d)
            secs = int(dist / 1000 / self.avg_kmh * 3600)
            traffic_secs = None
            if self.traffic_multiplier is not None:
                # Add deterministic per-destination jitter so re-ranking actually
                # reorders things (instead of being a constant multiplier).
                jitter = 1.0 + 0.3 * (((dist % 137) / 137) - 0.5)
                traffic_secs = int(secs * self.traffic_multiplier * jitter)
            legs.append(TravelLeg(
                duration_seconds=secs,
                duration_in_traffic_seconds=traffic_secs,
                distance_meters=dist,
            ))
        return legs

    def isochrone(self, origin, ranges_seconds, *, mode="driving", options=None):
        from geo_utils import buffer_point_geojson
        results = []
        for r in sorted(ranges_seconds):
            radius_m = self.avg_kmh * 1000 / 3600 * r
            results.append(IsochroneResult(
                range_seconds=int(r),
                geometry_geojson=buffer_point_geojson(origin.lng, origin.lat, radius_m, vertices=32),
                properties={"value": int(r)},
            ))
        return results


# ============================================================================
# Geocoding providers
# ============================================================================

class NominatimProvider:
    """OpenStreetMap Nominatim — free, fair-use ≤1 req/sec.
    Significantly more accurate than ORS Pelias for Hebrew street/house lookups
    in Israel because OSM has rich Hebrew tags."""
    name = "nominatim"
    _last_call_ts = 0.0
    _throttle_lock = threading.Lock()

    def __init__(self, user_agent: str = "post-branches-finder/1.0", country: str = "il"):
        self.user_agent = user_agent
        self.country = country

    def geocode(self, address: str) -> GeocodeResult | None:
        import json as _json
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError, URLError

        with NominatimProvider._throttle_lock:
            wait = 1.0 - (time.time() - NominatimProvider._last_call_ts)
            if wait > 0:
                time.sleep(wait)
            NominatimProvider._last_call_ts = time.time()

        qs = urlencode({"q": address, "format": "json", "limit": 1,
                        "countrycodes": self.country, "addressdetails": 1})
        req = Request(f"https://nominatim.openstreetmap.org/search?{qs}",
                      headers={"User-Agent": self.user_agent})
        try:
            with urlopen(req, timeout=15) as resp:
                results = _json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            raise RuntimeError(f"Nominatim HTTP {e.code}") from e
        except (URLError, socket.timeout) as e:
            raise RuntimeError(f"Nominatim network error: {e}") from e
        if not results:
            return None
        r = results[0]
        if any(ch.isdigit() for ch in address) and not (r.get("address") or {}).get("road"):
            # The query included a house number but Nominatim's match has no
            # road component — it silently fell back to a city/locality-level
            # match. That's not the address the user asked for; treat as a miss
            # rather than return a misleadingly "successful" city-centroid pin.
            return None
        return GeocodeResult(
            lat=float(r["lat"]), lng=float(r["lon"]),
            formatted_address=r.get("display_name"), raw=r,
        )


class ChainedGeocodingProvider:
    """Try multiple geocoders in order — first non-None result wins.

    Why: ORS Pelias has poor coverage of Hebrew street/house addresses in
    Israel (returns city-level only). Nominatim handles them correctly
    because OSM has rich Hebrew tags. Chain the two so the strong one
    gets the address while ORS still serves autocomplete.

    The chain pretends to BE the primary provider for cache + quota purposes
    (same `name`), so the existing cache/quota tables don't need new rows.

    Autocomplete is delegated to the first chain member that supports it.
    """
    def __init__(self, providers: list, name: str | None = None):
        if not providers:
            raise ValueError("ChainedGeocodingProvider needs at least one provider")
        self.providers = providers
        self.name = name or providers[0].name
        self._auto = next((p for p in providers if hasattr(p, "autocomplete")), None)

    def geocode(self, address: str):
        last_error = None
        for p in self.providers:
            try:
                r = p.geocode(address)
                if r is not None:
                    return r
            except Exception as e:
                last_error = e
                continue
        if last_error:
            raise last_error
        return None

    def autocomplete(self, text: str, size: int = 5):
        if self._auto is None:
            return []
        return self._auto.autocomplete(text, size=size)


# ============================================================================
# Capability registry — picks a provider that satisfies a query.
# ============================================================================

def pick_provider(providers: list[RoutingProvider], **needs: bool) -> RoutingProvider | None:
    """Return the first provider that satisfies all `supports_*` requirements.

    Example:
        pick_provider([ors, mapbox, google], supports_traffic=True, supports_isochrone=True)
    """
    for p in providers:
        if all(getattr(p, k, False) == v for k, v in needs.items()):
            return p
    return None
