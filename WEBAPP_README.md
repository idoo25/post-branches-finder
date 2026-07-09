# האתר — איתור סניפים בקליק

צינור 50→20→10 (אווירי → ORS → traffic-aware) חי במסך אחד. עיצוב בהשראת `doar.israelpost.co.il`.

## הפעלה

צריך **Python 3.11+** ו-**Node 18+**.

### שלב 1 — תלויות חד-פעמיות
```bash
pip install fastapi uvicorn
cd webapp
npm install
cd ..
```

### שלב 2 — בנייה ראשונית של ה-DB
```bash
python build_db.py
```

### שלב 3 — מפתחות API ב-`.env` (כבר קיים)
```
ORS_API_KEY=...        # כבר מוגדר
GOOGLE_API_KEY=...     # אופציונלי — בלעדיו השלב השלישי משתמש ב-mock_traffic
```

### שלב 4 — הפעלת שני שרתים במקביל

**מסוף 1 — FastAPI** (port 8000):
```bash
python -m uvicorn server:app --port 8000 --reload
```

**מסוף 2 — Vite dev** (port 5173):
```bash
cd webapp
npm run dev
```

פתח ב-דפדפן: <http://localhost:5173>

### לפריסה (production)
```bash
cd webapp && npm run build       # → webapp/dist
cd .. && python -m uvicorn server:app --port 8000
```
ה-FastAPI יגיש את ה-static build על הפורט הראשי — כתובת אחת, ללא Vite.

## איך זה עובד

```
┌────────────────────────────────────────────────────────────────────┐
│                          BROWSER (React)                           │
│  ┌────────────────────────┐    ┌──────────────────────────────┐    │
│  │ Header (RTL, אדום)     │    │  Address autocomplete        │    │
│  │                        │    │  (debounced, ESC/↑↓/Enter)   │    │
│  └────────────────────────┘    └──────────────────────────────┘    │
│  ┌──────────────────────┐ ┌─────────────────────────────────┐      │
│  │ Map (Leaflet+OSM)    │ │ List 1-10  (numbered, hover →   │      │
│  │ ┌────────────────┐   │ │   pin highlights, click →       │      │
│  │ │  pins 1..10    │◄──┼─┤   detail panel expands inline)  │      │
│  │ │  origin pin    │   │ │ ┌─────────────────────────────┐ │      │
│  │ │  fitBounds     │───┼─┤►│ Detail: hours, services,    │ │      │
│  │ └────────────────┘   │ │ │   accessibility, Waze link  │ │      │
│  └──────────────────────┘ └─────────────────────────────────┘      │
└──────────────────────────────────│──────────────────────────────────┘
                                   ▼  /api/...
┌────────────────────────────────────────────────────────────────────┐
│                       FastAPI (server.py)                          │
│  GET  /api/autocomplete?q=...  → ORS /geocode/autocomplete         │
│  POST /api/search              → svc.find_nearest_with_traffic()    │
│  GET  /api/branch/{n}          → BranchIndex + DB hours/services   │
└────────────────────────────────────────────────────────────────────┘
                                   │
                                   ▼
                  NearestBranchService (3-tier pipeline)
                       50 ─[air]→ 20 ─[ORS]→ 10 ─[traffic]→ list
```

## פיצ'רים שאומתו ב-preview

| תכונה | מצב |
|---|---|
| Autocomplete של כתובות (debounced 250ms) | ✅ עובד עם ORS Pelias |
| חיפוש 10 הסניפים הקרובים | ✅ ORS routing + mock traffic |
| מפה עם 10 פינים ממוספרים 1-10 | ✅ |
| פין מוצא נפרד (כחול) | ✅ |
| FitBounds אוטומטי לכל הפינים | ✅ |
| Click על פין → פתיחת פרטי סניף ברשימה | ✅ |
| Click על סניף ברשימה → סימון פין על המפה | ✅ |
| Hover על סניף → highlight של הפין | ✅ |
| פאנל פרטי סניף (שעות, שירותים, נגישות) | ✅ |
| כפתור Waze | ✅ |
| RTL מלא | ✅ |
| ניתוב /api דרך Vite proxy | ✅ |

## קבצי פרויקט

```
post_branches_db/
├── server.py                        ← FastAPI (3 endpoints)
├── .env                             ← מפתחות API (לא ב-git)
└── webapp/
    ├── package.json
    ├── vite.config.ts               ← /api proxy → :8000
    ├── tsconfig.json
    ├── index.html                   ← RTL, Hebrew title, Leaflet CSS
    └── src/
        ├── main.tsx                 ← React mount
        ├── App.tsx                  ← layout, state-lifting
        ├── api.ts                   ← fetch helpers + types
        ├── styles.css               ← העיצוב הדואר-ישראלי
        └── components/
            ├── AddressInput.tsx     ← autocomplete + debounce + keyboard nav
            ├── BranchMap.tsx        ← Leaflet, numbered DivIcons, fitBounds
            ├── BranchList.tsx       ← רשימה ממוספרת + hover/click handlers
            └── BranchDetail.tsx     ← אקורדיון פרטים (שעות/שירותים/נגישות)
```

## להחליף את הספק

```python
# server.py — שתי שורות
from providers import GoogleDistanceMatrixProvider, GoogleRoutesProvider, MapboxMatrixProvider, OSRMProvider, ValhallaProvider

# במקום ORS השתמש בכל אחד מאלה — אותו interface, אותו cache, אותה quota:
routing = OSRMProvider("http://localhost:5000")              # self-hosted
routing = MapboxMatrixProvider(access_token=MAPBOX_TOKEN)    # paid
routing = GoogleRoutesProvider(api_key=GOOGLE_KEY)           # paid + traffic
```
