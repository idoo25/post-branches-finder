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

const MODE_SUBTITLES: Record<Mode, string> = {
  travel: "מצאו את 10 הסניפים הקרובים ביותר לפי זמן נסיעה אמיתי, כולל תנועה בזמן אמת.",
  nearby: "15 הסניפים הקרובים ביותר בקו אווירי — חיפוש מקומי מהיר, ללא שירותי ניתוב חיצוניים.",
  browse: "עיינו בכל סניפי הדואר בישראל, חפשו לפי שם, עיר או כתובת.",
};

export default function App() {
  const [mode, setMode] = useState<Mode>("travel");

  const [results, setResults] = useState<RankedBranch[]>([]);
  const [origin, setOrigin] = useState<{ lat: number; lng: number } | null>(null);
  const [providers, setProviders] = useState<SearchResponse["providers"] | null>(null);
  const [isEstimate, setIsEstimate] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [nearbyResults, setNearbyResults] = useState<RankedBranch[]>([]);
  const [nearbyOrigin, setNearbyOrigin] = useState<{ lat: number; lng: number } | null>(null);
  const [nearbyLoading, setNearbyLoading] = useState(false);
  const [nearbyError, setNearbyError] = useState<string | null>(null);

  const [allBranches, setAllBranches] = useState<BranchSummary[] | null>(null);
  const [browseLoading, setBrowseLoading] = useState(false);
  const [browseError, setBrowseError] = useState<string | null>(null);
  const [browseFilter, setBrowseFilter] = useState("");
  const [focusedBranch, setFocusedBranch] = useState<number | null>(null);

  const [hoveredRank, setHoveredRank] = useState<number | null>(null);
  const [selectedRank, setSelectedRank] = useState<number | null>(null);

  const browseFetchInFlightRef = useRef(false);
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
      if (requestId !== searchRequestIdRef.current) return;
      setResults(r.results);
      setOrigin(r.origin);
      setProviders(r.providers);
      setIsEstimate(r.is_estimate);
      if (r.results.length === 0) {
        setError("לא נמצאו סניפים בקרבת הכתובת.");
      } else {
        prefetchBranches(r.results.map((x) => x.branch_number));
      }
    } catch (e) {
      if (requestId !== searchRequestIdRef.current) return;
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
      if (requestId !== nearbyRequestIdRef.current) return;
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
      if (requestId !== nearbyRequestIdRef.current) return;
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
        <div className="header-inner">
          <div className="brand">
            <div className="brand-icon" aria-hidden>
              <svg width="22" height="22" viewBox="0 0 32 32" fill="none">
                <path d="M6 10.5 16 18 26 10.5" stroke="#fff" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" />
                <rect x="6" y="8" width="20" height="15" rx="2" stroke="#fff" strokeWidth="2.4" />
              </svg>
            </div>
            <div className="brand-text">
              <span className="brand-name">איתור סניפי דואר</span>
              <span className="brand-tag">מערכת חיפוש סניפים חכמה</span>
            </div>
          </div>
          <span className="header-badge">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden>
              <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="2" />
              <path d="M12 6v6l4 2" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
            זמן נסיעה בזמן אמת
          </span>
        </div>
      </header>

      <section className="hero">
        <div className="hero-card">
          <div className="hero-top">
            <h1 className="hero-title">איפה הסניף הקרוב אליי?</h1>
            <p className="hero-subtitle">{MODE_SUBTITLES[mode]}</p>
            <ModeTabs mode={mode} onChange={setMode} />
          </div>

          <div className="hero-body">
            {mode === "travel" && (
              <>
                <AddressInput onSubmit={handleSearch} loading={loading} />
                {providers && (
                  <div className="info-banner info-banner--muted">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
                      <path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20Z" stroke="currentColor" strokeWidth="2" />
                      <path d="M12 8v4M12 16h.01" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                    </svg>
                    <span>
                      ניתוב: <b>{providers.routing}</b>
                      {providers.traffic ? (
                        <> · תנועה חיה: <b>{providers.traffic}</b></>
                      ) : (
                        <> · תנועה חיה: <span className="prov-disabled">לא פעיל</span></>
                      )}
                      {providers.geocoder && <> · גיוקודר: <b>{providers.geocoder}</b></>}
                    </span>
                  </div>
                )}
                {isEstimate && (
                  <div className="info-banner info-banner--warning">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
                      <path d="M12 9v4M12 17h.01" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                      <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
                    </svg>
                    <span>
                      שירותי הניתוב והתנועה החיה אינם זמינים כרגע — הזמנים שמוצגים הם{" "}
                      <b>הערכה גסה</b> לפי מרחק קו אווירי, לא זמן נסיעה אמיתי.
                    </span>
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
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
                      <circle cx="12" cy="12" r="3" stroke="currentColor" strokeWidth="2" />
                      <path d="M12 2v3M12 19v3M2 12h3M19 12h3" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                    </svg>
                    {nearbyLoading ? "מאתר…" : "השתמש במיקום שלי"}
                  </button>
                  <AddressInput
                    onSubmit={(address, coord) => runNearby(coord ? { lat: coord.lat, lng: coord.lng } : { address })}
                    loading={nearbyLoading}
                  />
                </div>
                <div className="info-banner info-banner--muted">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
                    <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0Z" stroke="currentColor" strokeWidth="2" />
                    <circle cx="12" cy="10" r="3" stroke="currentColor" strokeWidth="2" />
                  </svg>
                  <span>
                    15 הסניפים הקרובים ביותר בקו אווירי, ברדיוס אדפטיבי — ללא קריאה לשירות ניתוב חיצוני.
                    {furthestNearbyKm !== null && <> טווח החיפוש הגיע עד <b>{furthestNearbyKm.toFixed(2)} ק״מ</b>.</>}
                  </span>
                </div>
                {nearbyError && <div className="error-msg">{nearbyError}</div>}
              </>
            )}

            {mode === "browse" && (
              <div className="info-banner info-banner--muted">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
                  <path d="M3 7h18M3 12h18M3 17h18" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                </svg>
                <span>
                  חיפוש מקומי בכל {allBranches?.length ?? "…"} הסניפים לפי שם, עיר או כתובת — נטען פעם אחת, בלי קריאות רשת נוספות.
                </span>
              </div>
            )}
          </div>
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
                headerText={mode === "travel" ? "10 הסניפים הקרובים אליך" : "15 הסניפים הקרובים (קו אווירי)"}
                loadingText={mode === "travel" ? "מחפש את הסניפים הקרובים…" : "מאתר סניפים קרובים…"}
                emptyTitle={mode === "travel" ? "מוכנים לחיפוש" : "בחרו מיקום או כתובת"}
                emptyHint={
                  mode === "travel"
                    ? "הזינו כתובת למעלה ולחצו חיפוש כדי לראות את הסניפים הקרובים על המפה"
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
