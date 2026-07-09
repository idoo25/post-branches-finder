import { useEffect, useMemo, useRef } from "react";
import L from "leaflet";
import { MapContainer, TileLayer, Marker, useMap } from "react-leaflet";
import type { RankedBranch } from "../api";

interface Props {
  results: RankedBranch[];
  origin: { lat: number; lng: number } | null;
  hoveredRank: number | null;
  selectedRank: number | null;
  onPinClick: (rank: number) => void;
}

function makePinIcon(rank: number, kind: "default" | "hover" | "selected"): L.DivIcon {
  const bg =
    kind === "selected" ? "#A80B1E" : kind === "hover" ? "#C40E22" : "#D40E26";
  const ring =
    kind === "selected" ? "0 0 0 4px rgba(168,11,30,0.25)" :
    kind === "hover"    ? "0 0 0 6px rgba(212,14,38,0.28)" : "none";
  const html = `
    <div class="pin" style="
      background:${bg};
      box-shadow:0 1px 3px rgba(0,0,0,0.4), ${ring};
    ">${rank}</div>`;
  return L.divIcon({
    html,
    className: "pin-icon",
    iconSize: [34, 34],
    iconAnchor: [17, 17],
  });
}

function originIcon(): L.DivIcon {
  return L.divIcon({
    html: `<div class="origin-pin" title="המוצא שלך"></div>`,
    className: "origin-icon",
    iconSize: [22, 22],
    iconAnchor: [11, 11],
  });
}

function FitBounds({
  results,
  origin,
}: {
  results: RankedBranch[];
  origin: { lat: number; lng: number } | null;
}) {
  const map = useMap();
  useEffect(() => {
    if (!results.length && !origin) return;
    const points: [number, number][] = [];
    if (origin) points.push([origin.lat, origin.lng]);
    for (const r of results) points.push([r.latitude, r.longitude]);
    if (points.length === 0) return;
    if (points.length === 1) {
      map.setView(points[0], 14);
    } else {
      const bounds = L.latLngBounds(points);
      map.fitBounds(bounds, { padding: [40, 40], maxZoom: 16 });
    }
  }, [results, origin, map]);
  return null;
}

export function BranchMap({
  results,
  origin,
  hoveredRank,
  selectedRank,
  onPinClick,
}: Props) {
  // cache icons per rank+state to avoid re-creating per render
  const icons = useMemo(() => {
    const m = new Map<string, L.DivIcon>();
    for (const r of results) {
      m.set(`${r.rank}-default`, makePinIcon(r.rank, "default"));
      m.set(`${r.rank}-hover`, makePinIcon(r.rank, "hover"));
      m.set(`${r.rank}-selected`, makePinIcon(r.rank, "selected"));
    }
    return m;
  }, [results]);

  const center: [number, number] = origin ? [origin.lat, origin.lng] : [31.5, 34.85];

  return (
    <div className="map-wrap">
      <MapContainer center={center} zoom={8} className="leaflet-map" scrollWheelZoom>
        <TileLayer
          url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          attribution='&copy; <a href="https://openstreetmap.org/copyright">OpenStreetMap</a>'
        />
        {origin && (
          <Marker position={[origin.lat, origin.lng]} icon={originIcon()} />
        )}
        {results.map((r) => {
          const state =
            selectedRank === r.rank ? "selected" :
            hoveredRank === r.rank ? "hover" : "default";
          return (
            <Marker
              key={r.branch_number}
              position={[r.latitude, r.longitude]}
              icon={icons.get(`${r.rank}-${state}`) ?? makePinIcon(r.rank, state)}
              eventHandlers={{ click: () => onPinClick(r.rank) }}
              zIndexOffset={state === "selected" ? 1000 : state === "hover" ? 500 : 0}
            />
          );
        })}
        <FitBounds results={results} origin={origin} />
      </MapContainer>
    </div>
  );
}
