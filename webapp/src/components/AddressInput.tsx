import { useEffect, useRef, useState } from "react";
import { autocomplete, type Suggestion } from "../api";

interface Props {
  onSubmit: (address: string, coord?: { lat: number; lng: number }) => void;
  loading: boolean;
}

const DEBOUNCE_MS = 250;

export function AddressInput({ onSubmit, loading }: Props) {
  const [text, setText] = useState("");
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [showList, setShowList] = useState(false);
  const [highlight, setHighlight] = useState(-1);
  const wrapRef = useRef<HTMLDivElement>(null);
  const timer = useRef<number | null>(null);
  const lastQuery = useRef("");

  useEffect(() => {
    if (timer.current) window.clearTimeout(timer.current);
    if (text.trim().length < 2) {
      setSuggestions([]);
      return;
    }
    const q = text;
    timer.current = window.setTimeout(async () => {
      try {
        lastQuery.current = q;
        const r = await autocomplete(q);
        if (q === lastQuery.current) {
          // Pelias autocomplete sometimes returns 0 results for Hebrew when a
          // house number is appended (e.g. "זלמן ארן 96"). Don't drop the
          // existing suggestions in that case — the user is mid-typing and
          // the previous results are still relevant context.
          if (r.suggestions.length > 0) setSuggestions(r.suggestions);
        }
      } catch {
        /* ignore — autocomplete is non-critical */
      }
    }, DEBOUNCE_MS);
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [text]);

  // close dropdown on click-outside
  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setShowList(false);
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, []);

  function submit(value: string, coord?: { lat: number; lng: number }) {
    setShowList(false);
    setText(value);
    onSubmit(value, coord);
  }

  function onKey(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setShowList(true);
      setHighlight((h) => Math.min(h + 1, suggestions.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlight((h) => Math.max(h - 1, -1));
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (loading) return;
      if (highlight >= 0 && suggestions[highlight]) {
        const s = suggestions[highlight];
        submit(s.label, { lat: s.lat, lng: s.lng });
      } else if (text.trim()) {
        submit(text.trim());
      }
    } else if (e.key === "Escape") {
      setShowList(false);
    }
  }

  return (
    <div className="addr-wrap" ref={wrapRef}>
      <div className="addr-row">
        <button
          className="addr-search"
          disabled={loading || !text.trim()}
          onClick={() => text.trim() && submit(text.trim())}
        >
          {loading ? "מחפש…" : "חיפוש"}
        </button>
        <input
          className="addr-input"
          type="text"
          value={text}
          placeholder="הזינו כתובת (למשל: דיזנגוף 50, תל אביב)"
          aria-label="הזינו כתובת לחיפוש"
          role="combobox"
          aria-expanded={showList && suggestions.length > 0}
          aria-controls="addr-suggest-listbox"
          aria-activedescendant={
            showList && highlight >= 0 && suggestions[highlight]
              ? `addr-suggest-option-${highlight}`
              : undefined
          }
          onChange={(e) => {
            setText(e.target.value);
            setShowList(true);
            setHighlight(-1);
          }}
          onFocus={() => setShowList(true)}
          onKeyDown={onKey}
          dir="rtl"
        />
      </div>
      {showList && suggestions.length > 0 && (
        <ul className="addr-suggest" role="listbox" id="addr-suggest-listbox">
          {suggestions.map((s, i) => (
            <li
              key={`${s.lat}-${s.lng}-${i}`}
              id={`addr-suggest-option-${i}`}
              role="option"
              aria-selected={i === highlight}
              className={i === highlight ? "is-highlighted" : ""}
              onMouseEnter={() => setHighlight(i)}
              onMouseDown={(e) => e.preventDefault()}  // keep input focus
              onClick={() => submit(s.label, { lat: s.lat, lng: s.lng })}
            >
              {s.label}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
