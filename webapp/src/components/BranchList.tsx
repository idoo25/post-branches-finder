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

function SkeletonList() {
  return (
    <div className="skeleton-list">
      {[1, 2, 3, 4].map((i) => (
        <div key={i} className="skeleton-item">
          <div className="skeleton-circle" />
          <div className="skeleton-lines">
            <div className="skeleton-line skeleton-line--medium" />
            <div className="skeleton-line skeleton-line--short" />
          </div>
        </div>
      ))}
    </div>
  );
}

function EmptyIcon() {
  return (
    <div className="list-empty-icon" aria-hidden>
      <svg width="28" height="28" viewBox="0 0 24 24" fill="none">
        <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0Z" stroke="currentColor" strokeWidth="1.5" />
        <circle cx="12" cy="10" r="3" stroke="currentColor" strokeWidth="1.5" />
      </svg>
    </div>
  );
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
  emptyTitle = "מוכנים לחיפוש",
  emptyHint = "הזינו כתובת למעלה כדי להתחיל",
}: Props) {
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
    return (
      <div className="list-wrap">
        <div className="list-header">
          <span className="list-header-title">{loadingText}</span>
        </div>
        <SkeletonList />
      </div>
    );
  }

  if (results.length === 0) {
    return (
      <div className="list-empty">
        <EmptyIcon />
        <div className="list-empty-title">{emptyTitle}</div>
        <div className="list-empty-sub">{emptyHint}</div>
      </div>
    );
  }

  return (
    <div className="list-wrap">
      <div className="list-header">
        <span className="list-header-title">{headerText}</span>
        <span className="list-header-count">{results.length} תוצאות</span>
      </div>
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
                      <span className="meta-badge meta-badge--time">
                        ~{traffic.toFixed(1)} דק׳
                      </span>
                    )}
                    <span className="meta-badge meta-badge--dist">
                      {r.distance_km.toFixed(2)} ק״מ
                    </span>
                    {r.duration_in_traffic_min !== null &&
                      r.duration_min !== null &&
                      Math.abs(r.duration_in_traffic_min - r.duration_min) > 0.2 && (
                        <span className="meta-traffic">
                          +{(r.duration_in_traffic_min - r.duration_min).toFixed(1)} דק׳ פקקים
                        </span>
                      )}
                  </div>
                </div>
                <span className="expand-arrow" aria-hidden>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none">
                    <path d="M7 14l5-5 5 5" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
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
