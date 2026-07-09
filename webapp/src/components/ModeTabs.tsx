export type Mode = "travel" | "nearby" | "browse";

interface Props {
  mode: Mode;
  onChange: (m: Mode) => void;
}

const TABS: { key: Mode; label: string }[] = [
  { key: "travel", label: "לפי זמן נסיעה" },
  { key: "nearby", label: "קו אווירי סביבי" },
  { key: "browse", label: "כל הסניפים וחיפוש" },
];

export function ModeTabs({ mode, onChange }: Props) {
  return (
    <div className="mode-tabs" role="tablist">
      {TABS.map((t) => (
        <button
          key={t.key}
          type="button"
          role="tab"
          aria-selected={mode === t.key}
          className={`mode-tab${mode === t.key ? " is-active" : ""}`}
          onClick={() => onChange(t.key)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
