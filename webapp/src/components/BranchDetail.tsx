import { useEffect, useState } from "react";
import { branchDetail, getCachedBranch, type BranchDetail as B } from "../api";

const DAY_NAMES = ["", "ראשון", "שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת"];

function fmtRange(open: string | null, close: string | null): string | null {
  if (!open || !close) return null;
  const a = open.length >= 5 ? open.substring(0, 5) : open;
  const b = close.length >= 5 ? close.substring(0, 5) : close;
  return `${a} - ${b}`;
}

function SectionIcon({ children }: { children: React.ReactNode }) {
  return <span className="detail-h-icon">{children}</span>;
}

export function BranchDetail({ branchNumber }: { branchNumber: number }) {
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
        <h4 className="detail-h">
          <SectionIcon>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden>
              <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="2" />
              <path d="M12 6v6l4 2" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
            </svg>
          </SectionIcon>
          שעות פעילות
        </h4>
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
        <h4 className="detail-h">
          <SectionIcon>
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden>
              <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.127.96.361 1.903.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.907.339 1.85.573 2.81.7A2 2 0 0 1 22 16.92Z" stroke="currentColor" strokeWidth="2" />
            </svg>
          </SectionIcon>
          יצירת קשר
        </h4>
        <div className="contact">
          <div className="contact-card">
            <span className="contact-k">טלפון</span>
            <a className="contact-v" href={`tel:${data.telephone || "171"}`}>
              {data.telephone || "171"}
            </a>
          </div>
          {data.zip && (
            <div className="contact-card">
              <span className="contact-k">מיקוד</span>
              <span className="contact-v" style={{ color: "var(--ink)" }}>{data.zip}</span>
            </div>
          )}
        </div>
      </section>

      {cats.length > 0 && (
        <section className="detail-section">
          <h4 className="detail-h">
            <SectionIcon>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden>
                <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z" stroke="currentColor" strokeWidth="2" />
              </svg>
            </SectionIcon>
            השירותים בסניף
          </h4>
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
          <h4 className="detail-h">
            <SectionIcon>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden>
                <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2Z" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" />
              </svg>
            </SectionIcon>
            שירותים נוספים
          </h4>
          <div className="chips">
            {data.extra_services.map((e) => <span key={e} className="chip">{e}</span>)}
          </div>
        </section>
      )}

      {data.accessibility.length > 0 && (
        <section className="detail-section">
          <h4 className="detail-h">
            <SectionIcon>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" aria-hidden>
                <circle cx="12" cy="4" r="2" stroke="currentColor" strokeWidth="2" />
                <path d="M12 6v6M8 10h8M10 18l2-6 2 6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </SectionIcon>
            נגישות
          </h4>
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
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" aria-hidden>
          <path d="M12 2C8.13 2 5 5.13 5 9c0 5.25 7 13 7 13s7-7.75 7-13c0-3.87-3.13-7-7-7Z" fill="#053456" />
          <circle cx="12" cy="9" r="2.5" fill="#33CCFF" />
        </svg>
        ניווט בוויז
      </a>
    </div>
  );
}
