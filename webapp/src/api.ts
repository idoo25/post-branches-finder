// All HTTP calls to the FastAPI backend.

const API = "/api";

export interface Suggestion {
  label: string;
  lat: number;
  lng: number;
}

export interface RankedBranch {
  rank: number;
  branch_number: number;
  branch_name: string;
  city: string;
  full_address: string;
  telephone: string | null;
  latitude: number;
  longitude: number;
  distance_km: number;
  duration_min: number | null;
  duration_in_traffic_min: number | null;
  cache_hit: boolean;
}

export interface SearchResponse {
  origin: { lat: number; lng: number };
  providers: { routing: string; traffic: string | null; geocoder: string | null };
  is_estimate: boolean;
  results: RankedBranch[];
}

// ---- browse: every branch, lightweight (no distance/duration) ----
export interface BranchSummary {
  branch_number: number;
  branch_name: string;
  branch_type: string | null;
  city: string;
  full_address: string;
  telephone: string | null;
  latitude: number;
  longitude: number;
}

// ---- nearby: air-distance only, no routing/traffic API call ----
export interface NearbyBranch {
  rank: number;
  branch_number: number;
  branch_name: string;
  city: string;
  full_address: string;
  telephone: string | null;
  latitude: number;
  longitude: number;
  distance_km: number;
}

export interface NearbyResponse {
  origin: { lat: number; lng: number };
  results: NearbyBranch[];
}

export interface BranchHour {
  day_num: number;
  morning_open: string | null;
  morning_close: string | null;
  afternoon_open: string | null;
  afternoon_close: string | null;
  closed: boolean;
}

export interface BranchDetail {
  branch_number: number;
  branch_name: string;
  branch_type: string | null;
  city: string;
  full_address: string;
  zip: string | null;
  latitude: number;
  longitude: number;
  telephone: string | null;
  hours: BranchHour[];
  services: Record<string, string[]>;
  extra_services: string[];
  accessibility: string[];
}

async function getJSON<T>(path: string): Promise<T> {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}
async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status}: ${await r.text()}`);
  return r.json();
}

export function autocomplete(q: string): Promise<{ suggestions: Suggestion[] }> {
  return getJSON(`${API}/autocomplete?q=${encodeURIComponent(q)}&size=5`);
}
export function search(params: { address: string; lat?: number; lng?: number }): Promise<SearchResponse> {
  return postJSON(`${API}/search`, { air_pool: 50, drive_pool: 20, final_k: 10, ...params });
}

export function nearbyByAirDistance(params: {
  lat?: number; lng?: number; address?: string; k?: number;
}): Promise<NearbyResponse> {
  return postJSON(`${API}/nearby`, { k: 15, ...params });
}

// All branches — fetched once per session and cached in memory + localStorage.
// Local-only after the first load: no further network calls for browsing or
// searching by name/address, it's all client-side filtering over this list.
let allBranchesMemCache: BranchSummary[] | null = null;
const ALL_BRANCHES_CACHE_KEY = "post-branches:all:v1";
const ALL_BRANCHES_TTL_MS = 24 * 60 * 60 * 1000;

export async function fetchAllBranches(): Promise<BranchSummary[]> {
  if (allBranchesMemCache) return allBranchesMemCache;
  try {
    const raw = localStorage.getItem(ALL_BRANCHES_CACHE_KEY);
    if (raw) {
      const { data, fetchedAt } = JSON.parse(raw);
      if (Date.now() - fetchedAt < ALL_BRANCHES_TTL_MS) {
        allBranchesMemCache = data as BranchSummary[];
        return allBranchesMemCache;
      }
    }
  } catch {
    /* corrupt cache entry — fall through to a fresh fetch */
  }
  const r = await getJSON<{ branches: BranchSummary[] }>(`${API}/branches`);
  allBranchesMemCache = r.branches;
  try {
    localStorage.setItem(ALL_BRANCHES_CACHE_KEY, JSON.stringify({ data: r.branches, fetchedAt: Date.now() }));
  } catch {
    /* quota exceeded — silently drop */
  }
  return allBranchesMemCache;
}

// ---------------------------------------------------------------------------
// localStorage cache for branch details — keeps the expand-arrow click fast.
//
// Branch metadata (hours, services, accessibility) changes rarely, so a 7-day
// TTL is generous. Bumping CACHE_VERSION invalidates everything client-side.
// ---------------------------------------------------------------------------
const CACHE_VERSION = 1;
const CACHE_PREFIX  = `branch:v${CACHE_VERSION}:`;
const CACHE_TTL_MS  = 7 * 24 * 60 * 60 * 1000;

export function getCachedBranch(branch_number: number): BranchDetail | null {
  try {
    const raw = localStorage.getItem(CACHE_PREFIX + branch_number);
    if (!raw) return null;
    const { data, fetchedAt } = JSON.parse(raw);
    if (Date.now() - fetchedAt > CACHE_TTL_MS) {
      localStorage.removeItem(CACHE_PREFIX + branch_number);
      return null;
    }
    return data as BranchDetail;
  } catch {
    return null;
  }
}

function setCachedBranch(branch_number: number, data: BranchDetail): void {
  try {
    localStorage.setItem(
      CACHE_PREFIX + branch_number,
      JSON.stringify({ data, fetchedAt: Date.now() }),
    );
  } catch {
    /* quota exceeded — silently drop */
  }
}

export async function branchDetail(branch_number: number): Promise<BranchDetail> {
  const cached = getCachedBranch(branch_number);
  if (cached) return cached;
  const data = await getJSON<BranchDetail>(`${API}/branch/${branch_number}`);
  setCachedBranch(branch_number, data);
  return data;
}

// In-flight dedupe for prefetch — avoids duplicate requests if the same branch
// number appears in two consecutive searches.
const inflight = new Map<number, Promise<BranchDetail>>();

/** Fire-and-forget background warm-up. Call right after a search finishes. */
export function prefetchBranches(branch_numbers: number[]): void {
  for (const num of branch_numbers) {
    if (getCachedBranch(num)) continue;
    if (inflight.has(num)) continue;
    const p = branchDetail(num).finally(() => inflight.delete(num));
    inflight.set(num, p);
  }
}
