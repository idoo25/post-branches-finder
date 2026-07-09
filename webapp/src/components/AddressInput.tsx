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
          if (r.suggestions.length > 0) setSuggestions(r.suggestions);
        }
      } catch {
        /* ignore */
      }
    }, DEBOUNCE_MS);
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [text]);

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
        <span className="addr-search-icon" aria-hidden>
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none">
            <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="2" />
            <path d="M20 20l-3-3" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          </svg>
        </span>
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
        <button
          className="addr-search"
          disabled={loading || !text.trim()}
          onClick={() => text.trim() && submit(text.trim())}
        >
          {loading ? (
            "מחפש…"
          ) : (
            <>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden>
                <circle cx="11" cy="11" r="7" stroke="currentColor" strokeWidth="2.5" />
                <path d="M20 20l-3-3" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
              </svg>
              חיפוש
            </>
          )}
        </button>
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
              onMouseDown={(e) => e.preventDefault()}
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
