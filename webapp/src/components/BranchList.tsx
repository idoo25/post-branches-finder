import { useEffect, useRef } from "react";
import type { RankedBranch } from "../api";
import { BranchDetail } from "./BranchDetail";

interface Props {
  results: RankedBranch[];
  loading: boolean;
  hoveredRank: number | null;
  setHoveredRank: (r: number | null) => void;
  selectedRank: number | null;
  setSelectedRank: (r: number | null) => void;
  headerText?: string;
  loadingText?: string;
  emptyTitle?: string;
  emptyHint?: string;
}

export function BranchList({
  results,
  loading,
  hoveredRank,
  setHoveredRank,
  selectedRank,
  setSelectedRank,
  headerText = "10 הסניפים הקרובים אליך",
  loadingText = "מחפש את הסניפים הקרובים…",
  emptyTitle = "10 הסניפים הקרובים אליך",
  emptyHint = "הזינו כתובת למעלה כדי להתחיל",
}: Props) {
  // Clicking a map pin selects a rank that may currently be scrolled out of
  // view in the list — without this, the detail opens but the user never
  // sees it happen. Bring the selected row into view whenever it changes,
  // and again whenever its height changes: BranchDetail loads its data
  // asynchronously, so the row is still just a small "loading" placeholder
  // at the moment we first scroll, then grows once the real content (hours
  // table, contact, services) renders — re-scroll after that growth too.
  const selectedRef = useRef<HTMLLIElement | null>(null);
  useEffect(() => {
    const el = selectedRef.current;
    if (!el || selectedRank === null) return;
    el.scrollIntoView({ behavior: "smooth", block: "start" });
    const ro = new ResizeObserver(() => {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [selectedRank]);

  if (loading) {
    return <div className="list-empty">{loadingText}</div>;
  }
  if (results.length === 0) {
    return (
      <div className="list-empty">
        <div className="list-empty-title">{emptyTitle}</div>
        <div className="list-empty-sub">{emptyHint}</div>
      </div>
    );
  }
  return (
    <div className="list-wrap">
      <div className="list-header">{headerText}</div>
      <ul className="branch-list" role="list">
        {results.map((r) => {
          const isSel = selectedRank === r.rank;
          const isHov = hoveredRank === r.rank;
          const traffic = r.duration_in_traffic_min ?? r.duration_min;
          return (
            <li
              key={r.branch_number}
              ref={isSel ? selectedRef : undefined}
              className={`branch-item${isSel ? " is-selected" : ""}${isHov ? " is-hovered" : ""}`}
              onMouseEnter={() => setHoveredRank(r.rank)}
              onMouseLeave={() => setHoveredRank(null)}
            >
              <button
                type="button"
                className="branch-row"
                onClick={() => setSelectedRank(isSel ? null : r.rank)}
                aria-expanded={isSel}
              >
                <span className="branch-rank">{r.rank}</span>
                <div className="branch-text">
                  <div className="branch-name">
                    {r.branch_name}
                    <span className="branch-num">({r.branch_number})</span>
                  </div>
                  <div className="branch-addr">{r.full_address}</div>
                  <div className="branch-meta">
                    {traffic !== null && (
                      <>
                        <span className="meta-time">~{traffic.toFixed(1)} דק׳</span>
                        <span className="meta-sep">·</span>
                      </>
                    )}
                    <span className="meta-dist">{r.distance_km.toFixed(2)} ק״מ</span>
                    {r.duration_in_traffic_min !== null &&
                      r.duration_min !== null &&
                      Math.abs(r.duration_in_traffic_min - r.duration_min) > 0.2 && (
                        <span className="meta-traffic">
                          (פקקים: {(r.duration_in_traffic_min - r.duration_min).toFixed(1)} דק׳)
                        </span>
                      )}
                  </div>
                </div>
                <span className={`expand-arrow${isSel ? " is-open" : ""}`} aria-hidden>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                    <path d="M7 14l5-5 5 5" stroke="#E63946" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                </span>
              </button>
              {isSel && (
                <div className="branch-detail-wrap">
                  <BranchDetail branchNumber={r.branch_number} />
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
