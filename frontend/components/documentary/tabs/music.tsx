"use client";

import { api, apiFileUrl } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatDateTime, formatTimecode, humanize, langLabel } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { TableSkeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { EmptyState, ErrorState } from "@/components/state";
import { cn } from "@/lib/utils";
import { useProduction } from "../hooks";
import { LanguagePicker, type LanguageTabProps, Metric } from "../shared";

/** After-beat transitions where music carries the film without narration. */
const MUSIC_MOMENTS = new Set(["music_bridge", "emotional_moment", "chapter_break", "sting"]);

export function MusicTab(props: LanguageTabProps) {
  return (
    <div className="space-y-4">
      <TrackLibrary />
      <AudioPlanMoments {...props} />
      <Placements {...props} />
    </div>
  );
}

function TrackLibrary() {
  const { data, error, loading, refetch } = useApi(() => api.musicLibrary());
  return (
    <Card>
      <CardHeader className="flex-wrap gap-2">
        <CardTitle>Music track library</CardTitle>
        <span className="text-xs text-muted-foreground">
          several variants per kind and mood · a track heard in a recent film is rested while an alternative exists
        </span>
      </CardHeader>
      <CardContent className="p-0">
        {loading && (
          <div className="p-4">
            <TableSkeleton rows={4} cols={4} />
          </div>
        )}
        {error && (
          <div className="p-4">
            <ErrorState message={error} onRetry={refetch} />
          </div>
        )}
        {data && data.length === 0 && (
          <p className="px-4 py-6 text-sm text-muted-foreground">
            No tracks yet — the library fills when a mix first needs a kind and mood.
          </p>
        )}
        {data && data.length > 0 && (
          <Table className="text-xs">
            <THead>
              <TR className="hover:bg-transparent">
                <TH>Track</TH>
                <TH>Kind · mood</TH>
                <TH className="text-right">Length</TH>
                <TH className="text-right">Used</TH>
                <TH>Films</TH>
                <TH>Last used</TH>
                <TH>Status</TH>
                <TH className="min-w-64">Listen</TH>
              </TR>
            </THead>
            <TBody>
              {data.map((t) => (
                <TR key={t.id} className={cn(!t.active && "opacity-60")}>
                  <TD className="max-w-72">
                    <span className="font-mono font-medium">{t.track_code}</span>
                    {!t.active && (
                      <Badge variant="outline" className="ml-1.5">
                        retired
                      </Badge>
                    )}
                    <span className="mt-0.5 line-clamp-2 block text-[10px] leading-4 text-muted-foreground" title={t.prompt}>
                      {t.style ? `${t.style} — ` : ""}
                      {t.prompt}
                    </span>
                  </TD>
                  <TD className="whitespace-nowrap">
                    {humanize(t.kind)} · {t.mood}
                    <span className="block text-[10px] text-muted-foreground">variant {t.variant}</span>
                  </TD>
                  <TD className="whitespace-nowrap text-right tabular-nums">
                    {t.seconds} s{t.loop && " · loop"}
                  </TD>
                  <TD className="whitespace-nowrap text-right tabular-nums">
                    {t.usage_count}×
                    <span className="block text-[10px] text-muted-foreground">
                      in {t.films_count} film{t.films_count === 1 ? "" : "s"}
                    </span>
                  </TD>
                  <TD className="max-w-40">
                    {t.films.length ? (
                      <span className="block truncate font-mono text-[10px] text-muted-foreground" title={t.films.join(", ")}>
                        {t.films.join(", ")}
                      </span>
                    ) : (
                      <span className="text-muted-foreground">unused</span>
                    )}
                  </TD>
                  <TD className="whitespace-nowrap text-muted-foreground">{formatDateTime(t.last_used_at)}</TD>
                  <TD>
                    <Badge variant={t.generated ? "success" : "outline"}>
                      {t.generated ? "generated" : "not generated"}
                    </Badge>
                  </TD>
                  <TD>
                    {t.generated ? (
                      <audio
                        controls
                        preload="none"
                        src={apiFileUrl(t.url)}
                        aria-label={`Play track ${t.track_code}`}
                        className="h-8 w-full min-w-56"
                      />
                    ) : (
                      <span className="text-muted-foreground">Created on the first mix that needs it</span>
                    )}
                  </TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

function AudioPlanMoments({ overview }: LanguageTabProps) {
  const plan = overview.audio_plan;
  if (!plan) {
    return (
      <EmptyState
        title="No audio plan yet"
        description="The audio director plans breaths, music beds, bridges and silences per beat right after the blueprint. Start a pilot in the Run tab."
      />
    );
  }
  const beats = plan.plan.beats ?? [];
  const moments = beats.filter(
    (b) => b.bed !== "none" || MUSIC_MOMENTS.has(b.after.type) || b.after.type === "silence",
  );
  const v = plan.validation;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Audio plan · music per beat</CardTitle>
        <span className="text-xs text-muted-foreground">
          v{plan.version} · {humanize(plan.status)}
        </span>
      </CardHeader>
      <CardContent className="space-y-3">
        {plan.plan.notes && <p className="max-w-3xl text-xs leading-5 text-foreground/90">{plan.plan.notes}</p>}
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Metric label="Music moments" value={v.music_moments ?? "—"} />
          <Metric
            label="Music-only share"
            value={v.music_only_share != null ? `${Math.round(v.music_only_share * 100)}%` : "—"}
          />
          <Metric label="Bed switches" value={v.bed_switches ?? "—"} />
          <Metric
            label="Planned runtime"
            value={v.estimated_runtime_seconds != null ? formatTimecode(v.estimated_runtime_seconds) : "—"}
          />
        </div>
        {moments.length === 0 ? (
          <p className="text-sm text-muted-foreground">The plan uses no music — breaths only.</p>
        ) : (
          <div className="-mx-4">
            <Table className="text-xs">
              <THead>
                <TR className="hover:bg-transparent">
                  <TH>Beat</TH>
                  <TH>Purpose</TH>
                  <TH>Bed under narration</TH>
                  <TH>After the beat</TH>
                  <TH className="text-right">Seconds</TH>
                  <TH>Mood</TH>
                  <TH className="min-w-64">Why</TH>
                </TR>
              </THead>
              <TBody>
                {moments.map((b) => (
                  <TR key={b.beat_id}>
                    <TD className="font-mono">{b.beat_id}</TD>
                    <TD>{humanize(b.purpose)}</TD>
                    <TD>{b.bed === "none" ? "—" : `${b.bed} · ${humanize(b.bed_level)}`}</TD>
                    <TD>
                      {MUSIC_MOMENTS.has(b.after.type) ? (
                        <Badge variant="accent">{humanize(b.after.type)}</Badge>
                      ) : (
                        humanize(b.after.type)
                      )}
                    </TD>
                    <TD className="text-right tabular-nums">{b.after.seconds?.toFixed(1) ?? "—"}</TD>
                    <TD>{MUSIC_MOMENTS.has(b.after.type) ? (b.after.mood ?? "—") : "—"}</TD>
                    <TD className="leading-5 text-muted-foreground">{b.why || "—"}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Placements({ caseId, settings, overview, refreshKey, language, onLanguageChange, masterId }: LanguageTabProps) {
  const expected = overview.languages[language]?.production ?? null;
  const { data, error, loading, refetch } = useProduction(caseId, language, masterId, expected != null, refreshKey);
  const production = data && expected && data.id === expected.id ? data : null;
  const music = production?.script.music ?? [];

  return (
    <Card>
      <CardHeader className="flex-wrap gap-2">
        <CardTitle>Cue sheet of the production</CardTitle>
        <LanguagePicker
          languages={settings.languages}
          value={language}
          onChange={onLanguageChange}
          isAvailable={(l) => overview.languages[l]?.production != null}
        />
      </CardHeader>
      <CardContent className="p-0">
        {!expected ? (
          <p className="px-4 py-6 text-sm text-muted-foreground">
            No production script in {langLabel(language)} yet — run a pilot in the Run tab.
          </p>
        ) : !production ? (
          error && !loading ? (
            <div className="p-4">
              <ErrorState message={error} onRetry={refetch} />
            </div>
          ) : (
            <div className="p-4">
              <TableSkeleton rows={4} cols={6} />
            </div>
          )
        ) : music.length === 0 ? (
          <p className="px-4 py-6 text-sm text-muted-foreground">
            No music placed in this production (narration only).
          </p>
        ) : (
          <Table className="text-xs">
            <THead>
              <TR className="hover:bg-transparent">
                <TH>Role</TH>
                <TH>Track</TH>
                <TH>Mood</TH>
                <TH className="text-right">Start</TH>
                <TH className="text-right">Duration</TH>
                <TH className="text-right">Level</TH>
                <TH className="min-w-56">Why here</TH>
                <TH className="min-w-56">Why this track</TH>
              </TR>
            </THead>
            <TBody>
              {music.map((m, i) => (
                <TR key={`${m.start}-${i}`}>
                  <TD>
                    <Badge variant={m.role === "bed" ? "info" : m.role === "silence" ? "outline" : "accent"}>
                      {humanize(m.role)}
                    </Badge>
                  </TD>
                  <TD className="font-mono">{m.track_code ?? m.cue_id ?? (m.role === "silence" ? "room tone" : "—")}</TD>
                  <TD>{m.mood ?? "—"}</TD>
                  <TD className="text-right tabular-nums">{formatTimecode(m.start, { tenths: true })}</TD>
                  <TD className="text-right tabular-nums">{m.duration.toFixed(1)} s</TD>
                  <TD className="text-right tabular-nums">{m.level_db != null ? `${m.level_db} dB` : "—"}</TD>
                  <TD className="leading-5">{m.why || "—"}</TD>
                  <TD className="leading-5 text-muted-foreground">{m.selection_reason || "—"}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
