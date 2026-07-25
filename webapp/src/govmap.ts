// GovMap (govmap.gov.il) — the official Israeli government geocoder, used as
// the first-choice address resolver when available.
//
// Called from the BROWSER only: the API key is domain-locked to this app's
// GitHub Pages origin, which is exactly GovMap's supported usage model (the
// key is harmless to expose — requests from any other origin are rejected).
// The resolved coordinate is passed straight to our backend as the search
// origin; nothing from GovMap is stored anywhere.
//
// In local dev the key env var is absent, so govmapGeocode() no-ops and the
// server's own geocoding chain (verified-address canonicalization + Nominatim
// + ORS) handles everything — same as when GovMap is down or finds nothing.

const API_KEY = import.meta.env.VITE_GOVMAP_API_KEY as string | undefined;
const ENDPOINT = "https://www.govmap.gov.il/api/search-service/api-search";

// ---------------------------------------------------------------------------
// ITM (EPSG:2039, Israel TM Grid) -> WGS84.
// GovMap returns centroids in ITM meters, not lat/lng. Inverse Transverse
// Mercator on GRS80, then the Survey of Israel's published 7-parameter
// Helmert shift (coordinate-frame rotation convention — validated against two
// independent references: Nominatim (18.6m) and Israel Post's own feed (7.3m)).
// ---------------------------------------------------------------------------

const A = 6378137.0; // GRS80 semi-major axis
const F = 1 / 298.257222101;
const E2 = F * (2 - F);
const EP2 = E2 / (1 - E2);

// EPSG:2039 projection constants
const LAT0 = ((31 + 44 / 60 + 3.817 / 3600) * Math.PI) / 180;
const LON0 = ((35 + 12 / 60 + 16.261 / 3600) * Math.PI) / 180;
const K0 = 1.0000067;
const FE = 219529.584;
const FN = 626907.39;

// Israel 1993 -> WGS84 Helmert parameters (Survey of Israel)
const DX = -24.0024, DY = -17.1032, DZ = -17.8444;
const RX = (-0.33077 / 3600) * (Math.PI / 180);
const RY = (-1.85269 / 3600) * (Math.PI / 180);
const RZ = (1.66969 / 3600) * (Math.PI / 180);
const DS = 5.4262e-6;

// WGS84 ellipsoid
const WA = 6378137.0;
const WF = 1 / 298.257223563;
const WE2 = WF * (2 - WF);

function meridianArc(phi: number): number {
  return (
    A *
    ((1 - E2 / 4 - (3 * E2 * E2) / 64 - (5 * E2 ** 3) / 256) * phi -
      ((3 * E2) / 8 + (3 * E2 * E2) / 32 + (45 * E2 ** 3) / 1024) * Math.sin(2 * phi) +
      ((15 * E2 * E2) / 256 + (45 * E2 ** 3) / 1024) * Math.sin(4 * phi) -
      ((35 * E2 ** 3) / 3072) * Math.sin(6 * phi))
  );
}

export function itmToWgs84(easting: number, northing: number): [number, number] {
  // --- inverse Transverse Mercator on GRS80 (Israel 1993 datum) ---
  const M = meridianArc(LAT0) + (northing - FN) / K0;
  const mu = M / (A * (1 - E2 / 4 - (3 * E2 * E2) / 64 - (5 * E2 ** 3) / 256));
  const e1 = (1 - Math.sqrt(1 - E2)) / (1 + Math.sqrt(1 - E2));
  const phi1 =
    mu +
    ((3 * e1) / 2 - (27 * e1 ** 3) / 32) * Math.sin(2 * mu) +
    ((21 * e1 * e1) / 16 - (55 * e1 ** 4) / 32) * Math.sin(4 * mu) +
    ((151 * e1 ** 3) / 96) * Math.sin(6 * mu) +
    ((1097 * e1 ** 4) / 512) * Math.sin(8 * mu);

  const sin1 = Math.sin(phi1);
  const cos1 = Math.cos(phi1);
  const tan1 = Math.tan(phi1);
  const C1 = EP2 * cos1 * cos1;
  const T1 = tan1 * tan1;
  const N1 = A / Math.sqrt(1 - E2 * sin1 * sin1);
  const R1 = (A * (1 - E2)) / Math.pow(1 - E2 * sin1 * sin1, 1.5);
  const D = (easting - FE) / (N1 * K0);

  const phi =
    phi1 -
    ((N1 * tan1) / R1) *
      ((D * D) / 2 -
        ((5 + 3 * T1 + 10 * C1 - 4 * C1 * C1 - 9 * EP2) * D ** 4) / 24 +
        ((61 + 90 * T1 + 298 * C1 + 45 * T1 * T1 - 252 * EP2 - 3 * C1 * C1) * D ** 6) / 720);
  const lam =
    LON0 +
    (D -
      ((1 + 2 * T1 + C1) * D ** 3) / 6 +
      ((5 - 2 * C1 + 28 * T1 - 3 * C1 * C1 + 8 * EP2 + 24 * T1 * T1) * D ** 5) / 120) /
      cos1;

  // --- geodetic -> geocentric (GRS80) ---
  const N = A / Math.sqrt(1 - E2 * Math.sin(phi) ** 2);
  const x = N * Math.cos(phi) * Math.cos(lam);
  const y = N * Math.cos(phi) * Math.sin(lam);
  const z = N * (1 - E2) * Math.sin(phi);

  // --- Helmert (coordinate-frame convention: negate published rotations) ---
  const rx = -RX, ry = -RY, rz = -RZ;
  const s = 1 + DS;
  const x2 = DX + s * (x + rz * y - ry * z);
  const y2 = DY + s * (-rz * x + y + rx * z);
  const z2 = DZ + s * (ry * x - rx * y + z);

  // --- geocentric -> geodetic (WGS84), fixed-point iteration ---
  const lam2 = Math.atan2(y2, x2);
  const p = Math.hypot(x2, y2);
  let phi2 = Math.atan2(z2, p * (1 - WE2));
  for (let i = 0; i < 6; i++) {
    const N2 = WA / Math.sqrt(1 - WE2 * Math.sin(phi2) ** 2);
    phi2 = Math.atan2(z2 + WE2 * N2 * Math.sin(phi2), p);
  }

  return [(phi2 * 180) / Math.PI, (lam2 * 180) / Math.PI];
}

// ---------------------------------------------------------------------------

const WKT_POINT_RE = /^POINT\s*\(\s*([\d.]+)\s+([\d.]+)\s*\)$/;

interface GovmapResult {
  text?: unknown;
  centroid?: unknown;
}

/** Parse one api-search result into a WGS84 point, or null if unusable. */
function resultToPoint(r: GovmapResult): { lat: number; lng: number } | null {
  if (typeof r?.centroid !== "string") return null;
  const m = WKT_POINT_RE.exec(r.centroid);
  if (!m) return null;
  const [lat, lng] = itmToWgs84(parseFloat(m[1]), parseFloat(m[2]));
  // Sanity: must land inside Israel's bounding box, else refuse the result.
  if (lat < 29 || lat > 33.5 || lng < 34 || lng > 36) return null;
  return { lat, lng };
}

async function apiSearch(
  searchText: string,
  opts: { maxResults: number; isAccurate: boolean; timeoutMs: number },
): Promise<GovmapResult[] | null> {
  if (!API_KEY || !searchText.trim()) return null;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), opts.timeoutMs);
  try {
    const resp = await fetch(ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        searchText,
        language: "he",
        maxResults: opts.maxResults,
        isAccurate: opts.isAccurate,
        apiKey: API_KEY,
      }),
      signal: ctrl.signal,
    });
    if (!resp.ok) return null;
    const data = await resp.json();
    return Array.isArray(data?.results) ? (data.results as GovmapResult[]) : null;
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

/** Resolve a free-text Israeli address via GovMap. Returns WGS84 coordinates,
 * or null on ANY miss/failure — callers fall back to the server's chain. */
export async function govmapGeocode(address: string): Promise<{ lat: number; lng: number } | null> {
  const results = await apiSearch(address, { maxResults: 5, isAccurate: true, timeoutMs: 6500 });
  for (const r of results ?? []) {
    const pt = resultToPoint(r);
    if (pt) return pt;
  }
  return null;
}

/** As-you-type suggestions via GovMap (isAccurate=false tolerates partial
 * input). Returns null/[] on miss — callers fall back to the server's
 * autocomplete so the user never sees the difference. Verified behavior:
 * partial street+number queries work well; partial settlement names may
 * return nothing (the fallback covers those). */
export async function govmapAutocomplete(
  query: string,
): Promise<Array<{ label: string; lat: number; lng: number }> | null> {
  const results = await apiSearch(query, { maxResults: 5, isAccurate: false, timeoutMs: 3500 });
  if (!results) return null;
  const out: Array<{ label: string; lat: number; lng: number }> = [];
  for (const r of results) {
    const pt = resultToPoint(r);
    if (pt && typeof r.text === "string" && r.text.trim()) {
      out.push({ label: r.text, ...pt });
    }
  }
  return out;
}
