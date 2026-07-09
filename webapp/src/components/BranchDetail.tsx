import { useEffect, useState } from "react";
import { branchDetail, getCachedBranch, type BranchDetail as B } from "../api";

const DAY_NAMES = ["", "ראשון", "שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת"];

function fmtRange(open: string | null, close: string | null): string | null {
  if (!open || !close) return null;
  const a = open.length >= 5 ? open.substring(0, 5) : open;
  const b = close.length >= 5 ? close.substring(0, 5) : close;
  return `${a} - ${b}`;
}

export function BranchDetail({ branchNumber }: { branchNumber: number }) {
  // Initial render: try the localStorage cache synchronously — if it's there
  // (warmed by App's prefetchBranches), the panel paints instantly with no
  // spinner and no /api/branch round-trip.
  const [data, setData] = useState<B | null>(() => getCachedBranch(branchNumber));
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const cached = getCachedBranch(branchNumber);
    if (cached) {
      setData(cached);
      setError(null);
      return;
    }
    setData(null);
    setError(null);
    let alive = true;
    branchDetail(branchNumber)
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(String(e)));
    return () => {
      alive = false;
    };
  }, [branchNumber]);

  if (error) return <div className="detail-error">שגיאה: {error}</div>;
  if (!data) return <div className="detail-loading">טוען פרטי סניף…</div>;

  const cats = Object.entries(data.services);

  return (
    <div className="detail">
      <section className="detail-section">
        <h4 className="detail-h">שעות פעילות</h4>
        <table className="hours-table">
          <tbody>
            {data.hours.map((h) => {
              const m = fmtRange(h.morning_open, h.morning_close);
              const a = fmtRange(h.afternoon_open, h.afternoon_close);
              const txt = h.closed ? "סגור" : [m, a].filter(Boolean).join(" | ") || "—";
              return (
                <tr key={h.day_num}>
                  <td className="hours-day">יום {DAY_NAMES[h.day_num] ?? h.day_num}</td>
                  <td className="hours-val">{txt}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>

      <section className="detail-section">
        <h4 className="detail-h">יצירת קשר</h4>
        <div className="contact">
          <div>
            <span className="contact-k">טלפון</span>
            <a className="contact-v" href={`tel:${data.telephone || "171"}`}>
              {data.telephone || "171"}
            </a>
          </div>
          {data.zip && (
            <div>
              <span className="contact-k">מיקוד</span>
              <span className="contact-v">{data.zip}</span>
            </div>
          )}
        </div>
      </section>

      {cats.length > 0 && (
        <section className="detail-section">
          <h4 className="detail-h">השירותים בסניף</h4>
          <div className="services">
            {cats.map(([cat, names]) => (
              <details key={cat} className="svc-cat">
                <summary>
                  {cat} <span className="svc-count">({names.length})</span>
                </summary>
                <ul>
                  {names.map((n) => <li key={n}>{n}</li>)}
                </ul>
              </details>
            ))}
          </div>
        </section>
      )}

      {data.extra_services.length > 0 && (
        <section className="detail-section">
          <h4 className="detail-h">שירותים נוספים</h4>
          <div className="chips">
            {data.extra_services.map((e) => <span key={e} className="chip">{e}</span>)}
          </div>
        </section>
      )}

      {data.accessibility.length > 0 && (
        <section className="detail-section">
          <h4 className="detail-h">נגישות</h4>
          <div className="chips">
            {data.accessibility.map((a) => <span key={a} className="chip chip-ac">{a}</span>)}
          </div>
        </section>
      )}

      <a
        className="waze-link"
        href={`https://waze.com/ul?ll=${data.latitude},${data.longitude}&navigate=yes`}
        target="_blank"
        rel="noreferrer"
      >
        ניווט בוויז ←
      </a>
    </div>
  );
}
