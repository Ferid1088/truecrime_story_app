"use client";

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ZoomIn, ZoomOut } from "lucide-react";
import { api, apiFileUrl } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatTimecode, humanize, isRtl, langLabel } from "@/lib/format";
import type { ProductionScriptData } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { TableSkeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/state";
import { cn } from "@/lib/utils";
import { useProduction } from "../hooks";
import { LanguagePicker, type LanguageTabProps } from "../shared";

/** Pixels per second. */
const ZOOMS = [0.25, 0.5, 1, 2, 4, 8, 16, 32];
/** Default zoom: the largest level that keeps the whole film within this width. */
const FIT_WIDTH_PX = 2400;
const TICK_STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600];
const MIN_TICK_SPACING_PX = 72;
const RULER_HEIGHT = 24;

type TrackKey = "beats" | "voice" | "visual" | "overlay" | "music" | "silence";

const TRACKS: { key: TrackKey; label: string; height: number }[] = [
  { key: "beats", label: "Beats", height: 26 },
  { key: "voice", label: "Voice", height: 30 },
  { key: "visual", label: "Visual", height: 52 },
  { key: "overlay", label: "Overlay", height: 30 },
  { key: "music", label: "Music", height: 40 },
  { key: "silence", label: "Silence", height: 24 },
];

const TRACK_TOPS = TRACKS.map((_, i) =>
  TRACKS.slice(0, i).reduce((top, t) => top + t.height, RULER_HEIGHT),
);
const TRACKS_HEIGHT = TRACKS.reduce((h, t) => h + t.height, 0);

interface TimelineItem {
  key: string;
  track: TrackKey;
  start: number;
  end: number;
  label: string;
  /** Hover tooltip and accessible name. */
  summary: string;
  className: string;
  /** Music: beds in the top lane, moments in the bottom lane. */
  lane?: "top" | "bottom";
  thumb?: string;
  /** Language of on-screen text (overlays). */
  textLang?: string;
  /** Visual shots: how the picture enters (marked on its left edge). */
  transition?: string;
  details: [string, string][];
}

const SHOT_CLASS: Record<string, string> = {
  image: "bg-indigo-500/35",
  document: "bg-amber-500/35",
  map: "bg-emerald-500/35",
  black: "bg-zinc-900 text-zinc-300",
};

const OVERLAY_CLASS: Record<string, string> = {
  quote: "bg-violet-500/35",
  date: "bg-amber-500/35",
  place: "bg-emerald-500/35",
  label: "bg-rose-500/35",
  credit: "bg-zinc-500/25",
};

const MUSIC_CLASS: Record<string, string> = {
  bed: "bg-teal-500/30",
  music_bridge: "bg-indigo-500/45",
  emotional_moment: "bg-pink-500/45",
  chapter_break: "bg-purple-500/45",
  sting: "bg-rose-500/55",
  silence: "bg-zinc-500/25",
};

const range = (start: number, end: number) =>
  `${formatTimecode(start, { tenths: true })}–${formatTimecode(end, { tenths: true })}`;

function buildItems(script: ProductionScriptData, thumbs: Map<string, string>, language: string): TimelineItem[] {
  const items: TimelineItem[] = [];
  (script.beats ?? []).forEach((b, i) =>
    items.push({
      key: `beat-${i}`,
      track: "beats",
      start: b.start,
      end: b.end,
      label: b.beat_id,
      summary: `Beat ${b.beat_id}, ${range(b.start, b.end)}`,
      className: i % 2 ? "bg-zinc-500/15" : "bg-zinc-500/30",
      details: [
        ["Beat", b.beat_id],
        ["Time", range(b.start, b.end)],
        ["Length", `${(b.end - b.start).toFixed(1)} s`],
      ],
    }),
  );
  (script.voice ?? []).forEach((v, i) =>
    items.push({
      key: `voice-${i}`,
      track: "voice",
      start: v.start,
      end: v.end,
      label: v.block_id,
      summary: `Voice block ${v.block_id}, ${range(v.start, v.end)}`,
      className: "bg-sky-500/35",
      details: [
        ["Block", v.block_id],
        ["Time", range(v.start, v.end)],
        ["Length", `${(v.end - v.start).toFixed(1)} s`],
      ],
    }),
  );
  (script.shots ?? []).forEach((s) => {
    const kind = s.kind ?? "black";
    items.push({
      key: `shot-${s.index}`,
      track: "visual",
      start: s.start,
      end: s.end,
      label: kind === "black" ? "black" : (s.asset_id ?? kind),
      summary: `Shot ${s.index}, ${humanize(s.command)}, ${kind}${s.asset_id ? ` ${s.asset_id}` : ""}, motion ${humanize(s.motion)}, ${humanize(s.transition_in)} in, ${range(s.start, s.end)}`,
      className: SHOT_CLASS[kind] ?? "bg-muted",
      thumb: s.asset_id && kind !== "black" ? thumbs.get(s.asset_id) : undefined,
      transition: s.transition_in,
      details: [
        ["Shot", `#${s.index}`],
        ["Beat", s.beat_id],
        ["Time", range(s.start, s.end)],
        ["Command", humanize(s.command)],
        ["Kind", kind],
        ["Asset", s.asset_id ?? "—"],
        ["Role", s.role ?? "—"],
        ["Motion", humanize(s.motion)],
        ["Transition in", humanize(s.transition_in)],
        ...(s.credit ? ([["Credit", s.credit]] as [string, string][]) : []),
        ...(s.reframe ? ([["Reframe", "second, tighter framing of a long still"]] as [string, string][]) : []),
      ],
    });
  });
  (script.overlays ?? []).forEach((o, i) =>
    items.push({
      key: `overlay-${i}`,
      track: "overlay",
      start: o.start,
      end: o.end,
      label: o.text,
      summary: `${o.kind} overlay, ${range(o.start, o.end)}: ${o.text}`,
      className: OVERLAY_CLASS[o.kind] ?? "bg-muted",
      textLang: language,
      details: [
        ["Kind", o.kind],
        ["Time", range(o.start, o.end)],
        ["Text", o.text],
      ],
    }),
  );
  (script.music ?? []).forEach((m, i) =>
    items.push({
      key: `music-${i}`,
      track: "music",
      start: m.start,
      end: m.start + m.duration,
      label: `${humanize(m.role)}${m.mood ? ` · ${m.mood}` : ""}`,
      summary: `Music ${humanize(m.role)}${m.mood ? ` (${m.mood})` : ""}, ${range(m.start, m.start + m.duration)}`,
      className: MUSIC_CLASS[m.role] ?? "bg-muted",
      lane: m.role === "bed" ? "top" : "bottom",
      details: [
        ["Role", humanize(m.role)],
        ["Cue", m.cue_id ?? "—"],
        ["Mood", m.mood ?? "—"],
        ["Time", range(m.start, m.start + m.duration)],
        ["Level", m.level_db != null ? `${m.level_db} dB` : "—"],
      ],
    }),
  );
  (script.silences ?? []).forEach((s, i) =>
    items.push({
      key: `silence-${i}`,
      track: "silence",
      start: s.start,
      end: s.start + s.duration,
      label: s.kind ? humanize(s.kind) : "",
      summary: `Pause${s.kind ? ` (${humanize(s.kind)})` : ""}, ${s.duration.toFixed(1)} s at ${formatTimecode(s.start, { tenths: true })}`,
      className: "bg-zinc-500/30",
      details: [
        ["Kind", s.kind ? humanize(s.kind) : "—"],
        ["Time", range(s.start, s.start + s.duration)],
        ["Length", `${s.duration.toFixed(1)} s`],
      ],
    }),
  );
  return items;
}

function fitZoomIndex(duration: number): number {
  for (let i = ZOOMS.length - 1; i > 0; i--) if (duration * ZOOMS[i] <= FIT_WIDTH_PX) return i;
  return 0;
}

export function TimelineTab({
  caseId,
  settings,
  overview,
  refreshKey,
  language,
  onLanguageChange,
  masterId,
}: LanguageTabProps) {
  const expected = overview.languages[language]?.production ?? null;
  const { data, error, loading, refetch } = useProduction(caseId, language, masterId, expected != null, refreshKey);
  const { data: visuals } = useApi(() => api.listVisuals(caseId), [caseId, refreshKey], { keepPrevious: true });
  const production = data && expected && data.id === expected.id ? data : null;

  const thumbs = useMemo(
    () => new Map((visuals ?? []).map((v) => [v.asset_id, apiFileUrl(v.thumbnail_url)])),
    [visuals],
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <LanguagePicker
          languages={settings.languages}
          value={language}
          onChange={onLanguageChange}
          isAvailable={(l) => overview.languages[l]?.production != null}
        />
        {expected && (
          <p className="text-xs text-muted-foreground">
            Production v{expected.version} · {expected.mode} · read-only
          </p>
        )}
      </div>

      {!expected ? (
        <EmptyState
          title={`No production script in ${langLabel(language)} yet — run a pilot in the Run tab`}
          description="The production script places narration, pictures, on-screen text, music and pauses on one timeline per language."
        />
      ) : production ? (
        <TimelineView key={production.id} script={production.script} thumbs={thumbs} language={language} />
      ) : error && !loading ? (
        <ErrorState message={error} onRetry={refetch} />
      ) : (
        <TableSkeleton rows={6} cols={1} />
      )}
    </div>
  );
}

function TimelineView({
  script,
  thumbs,
  language,
}: {
  script: ProductionScriptData;
  thumbs: Map<string, string>;
  language: string;
}) {
  const items = useMemo(() => buildItems(script, thumbs, language), [script, thumbs, language]);
  const byTrack = useMemo(() => {
    const groups: Record<TrackKey, TimelineItem[]> = {
      beats: [],
      voice: [],
      visual: [],
      overlay: [],
      music: [],
      silence: [],
    };
    for (const item of items) groups[item.track].push(item);
    return groups;
  }, [items]);
  const duration = useMemo(
    () => items.reduce((d, i) => Math.max(d, i.end), Math.max(script.duration ?? 0, 1)),
    [script.duration, items],
  );

  const scroller = useRef<HTMLDivElement>(null);
  const pendingScroll = useRef<number | null>(null);
  const [zoomChoice, setZoomChoice] = useState<number | null>(null);
  const [view, setView] = useState({ left: 0, width: 0 });
  const [selected, setSelected] = useState<TimelineItem | null>(null);

  const zoomIndex = zoomChoice ?? fitZoomIndex(duration);
  const pps = ZOOMS[zoomIndex];
  const width = Math.ceil(duration * pps) + 1;

  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const observer = new ResizeObserver(() =>
      setView((v) => (v.width === el.clientWidth ? v : { ...v, width: el.clientWidth })),
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // Keep the same moment centred when zooming.
  useLayoutEffect(() => {
    const el = scroller.current;
    if (el && pendingScroll.current != null) {
      el.scrollLeft = pendingScroll.current;
      pendingScroll.current = null;
    }
  }, [pps]);

  function zoomTo(index: number) {
    const next = Math.max(0, Math.min(ZOOMS.length - 1, index));
    const el = scroller.current;
    if (el) {
      const centre = (el.scrollLeft + el.clientWidth / 2) / pps;
      pendingScroll.current = Math.max(0, centre * ZOOMS[next] - el.clientWidth / 2);
    }
    setZoomChoice(next);
  }

  // Only what is on screen (plus one screen either side) is rendered, so a
  // two-hour film with hundreds of shots stays cheap to scroll.
  const viewWidth = view.width || 1200;
  const from = Math.max(0, (view.left - viewWidth) / pps);
  const to = (view.left + 2 * viewWidth) / pps;
  const visible = (list: TimelineItem[]) => list.filter((i) => i.end >= from && i.start <= to);

  const step = TICK_STEPS.find((s) => s * pps >= MIN_TICK_SPACING_PX) ?? TICK_STEPS[TICK_STEPS.length - 1];
  const ticks: number[] = [];
  for (let t = Math.floor(from / step) * step; t <= Math.min(to, duration); t += step) ticks.push(t);

  return (
    <Card>
      <CardHeader className="flex-wrap gap-2">
        <CardTitle>
          Timeline · {langLabel(language)} · {formatTimecode(duration)}
        </CardTitle>
        <div className="flex items-center gap-1.5">
          <Button variant="ghost" size="icon" aria-label="Zoom out" onClick={() => zoomTo(zoomIndex - 1)} disabled={zoomIndex === 0}>
            <ZoomOut />
          </Button>
          <input
            type="range"
            min={0}
            max={ZOOMS.length - 1}
            step={1}
            value={zoomIndex}
            onChange={(e) => zoomTo(Number(e.target.value))}
            aria-label="Zoom"
            aria-valuetext={`${ZOOMS[zoomIndex]} pixels per second`}
            className="w-28 cursor-pointer accent-primary"
          />
          <Button
            variant="ghost"
            size="icon"
            aria-label="Zoom in"
            onClick={() => zoomTo(zoomIndex + 1)}
            disabled={zoomIndex === ZOOMS.length - 1}
          >
            <ZoomIn />
          </Button>
          <span className="w-16 text-right text-[11px] tabular-nums text-muted-foreground">{ZOOMS[zoomIndex]} px/s</span>
          <Button variant="outline" size="sm" onClick={() => zoomTo(fitZoomIndex(duration))}>
            Fit
          </Button>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        <div className="flex">
          <div className="w-20 shrink-0 border-r border-border bg-subtle text-[11px] font-medium text-muted-foreground" aria-hidden>
            <div style={{ height: RULER_HEIGHT }} />
            {TRACKS.map((t) => (
              <div key={t.key} className="flex items-center border-t border-border px-2" style={{ height: t.height }}>
                {t.label}
              </div>
            ))}
          </div>
          <div
            ref={scroller}
            tabIndex={0}
            role="region"
            aria-label="Timeline tracks, scroll horizontally"
            onScroll={(e) => {
              const left = e.currentTarget.scrollLeft;
              setView((v) => (v.left === left ? v : { ...v, left }));
            }}
            className="relative min-w-0 flex-1 overflow-x-auto outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/50"
          >
            <div className="relative" style={{ width, height: RULER_HEIGHT + TRACKS_HEIGHT }}>
              <div className="absolute inset-x-0 top-0" style={{ height: RULER_HEIGHT }} aria-hidden>
                {ticks.map((t) => (
                  <div key={t} className="absolute top-0 h-full border-l border-border-strong pl-1" style={{ left: t * pps }}>
                    <span className="text-[10px] tabular-nums text-muted-foreground">{formatTimecode(t)}</span>
                  </div>
                ))}
              </div>
              {TRACKS.map((t, i) => (
                <div
                  key={t.key}
                  role="group"
                  aria-label={`${t.label} track`}
                  className="absolute inset-x-0 border-t border-border"
                  style={{ top: TRACK_TOPS[i], height: t.height }}
                >
                  {visible(byTrack[t.key]).map((item) => (
                    <TimelineBlock
                      key={item.key}
                      item={item}
                      pps={pps}
                      trackHeight={t.height}
                      selected={selected?.key === item.key}
                      onSelect={() => setSelected(item)}
                    />
                  ))}
                </div>
              ))}
              <div className="pointer-events-none absolute inset-x-0" style={{ top: RULER_HEIGHT, height: TRACKS_HEIGHT }} aria-hidden>
                {visible(byTrack.beats).map((b) => (
                  <div key={b.key} className="absolute inset-y-0 w-px bg-border-strong" style={{ left: b.start * pps }} />
                ))}
              </div>
            </div>
          </div>
        </div>
        <div className="space-y-2 border-t border-border p-3">
          <Legend />
          <div aria-live="polite" className="text-xs">
            {selected ? (
              <dl className="grid grid-cols-[110px_1fr] gap-x-2 gap-y-1">
                {selected.details.map(([k, v]) => (
                  <div key={k} className="contents">
                    <dt className="text-muted-foreground">{k}</dt>
                    <dd
                      className="min-w-0 break-words"
                      lang={k === "Text" ? selected.textLang : undefined}
                      dir={k === "Text" && selected.textLang ? (isRtl(selected.textLang) ? "rtl" : "ltr") : undefined}
                    >
                      {v}
                    </dd>
                  </div>
                ))}
              </dl>
            ) : (
              <p className="text-muted-foreground">
                Hover an element for a summary; click (or focus and press Enter) for its details.
              </p>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function TimelineBlock({
  item,
  pps,
  trackHeight,
  selected,
  onSelect,
}: {
  item: TimelineItem;
  pps: number;
  trackHeight: number;
  selected: boolean;
  onSelect: () => void;
}) {
  const w = Math.max(2, (item.end - item.start) * pps);
  const half = (trackHeight - 6) / 2;
  const top = item.lane === "bottom" ? 3 + half : 3;
  const height = item.lane ? half : trackHeight - 6;
  const showThumb = item.thumb && w >= 24;
  return (
    <button
      type="button"
      onClick={onSelect}
      title={item.summary}
      aria-label={item.summary}
      aria-pressed={selected}
      className={cn(
        "absolute overflow-hidden rounded-sm border border-black/10 bg-cover bg-center text-left outline-none focus-visible:z-20 focus-visible:ring-2 focus-visible:ring-ring dark:border-white/10",
        item.className,
        selected && "z-10 ring-2 ring-primary",
      )}
      style={{
        left: item.start * pps,
        width: w,
        top,
        height,
        backgroundImage: showThumb ? `url("${item.thumb}")` : undefined,
      }}
    >
      {item.transition && item.transition !== "CUT" && w >= 8 && (
        <span
          aria-hidden
          className={cn(
            "absolute inset-y-0 left-0 w-1.5",
            item.transition === "FADE_BLACK"
              ? "bg-black/80"
              : "bg-linear-to-r from-white/70 to-transparent dark:from-white/40",
          )}
        />
      )}
      {w >= 30 && item.label && (
        <span
          lang={item.textLang}
          dir="auto"
          className={cn(
            "block truncate px-1 text-[10px] leading-4",
            showThumb && "m-0.5 inline-block max-w-[calc(100%-4px)] rounded-sm bg-black/60 text-white",
          )}
        >
          {item.label}
        </span>
      )}
    </button>
  );
}

function Legend() {
  const entries: [string, string][] = [
    ["image", SHOT_CLASS.image],
    ["document", SHOT_CLASS.document],
    ["map", SHOT_CLASS.map],
    ["black", SHOT_CLASS.black],
    ["music bed", MUSIC_CLASS.bed],
    ["music moment", MUSIC_CLASS.music_bridge],
    ["sting", MUSIC_CLASS.sting],
    ["quote / text", OVERLAY_CLASS.quote],
  ];
  const edges: [string, string][] = [
    ["crossfade in", "bg-linear-to-r from-white/70 to-transparent dark:from-white/40"],
    ["fade from black", "bg-black/80"],
  ];
  return (
    <ul className="flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-muted-foreground" aria-label="Legend">
      {entries.map(([label, cls]) => (
        <li key={label} className="flex items-center gap-1">
          <span className={cn("inline-block size-2.5 rounded-sm border border-black/10 dark:border-white/10", cls)} aria-hidden />
          {label}
        </li>
      ))}
      {edges.map(([label, cls]) => (
        <li key={label} className="flex items-center gap-1">
          <span className="relative inline-block h-2.5 w-4 rounded-sm bg-indigo-500/35" aria-hidden>
            <span className={cn("absolute inset-y-0 left-0 w-1.5 rounded-l-sm", cls)} />
          </span>
          {label}
        </li>
      ))}
    </ul>
  );
}
