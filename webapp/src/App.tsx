import { useEffect, useRef, useState } from "react";
import { AddressInput } from "./components/AddressInput";
import { BranchList } from "./components/BranchList";
import { BranchMap } from "./components/BranchMap";
import { BrowsePanel } from "./components/BrowsePanel";
import { ModeTabs, type Mode } from "./components/ModeTabs";
import {
  prefetchBranches,
  search,
  nearbyByAirDistance,
  fetchAllBranches,
  type RankedBranch,
  type SearchResponse,
  type BranchSummary,
} from "./api";

export default function App() {
  const [mode, setMode] = useState<Mode>("travel");

  // ---- travel mode: real travel time (existing 3-tier pipeline) ----
  const [results, setResults] = useState<RankedBranch[]>([]);
  const [origin, setOrigin] = useState<{ lat: number; lng: number } | null>(null);
  const [providers, setProviders] = useState<SearchResponse["providers"] | null>(null);
  const [isEstimate, setIsEstimate] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // ---- nearby mode: air-distance only, adaptive radius, no routing API ----
  const [nearbyResults, setNearbyResults] = useState<RankedBranch[]>([]);
  const [nearbyOrigin, setNearbyOrigin] = useState<{ lat: number; lng: number } | null>(null);
  const [nearbyLoading, setNearbyLoading] = useState(false);
  const [nearbyError, setNearbyError] = useState<string | null>(null);

  // ---- browse mode: all branches, local text filter, no API after first load ----
  const [allBranches, setAllBranches] = useState<BranchSummary[] | null>(null);
  const [browseLoading, setBrowseLoading] = useState(false);
  const [browseError, setBrowseError] = useState<string | null>(null);
  const [browseFilter, setBrowseFilter] = useState("");
  const [focusedBranch, setFocusedBranch] = useState<number | null>(null);

  // shared selection state for the two ranked-list modes (travel / nearby)
  const [hoveredRank, setHoveredRank] = useState<number | null>(null);
  const [selectedRank, setSelectedRank] = useState<number | null>(null);

  // re-entrancy guard for the browse-mode fetch effect below — a ref (not state)
  // so that flipping it does NOT retrigger the effect; only `mode`/`allBranches`
  // changing should do that. `browseLoading` state remains purely for the UI spinner.
  const browseFetchInFlightRef = useRef(false);

  // monotonically increasing request ids so a slow, stale response can never
  // clobber the state written by a later request that already resolved.
  const searchRequestIdRef = useRef(0);
  const nearbyRequestIdRef = useRef(0);

  useEffect(() => {
    setHoveredRank(null);
    setSelectedRank(null);
  }, [mode]);

  useEffect(() => {
    if (mode !== "browse" || allBranches !== null || browseFetchInFlightRef.current) return;
    browseFetchInFlightRef.current = true;
    setBrowseLoading(true);
    setBrowseError(null);
    fetchAllBranches()
      .then(setAllBranches)
      .catch((e) => setBrowseError(String(e)))
      .finally(() => {
        browseFetchInFlightRef.current = false;
        setBrowseLoading(false);
      });
  }, [mode, allBranches]);

  async function handleSearch(address: string, coord?: { lat: number; lng: number }) {
    const requestId = ++searchRequestIdRef.current;
    setLoading(true);
    setError(null);
    setSelectedRank(null);
    setHoveredRank(null);
    try {
      const r = await search({ address, lat: coord?.lat, lng: coord?.lng });
      if (requestId !== searchRequestIdRef.current) return; // a newer search superseded this one
      setResults(r.results);
      setOrigin(r.origin);
      setProviders(r.providers);
      setIsEstimate(r.is_estimate);
      if (r.results.length === 0) {
        setError("לא נמצאו סניפים בקרבת הכתובת.");
      } else {
        // Warm the localStorage cache so clicking the expand-arrow is instant.
        prefetchBranches(r.results.map((x) => x.branch_number));
      }
    } catch (e) {
      if (requestId !== searchRequestIdRef.current) return; // a newer search superseded this one
      setError(String(e));
      setResults([]);
      setOrigin(null);
      setIsEstimate(false);
    } finally {
      if (requestId === searchRequestIdRef.current) setLoading(false);
    }
  }

  async function runNearby(params: { lat?: number; lng?: number; address?: string }) {
    const requestId = ++nearbyRequestIdRef.current;
    setNearbyLoading(true);
    setNearbyError(null);
    setSelectedRank(null);
    setHoveredRank(null);
    try {
      const r = await nearbyByAirDistance(params);
      if (requestId !== nearbyRequestIdRef.current) return; // a newer nearby search superseded this one
      const asRanked: RankedBranch[] = r.results.map((x) => ({
        ...x,
        duration_min: null,
        duration_in_traffic_min: null,
        cache_hit: false,
      }));
      setNearbyResults(asRanked);
      setNearbyOrigin(r.origin);
      if (r.results.length === 0) setNearbyError("לא נמצאו סניפים בקרבת מקום.");
      else prefetchBranches(r.results.map((x) => x.branch_number));
    } catch (e) {
      if (requestId !== nearbyRequestIdRef.current) return; // a newer nearby search superseded this one
      setNearbyError(String(e));
      setNearbyResults([]);
      setNearbyOrigin(null);
    } finally {
      if (requestId === nearbyRequestIdRef.current) setNearbyLoading(false);
    }
  }

  function useMyLocation() {
    if (!("geolocation" in navigator)) {
      setNearbyError("הדפדפן הזה לא תומך באיתור מיקום. אפשר להזין כתובת במקום.");
      return;
    }
    setNearbyLoading(true);
    setNearbyError(null);
    navigator.geolocation.getCurrentPosition(
      (pos) => runNearby({ lat: pos.coords.latitude, lng: pos.coords.longitude }),
      (err) => {
        setNearbyLoading(false);
        setNearbyError(`לא הצלחנו לקבל את המיקום שלך (${err.message}). אפשר להזין כתובת במקום.`);
      },
      { enableHighAccuracy: true, timeout: 10_000 },
    );
  }

  const activeResults = mode === "travel" ? results : nearbyResults;
  const activeOrigin = mode === "travel" ? origin : nearbyOrigin;
  const activeLoading = mode === "travel" ? loading : nearbyLoading;
  const furthestNearbyKm =
    mode === "nearby" && nearbyResults.length > 0
      ? nearbyResults[nearbyResults.length - 1].distance_km
      : null;

  return (
    <div className="app">
      <header className="app-header">
        <div className="logo-strip">
          <div className="logo-mark">דואר ישראל</div>
          <div className="logo-tag">איתור סניפים לפי זמן נסיעה אמיתי</div>
        </div>
      </header>

      <section className="hero">
        <div className="hero-inner">
          <h1 className="hero-title">איתור סניפים וזימון תור בקליק</h1>
          <ModeTabs mode={mode} onChange={setMode} />

          {mode === "travel" && (
            <>
              <AddressInput onSubmit={handleSearch} loading={loading} />
              {providers && (
                <div className="prov-row" title="הספקים שמשרתים את הבקשה">
                  ניתוב: <b>{providers.routing}</b>
                  {providers.traffic ? (
                    <> · תנועה חיה: <b>{providers.traffic}</b></>
                  ) : (
                    <> · תנועה חיה: <span className="prov-disabled">לא פעיל</span></>
                  )}
                  {providers.geocoder && <> · גיוקודר: <b>{providers.geocoder}</b></>}
                </div>
              )}
              {isEstimate && (
                <div className="error-msg">
                  ⚠ שירותי הניתוב והתנועה החיה אינם זמינים כרגע (מכסה יומית נגמרה) — הזמנים שמוצגים הם{" "}
                  <b>הערכה גסה</b> לפי מרחק קו אווירי חלקי מהירות נסיעה ממוצעת, לא זמן נסיעה אמיתי.
                </div>
              )}
              {error && <div className="error-msg">{error}</div>}
            </>
          )}

          {mode === "nearby" && (
            <>
              <div className="nearby-row">
                <button
                  type="button"
                  className="geo-btn"
                  onClick={useMyLocation}
                  disabled={nearbyLoading}
                >
                  {nearbyLoading ? "מאתר…" : "📍 השתמש במיקום שלי"}
                </button>
                <AddressInput
                  onSubmit={(address, coord) => runNearby(coord ? { lat: coord.lat, lng: coord.lng } : { address })}
                  loading={nearbyLoading}
                />
              </div>
              <div className="prov-row">
                15 הסניפים הקרובים ביותר קו אווירי, ברדיוס אדפטיבי — לגמרי מקומי, ללא קריאה לשירות ניתוב חיצוני.
                {furthestNearbyKm !== null && <> טווח החיפוש בפועל הגיע עד <b>{furthestNearbyKm.toFixed(2)} ק״מ</b>.</>}
              </div>
              {nearbyError && <div className="error-msg">{nearbyError}</div>}
            </>
          )}

          {mode === "browse" && (
            <div className="prov-row">
              חיפוש מקומי בכל {allBranches?.length ?? "…"} הסניפים לפי שם, עיר או כתובת — נטען פעם אחת, בלי קריאות רשת נוספות.
            </div>
          )}
        </div>
      </section>

      <main className="main-grid">
        {mode === "browse" ? (
          <BrowsePanel
            branches={allBranches}
            loading={browseLoading}
            error={browseError}
            filter={browseFilter}
            setFilter={setBrowseFilter}
            focused={focusedBranch}
            setFocused={setFocusedBranch}
          />
        ) : (
          <>
            <div className="map-col">
              <BranchMap
                results={activeResults}
                origin={activeOrigin}
                hoveredRank={hoveredRank}
                selectedRank={selectedRank}
                onPinClick={(rank) => setSelectedRank(selectedRank === rank ? null : rank)}
              />
            </div>
            <aside className="list-col">
              <BranchList
                results={activeResults}
                loading={activeLoading}
                hoveredRank={hoveredRank}
                setHoveredRank={setHoveredRank}
                selectedRank={selectedRank}
                setSelectedRank={setSelectedRank}
                headerText={mode === "travel" ? "10 הסניפים הקרובים אליך" : "15 הסניפים הקרובים אליך (קו אווירי)"}
                loadingText={mode === "travel" ? "מחפש את הסניפים הקרובים…" : "מאתר סניפים קרובים…"}
                emptyTitle={mode === "travel" ? "10 הסניפים הקרובים אליך" : "15 הסניפים הקרובים אליך (קו אווירי)"}
                emptyHint={
                  mode === "travel"
                    ? "הזינו כתובת למעלה כדי להתחיל"
                    : "לחצו על 'השתמש במיקום שלי' או הזינו כתובת למעלה"
                }
              />
            </aside>
          </>
        )}
      </main>
    </div>
  );
}
