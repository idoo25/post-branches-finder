import { useEffect, useMemo, useRef } from "react";
import L from "leaflet";
import { CircleMarker, MapContainer, TileLayer, useMap } from "react-leaflet";
import type { BranchSummary } from "../api";
import { BranchDetail } from "./BranchDetail";

interface Props {
  branches: BranchSummary[] | null;
  loading: boolean;
  error: string | null;
  filter: string;
  setFilter: (v: string) => void;
  focused: number | null;
  setFocused: (n: number | null) => void;
}

const LIST_CAP = 300;

function FitAll({ points }: { points: [number, number][] }) {
  const map = useMap();
  const didFit = useRef(false);
  useEffect(() => {
    if (didFit.current || points.length === 0) return;
    didFit.current = true;
    map.fitBounds(L.latLngBounds(points), { padding: [30, 30], maxZoom: 12 });
  }, [points, map]);
  return null;
}

function FlyTo({ target }: { target: [number, number] | null }) {
  const map = useMap();
  useEffect(() => {
    if (target) map.flyTo(target, Math.max(map.getZoom(), 15), { duration: 0.6 });
  }, [target, map]);
  return null;
}

export function BrowsePanel({ branches, loading, error, filter, setFilter, focused, setFocused }: Props) {
  const filtered = useMemo(() => {
    if (!branches) return [];
    const q = filter.trim().toLowerCase();
    if (!q) return branches;
    return branches.filter(
      (b) =>
        b.branch_name.toLowerCase().includes(q) ||
        (b.city ?? "").toLowerCase().includes(q) ||
        b.full_address.toLowerCase().includes(q) ||
        String(b.branch_number).includes(q),
    );
  }, [branches, filter]);

  const allPoints = useMemo<[number, number][]>(
    () => (branches ?? []).map((b) => [b.latitude, b.longitude]),
    [branches],
  );

  const focusedBranch = useMemo(
    () => (focused != null ? (branches ?? []).find((b) => b.branch_number === focused) ?? null : null),
    [branches, focused],
  );
  const flyTarget: [number, number] | null = focusedBranch
    ? [focusedBranch.latitude, focusedBranch.longitude]
    : null;

  // Clicking a map pin focuses a branch that may be scrolled out of view in
  // the list (or, before the fix below, not rendered at all) — bring it into
  // view whenever the focus changes, and again whenever its height changes:
  // BranchDetail loads its data asynchronously, so the row is still just a
  // small "loading" placeholder at the moment we first scroll, then grows
  // once the real content renders — re-scroll after that growth too.
  const focusedRef = useRef<HTMLLIElement | null>(null);
  useEffect(() => {
    const el = focusedRef.current;
    if (!el || focused === null) return;
    el.scrollIntoView({ behavior: "smooth", block: "start" });
    const ro = new ResizeObserver(() => {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [focused]);

  // The map renders every filtered marker, but the list caps at LIST_CAP for
  // performance — so clicking a marker outside the first LIST_CAP would set
  // `focused` with no matching <li> ever rendered to show the detail in.
  // Make sure the focused branch always has a row, even if it's off-cap.
  const visibleBranches = useMemo(() => {
    const capped = filtered.slice(0, LIST_CAP);
    if (focusedBranch && !capped.some((b) => b.branch_number === focusedBranch.branch_number)) {
      return [focusedBranch, ...capped];
    }
    return capped;
  }, [filtered, focusedBranch]);

  return (
    <>
      <div className="map-col">
        <div className="map-wrap">
          <MapContainer center={[31.5, 34.85]} zoom={8} className="leaflet-map" scrollWheelZoom preferCanvas>
            <TileLayer
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
              attribution='&copy; <a href="https://openstreetmap.org/copyright">OpenStreetMap</a>'
            />
            {filtered.map((b) => (
              <CircleMarker
                key={b.branch_number}
                center={[b.latitude, b.longitude]}
                radius={focused === b.branch_number ? 9 : 5}
                pathOptions={{
                  color: "#fff",
                  weight: 1,
                  fillColor: focused === b.branch_number ? "#7c0a17" : "#E63946",
                  fillOpacity: 0.85,
                }}
                eventHandlers={{ click: () => setFocused(focused === b.branch_number ? null : b.branch_number) }}
              />
            ))}
            <FitAll points={allPoints} />
            <FlyTo target={flyTarget} />
          </MapContainer>
        </div>
      </div>
      <aside className="list-col">
        <div className="browse-search">
          <input
            className="browse-input"
            type="text"
            placeholder="חפשו לפי שם סניף, עיר או כתובת…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            dir="rtl"
          />
        </div>
        {loading && <div className="list-empty">טוען את כל הסניפים…</div>}
        {error && <div className="error-msg" style={{ margin: 12 }}>{error}</div>}
        {!loading && !error && (
          <>
            <div className="list-header">
              {filter
                ? `${filtered.length} תוצאות מתוך ${branches?.length ?? 0}`
                : `כל ${branches?.length ?? 0} הסניפים`}
            </div>
            {visibleBranches.length === 0 ? (
              <div className="list-empty">
                <div className="list-empty-title">לא נמצאו סניפים תואמים</div>
                <div className="list-empty-sub">נסו לחפש לפי שם סניף, עיר או כתובת אחרים</div>
              </div>
            ) : (
              <ul className="branch-list" role="list">
                {visibleBranches.map((b) => {
                  const isSel = focused === b.branch_number;
                  return (
                    <li
                      key={b.branch_number}
                      ref={isSel ? focusedRef : undefined}
                      className={`branch-item${isSel ? " is-selected" : ""}`}
                    >
                      <button
                        type="button"
                        className="branch-row"
                        onClick={() => setFocused(isSel ? null : b.branch_number)}
                        aria-expanded={isSel}
                      >
                        <span className="branch-dot" aria-hidden />
                        <div className="branch-text">
                          <div className="branch-name">
                            {b.branch_name}
                            <span className="branch-num">({b.branch_number})</span>
                          </div>
                          <div className="branch-addr">{b.full_address}</div>
                        </div>
                        <span className={`expand-arrow${isSel ? " is-open" : ""}`} aria-hidden>
                          <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                            <path d="M7 14l5-5 5 5" stroke="#E63946" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
                          </svg>
                        </span>
                      </button>
                      {isSel && (
                        <div className="branch-detail-wrap">
                          <BranchDetail branchNumber={b.branch_number} />
                        </div>
                      )}
                    </li>
                  );
                })}
              </ul>
            )}
            {filtered.length > LIST_CAP && (
              <div className="browse-more-hint">
                מוצגים {LIST_CAP} התוצאות הראשונות מתוך {filtered.length} — כל הסניפים התואמים כן מופיעים על המפה. צמצמו את החיפוש כדי לראות רשימה מדויקת יותר.
              </div>
            )}
          </>
        )}
      </aside>
    </>
  );
}
