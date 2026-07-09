export type Mode = "travel" | "nearby" | "browse";

interface Props {
  mode: Mode;
  onChange: (m: Mode) => void;
}

const TABS: { key: Mode; label: string; icon: JSX.Element }[] = [
  {
    key: "travel",
    label: "זמן נסיעה",
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
        <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="2" />
        <path d="M12 6v6l4 2" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      </svg>
    ),
  },
  {
    key: "nearby",
    label: "קו אווירי",
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
        <path d="M21 10c0 7-9 13-9 13s-9-6-9-13a9 9 0 0 1 18 0Z" stroke="currentColor" strokeWidth="2" />
        <circle cx="12" cy="10" r="3" stroke="currentColor" strokeWidth="2" />
      </svg>
    ),
  },
  {
    key: "browse",
    label: "כל הסניפים",
    icon: (
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
        <rect x="3" y="3" width="7" height="7" rx="1" stroke="currentColor" strokeWidth="2" />
        <rect x="14" y="3" width="7" height="7" rx="1" stroke="currentColor" strokeWidth="2" />
        <rect x="3" y="14" width="7" height="7" rx="1" stroke="currentColor" strokeWidth="2" />
        <rect x="14" y="14" width="7" height="7" rx="1" stroke="currentColor" strokeWidth="2" />
      </svg>
    ),
  },
];

export function ModeTabs({ mode, onChange }: Props) {
  return (
    <div className="mode-tabs" role="tablist" aria-label="מצבי חיפוש">
      {TABS.map((t) => (
        <button
          key={t.key}
          type="button"
          role="tab"
          aria-selected={mode === t.key}
          className={`mode-tab${mode === t.key ? " is-active" : ""}`}
          onClick={() => onChange(t.key)}
        >
          <span className="mode-tab-icon">{t.icon}</span>
          <span className="mode-tab-label">{t.label}</span>
        </button>
      ))}
    </div>
  );
}
