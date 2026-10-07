"use client";

import { useId, useState } from "react";
import { ChevronDown, ChevronRight, Mic } from "lucide-react";
import { api, nullIfNotFound } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatTimecode, humanize, isRtl, langLabel } from "@/lib/format";
import type {
  BlueprintBeat,
  PerformanceBeat,
  PerformanceSentence,
  RiskyWord,
  VoicePerformance,
} from "@/lib/types";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/state";
import { cn } from "@/lib/utils";
import { Choice } from "../run-options";
import { InlineAlert, LanguagePicker, type LanguageTabProps, Metric, TagChip, TtsText } from "../shared";

/** Narrator arc levels: 0 neutral · 1 unease · 2 dark · 3 climax. */
const LEVELS: {
  label: string;
  badge: NonNullable<BadgeProps["variant"]>;
  /** Beat body in the arc strip. */
  bar: string;
  /** Peak band on top of a beat. */
  peak: string;
}[] = [
  { label: "neutral", badge: "default", bar: "bg-zinc-400/35 dark:bg-zinc-500/35", peak: "bg-zinc-500" },
  { label: "unease", badge: "warning", bar: "bg-amber-500/45", peak: "bg-amber-500" },
  { label: "dark", badge: "accent", bar: "bg-indigo-600/60 dark:bg-indigo-500/55", peak: "bg-indigo-600" },
  { label: "climax", badge: "danger", bar: "bg-rose-600/70 dark:bg-rose-500/65", peak: "bg-rose-600" },
];

const level = (n: number) => LEVELS[Math.max(0, Math.min(LEVELS.length - 1, Math.round(n)))];

const BEATS_PER_PAGE = 12;

const beatAnchor = (prefix: string, beatId: string) => `${prefix}-beat-${beatId}`;

function LevelBadge({ value }: { value: number }) {
  const meta = level(value);
  return <Badge variant={meta.badge}>{meta.label}</Badge>;
}

/** Beats the pilot covers: the opening beats up to `seconds` plus one beat margin (as the job does). */
function pilotBeatIds(beats: BlueprintBeat[], secondsPerWord: number, seconds: number): string[] {
  const out: string[] = [];
  let t = 0;
  for (const b of beats) {
    out.push(b.id);
    t += (b.words ?? 0) * secondsPerWord;
    if (t > seconds) break;
  }
  if (out.length < beats.length) out.push(beats[out.length].id);
  return out;
}

export function VoicePerformanceTab({ settings, overview, refreshKey, language, onLanguageChange }: LanguageTabProps) {
  const entry = overview.languages[language] ?? null;
  const beats = overview.blueprint?.blueprint.beats ?? [];
  const totalWords = beats.reduce((n, b) => n + (b.words ?? 0), 0);
  const pilotBeats =
    entry && totalWords > 0
      ? pilotBeatIds(beats, (entry.estimated_film_minutes * 60) / totalWords, settings.pilot_seconds)
      : null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <LanguagePicker
          languages={settings.languages}
          value={language}
          onChange={onLanguageChange}
          isAvailable={(l) => overview.languages[l] != null}
        />
        <p className="max-w-xl text-xs text-muted-foreground">
          How the narrator says it: a tension arc across the beats and ElevenLabs audio tags placed
          right before the words they colour.
        </p>
      </div>
      {!settings.voice_performance.enabled && (
        <InlineAlert tone="warning">
          Voice performance is switched off in the configuration — narration uses the plain spoken text.
        </InlineAlert>
      )}
      {!entry ? (
        <EmptyState
          title={`No spoken version in ${langLabel(language)} yet — run a pilot in the Run tab`}
          description="The narrator's performance is directed per spoken version, right before its narration is recorded."
        />
      ) : (
        <PerformancePanel
          key={entry.version_id}
          versionId={entry.version_id}
          language={language}
          refreshKey={refreshKey}
          pilotBeats={pilotBeats}
          pilotSeconds={settings.pilot_seconds}
          audioTags={settings.voices[language]?.audio_tags ?? false}
          pronunciation={
            settings.pronunciation_check.enabled && settings.pronunciation_check.languages.includes(language)
              ? { maxRounds: settings.pronunciation_check.max_rounds }
              : null
          }
        />
      )}
    </div>
  );
}

function PerformancePanel({
  versionId,
  language,
  refreshKey,
  pilotBeats,
  pilotSeconds,
  audioTags,
  pronunciation,
}: {
  versionId: number;
  language: string;
  refreshKey: number;
  pilotBeats: string[] | null;
  pilotSeconds: number;
  audioTags: boolean;
  /** Set when this language gets the pronunciation check. */
  pronunciation: { maxRounds: number } | null;
}) {
  const ids = useId();
  const [tick, setTick] = useState(0);
  const [scope, setScope] = useState<"pilot" | "film">(pilotBeats ? "pilot" : "film");
  const [directing, setDirecting] = useState(false);
  const [directError, setDirectError] = useState<string | null>(null);
  const [shownBeats, setShownBeats] = useState(BEATS_PER_PAGE);

  const perfState = useApi(
    () => nullIfNotFound(api.voicePerformance(versionId)),
    [versionId, refreshKey, tick],
    { keepPrevious: true },
  );
  const perf = perfState.data;
  const notDirected = !perfState.loading && !perfState.error && perf == null;
  const speechState = useApi(
    () => (notDirected ? api.speechStructure(versionId) : Promise.resolve(null)),
    [versionId, notDirected],
  );

  async function direct() {
    setDirecting(true);
    setDirectError(null);
    try {
      await api.directVoicePerformance(versionId, scope === "pilot" ? pilotBeats : null);
      setTick((t) => t + 1);
    } catch (e) {
      setDirectError(e instanceof Error ? e.message : String(e));
    } finally {
      setDirecting(false);
    }
  }

  // Sentences to list: the performance, or the plain speech before any direction.
  const listBeats: PerformanceBeat[] | null = perf
    ? perf.performance.beats
    : speechState.data
      ? speechState.data.beats.map((b) => ({
          beat_id: b.beat_id,
          arc_level: 0,
          arc_peak: 0,
          paragraphs: b.paragraphs.map((p) => p.map((s) => ({ ...s, tts: null, level: 0, directed: false }))),
        }))
      : null;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_300px]">
        {perf ? (
          <ArcCard
            perf={perf}
            anchorPrefix={ids}
            onReveal={(index) =>
              setShownBeats((n) => Math.max(n, Math.ceil((index + 1) / BEATS_PER_PAGE) * BEATS_PER_PAGE))
            }
          />
        ) : perfState.error && !perf ? (
          <ErrorState message={perfState.error} onRetry={perfState.refetch} />
        ) : perfState.loading ? (
          <Skeleton className="h-48" />
        ) : (
          <EmptyState
            title="Not directed yet"
            description="The job directs the narrator before recording the voice. You can also direct it here."
          />
        )}

        <Card className="h-fit">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Mic className="size-4" /> Direct the narrator
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {!audioTags && (
              <p className="text-[11px] leading-4 text-muted-foreground">
                This language&apos;s voice model does not perform audio tags; the arc still sets the
                voice style per level.
              </p>
            )}
            <fieldset className="space-y-2">
              <legend className="mb-1.5 text-xs font-medium text-muted-foreground">Sentences</legend>
              {pilotBeats && (
                <Choice
                  name={`${ids}-scope`}
                  checked={scope === "pilot"}
                  onSelect={() => setScope("pilot")}
                  title="Pilot"
                  description={`Opening beats ${pilotBeats[0]}–${pilotBeats[pilotBeats.length - 1]} (≈ first ${formatTimecode(pilotSeconds)}).`}
                />
              )}
              <Choice
                name={`${ids}-scope`}
                checked={scope === "film"}
                onSelect={() => setScope("film")}
                title="Whole film"
                description="Every sentence. Already directed sentences are kept."
              />
            </fieldset>
            <Button className="w-full" size="sm" onClick={direct} loading={directing}>
              Direct voice performance
            </Button>
            {directing && (
              <p className="text-[11px] leading-4 text-muted-foreground" aria-live="polite">
                Directing… one model call per chunk of sentences — a whole film can take several minutes.
              </p>
            )}
            {directError && <InlineAlert tone="error">{directError}</InlineAlert>}
            {perf && (
              <p className="text-[11px] text-muted-foreground">
                v{perf.version} · {humanize(perf.status)}
                {perf.model && <span className="block truncate font-mono text-[10px]">{perf.model}</span>}
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      {perf && perfState.error && (
        <InlineAlert tone="error">Could not refresh the voice performance: {perfState.error}</InlineAlert>
      )}

      {pronunciation && perf && <PronunciationKeyCard perf={perf} maxRounds={pronunciation.maxRounds} />}

      {listBeats ? (
        <SentenceList
          beats={listBeats}
          incidentBeat={perf?.performance.incident_beat ?? null}
          language={language}
          anchorPrefix={ids}
          shown={shownBeats}
          onShowMore={() => setShownBeats((n) => n + BEATS_PER_PAGE)}
        />
      ) : speechState.error ? (
        <ErrorState message={speechState.error} onRetry={speechState.refetch} />
      ) : (
        (perfState.loading || speechState.loading) && !perfState.error && <Skeleton className="h-64" />
      )}
    </div>
  );
}

function ArcCard({
  perf,
  anchorPrefix,
  onReveal,
}: {
  perf: VoicePerformance;
  anchorPrefix: string;
  /** Make sure the beat at this index is rendered in the sentence list. */
  onReveal: (index: number) => void;
}) {
  const [showLog, setShowLog] = useState(false);
  const { beats, stats, incident_beat: incident } = perf.performance;
  const { validation } = perf;
  const adjustments = [...(validation.arc_log ?? []), ...(validation.level_log ?? [])];
  const issueTotal = Object.values(stats.issues ?? {}).reduce((n, v) => n + v, 0);
  const topTags = Object.entries(stats.tags ?? {}).slice(0, 10);
  const sentencesIn = (b: PerformanceBeat) => b.paragraphs.reduce((n, p) => n + p.length, 0);

  return (
    <Card>
      <CardHeader>
        <CardTitle>Narrator arc</CardTitle>
        <span className="text-xs text-muted-foreground">
          {beats.length} beats{incident && ` · first incident ${incident}`}
        </span>
      </CardHeader>
      <CardContent className="space-y-4">
        <div>
          <ul aria-label="Arc level per beat" className="flex h-10 w-full gap-px overflow-hidden rounded-md">
            {beats.map((b, index) => {
              const lv = level(b.arc_level);
              const peak = level(b.arc_peak);
              const isIncident = b.beat_id === incident;
              const summary = `${b.beat_id}: ${lv.label}${b.arc_peak > b.arc_level ? `, peak ${peak.label}` : ""}${isIncident ? ", first incident" : ""}, ${sentencesIn(b)} sentences`;
              return (
                <li key={b.beat_id} className="flex min-w-[3px] basis-0" style={{ flexGrow: Math.max(1, sentencesIn(b)) }}>
                  <a
                    href={`#${beatAnchor(anchorPrefix, b.beat_id)}`}
                    onClick={() => {
                      onReveal(index);
                      requestAnimationFrame(() =>
                        document.getElementById(beatAnchor(anchorPrefix, b.beat_id))?.scrollIntoView({ block: "start" }),
                      );
                    }}
                    title={summary}
                    aria-label={summary}
                    className={cn(
                      "relative block w-full outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
                      lv.bar,
                    )}
                  >
                    <span className={cn("absolute inset-x-0 top-0 h-1.5", peak.peak)} aria-hidden />
                    {isIncident && <span className="absolute inset-y-0 left-0 w-0.5 bg-foreground" aria-hidden />}
                  </a>
                </li>
              );
            })}
          </ul>
          <ul className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-[10px] text-muted-foreground" aria-label="Legend">
            {LEVELS.map((l) => (
              <li key={l.label} className="flex items-center gap-1">
                <span className={cn("inline-block size-2.5 rounded-sm", l.bar)} aria-hidden />
                {l.label}
                <span className="tabular-nums" title="directed sentences at this level">
                  {stats.levels?.[l.label as keyof typeof stats.levels] ?? 0}
                </span>
              </li>
            ))}
            <li>top band = the beat&apos;s peak</li>
            <li className="flex items-center gap-1">
              <span className="inline-block h-2.5 w-0.5 bg-foreground" aria-hidden /> first incident
            </li>
          </ul>
        </div>

        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <Metric label="Sentences directed" value={`${stats.directed}/${stats.sentences}`} />
          <Metric
            label="With audio tags"
            value={stats.directed ? `${Math.round((stats.tagged / stats.directed) * 100)}%` : "—"}
          />
          <Metric label="Validator findings" value={issueTotal} tone={issueTotal ? "danger" : "success"} />
          <Metric label="Tags thinned" value={stats.thinned ?? 0} />
        </div>

        {topTags.length > 0 && (
          <div>
            <p className="mb-1 text-xs font-medium text-muted-foreground">Most used audio tags</p>
            <ul className="flex flex-wrap gap-1.5">
              {topTags.map(([tag, n]) => (
                <li key={tag}>
                  <TagChip tag={tag} />
                  <span className="ml-0.5 text-[10px] tabular-nums text-muted-foreground">×{n}</span>
                </li>
              ))}
            </ul>
          </div>
        )}

        {issueTotal > 0 && (
          <div>
            <p className="mb-1 text-xs font-medium text-muted-foreground">Validator findings (fixed or removed)</p>
            <ul className="flex flex-wrap gap-1.5">
              {Object.entries(stats.issues).map(([kind, n]) => (
                <li key={kind}>
                  <Badge variant="warning">
                    {humanize(kind)} · {n}
                  </Badge>
                </li>
              ))}
            </ul>
          </div>
        )}

        {validation.errors?.length > 0 && (
          <InlineAlert tone="error">
            <p className="font-medium">Some sentence chunks failed and stay undirected:</p>
            <ul className="mt-1 space-y-0.5">
              {validation.errors.map((e, i) => (
                <li key={i} className="break-words">
                  {e}
                </li>
              ))}
            </ul>
          </InlineAlert>
        )}

        {adjustments.length > 0 && (
          <div>
            <button
              type="button"
              onClick={() => setShowLog((v) => !v)}
              aria-expanded={showLog}
              className="flex cursor-pointer items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground"
            >
              {showLog ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
              {adjustments.length} arc rule adjustment{adjustments.length === 1 ? "" : "s"}
            </button>
            {showLog && (
              <ul className="mt-1.5 flex flex-wrap gap-1.5">
                {adjustments.map((a, i) => (
                  <li key={i}>
                    <Badge variant="outline">{humanize(a)}</Badge>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function PronunciationKeyCard({ perf, maxRounds }: { perf: VoicePerformance; maxRounds: number }) {
  const sentences = perf.performance.beats.flatMap((b) => b.paragraphs.flat());
  const keyed = sentences.filter((s) => s.risky != null);
  const riskyWords = keyed.reduce((n, s) => n + (s.risky?.length ?? 0), 0);
  const lastRun = perf.validation.pronunciation_key ?? null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Pronunciation key</CardTitle>
        <span className="text-xs text-muted-foreground">words a voice could misread</span>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="max-w-3xl text-xs leading-5 text-muted-foreground">
          Persian script leaves most short vowels unwritten: <span lang="fa" dir="rtl">ملک</span> can be melk,
          molk, malek or malak. For each such word the key records the reading the sentence&apos;s
          meaning needs. After recording, a phoneme listener checks those words; a word said wrong
          gets harakat (then full harakat, then an unambiguous spelling) and the block is spoken
          again — up to {maxRounds} corrected take{maxRounds === 1 ? "" : "s"}. Results are in the
          Voice tab.
        </p>
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
          <Metric label="Sentences keyed" value={`${keyed.length}/${sentences.length}`} />
          <Metric label="Risky words" value={riskyWords} />
          <Metric
            label="Key issues (last run)"
            value={lastRun ? lastRun.issues.length : "—"}
            tone={lastRun?.issues.length ? "danger" : undefined}
          />
        </div>
        {lastRun && lastRun.issues.length > 0 && (
          <ul className="flex flex-wrap gap-1.5" aria-label="Pronunciation key issues">
            {lastRun.issues.map((issue, i) => {
              const [code, word] = issue.split(":");
              return (
                <li key={i}>
                  <Badge variant="warning">
                    {humanize(code)}
                    {word && (
                      <span lang="fa" dir="rtl">
                        {word}
                      </span>
                    )}
                  </Badge>
                </li>
              );
            })}
          </ul>
        )}
        {lastRun && lastRun.errors.length > 0 && (
          <InlineAlert tone="error">
            <p className="font-medium">Some sentences could not be keyed:</p>
            <ul className="mt-1 space-y-0.5">
              {lastRun.errors.map((e, i) => (
                <li key={i} className="break-words">
                  {e}
                </li>
              ))}
            </ul>
          </InlineAlert>
        )}
      </CardContent>
    </Card>
  );
}

/** Word → the reading its meaning needs; the vowelled form in the tooltip. */
function RiskyChip({ word, language }: { word: RiskyWord; language: string }) {
  const forms = [
    word.vowelled && `harakat ${word.vowelled}`,
    word.full && `full harakat ${word.full}`,
    word.respell && `spelling ${word.respell}`,
    word.synonym && `synonym ${word.synonym}${word.synonym_read ? ` (${word.synonym_read})` : ""}`,
  ].filter(Boolean);
  return (
    <span
      dir="ltr"
      title={forms.length ? forms.join(" · ") : undefined}
      className="inline-flex items-center gap-1 rounded border border-amber-500/25 bg-amber-500/10 px-1.5 text-[11px] leading-5"
    >
      <span lang={language} dir="rtl" className="font-medium">
        {word.w}
      </span>
      <span aria-hidden className="text-muted-foreground">
        →
      </span>
      <span lang={`${language}-Latn`} className="font-mono">
        {word.read}
      </span>
      {word.meaning && <span className="text-muted-foreground">· {word.meaning}</span>}
      {forms.length > 0 && <span className="sr-only">({forms.join(", ")})</span>}
    </span>
  );
}

function SentenceRow({ sentence, language }: { sentence: PerformanceSentence; language: string }) {
  const voiceText = sentence.directed && sentence.tts ? sentence.tts : sentence.speech;
  return (
    <li className={cn("flex items-start gap-2 py-1", !sentence.directed && "opacity-55")}>
      <span className="mt-1 w-16 shrink-0">
        {sentence.directed ? (
          <LevelBadge value={sentence.level} />
        ) : (
          <span className="text-[10px] text-muted-foreground">not directed</span>
        )}
      </span>
      <div className="min-w-0 flex-1 text-sm leading-6">
        <p lang={language} dir={isRtl(language) ? "rtl" : "ltr"} className="break-words">
          <TtsText text={voiceText} />
        </p>
        {sentence.risky && sentence.risky.length > 0 && (
          <ul
            dir={isRtl(language) ? "rtl" : "ltr"}
            aria-label="Pronunciation key"
            className="mt-0.5 flex flex-wrap gap-1"
          >
            {sentence.risky.map((w, i) => (
              <li key={`${w.w}-${i}`}>
                <RiskyChip word={w} language={language} />
              </li>
            ))}
          </ul>
        )}
      </div>
    </li>
  );
}

function SentenceList({
  beats,
  incidentBeat,
  language,
  anchorPrefix,
  shown,
  onShowMore,
}: {
  beats: PerformanceBeat[];
  incidentBeat: string | null;
  language: string;
  anchorPrefix: string;
  shown: number;
  onShowMore: () => void;
}) {
  const [onlyDirected, setOnlyDirected] = useState(false);
  const visible = beats.slice(0, shown);

  return (
    <Card>
      <CardHeader className="flex-wrap gap-2">
        <CardTitle>Sentences</CardTitle>
        <div className="flex items-center gap-3">
          <label className="flex cursor-pointer items-center gap-1.5 text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={onlyDirected}
              onChange={(e) => setOnlyDirected(e.target.checked)}
              className="size-3.5 cursor-pointer accent-primary"
            />
            Only directed sentences
          </label>
          <span className="text-xs text-muted-foreground">
            {Math.min(shown, beats.length)} of {beats.length} beats
          </span>
        </div>
      </CardHeader>
      <CardContent className="space-y-5">
        {visible.map((b) => {
          const headingId = `${beatAnchor(anchorPrefix, b.beat_id)}-title`;
          const paragraphs = b.paragraphs
            .map((p) => (onlyDirected ? p.filter((s) => s.directed) : p))
            .filter((p) => p.length > 0);
          return (
            <section
              key={b.beat_id}
              id={beatAnchor(anchorPrefix, b.beat_id)}
              aria-labelledby={headingId}
              className="scroll-mt-4"
            >
              <h3 id={headingId} className="mb-1.5 flex flex-wrap items-center gap-1.5 border-b border-border pb-1.5 text-xs">
                <span className="font-mono font-medium">{b.beat_id}</span>
                {b.paragraphs.some((p) => p.some((s) => s.directed)) && (
                  <>
                    <LevelBadge value={b.arc_level} />
                    {b.arc_peak > b.arc_level && (
                      <span className="text-muted-foreground">
                        peak <LevelBadge value={b.arc_peak} />
                      </span>
                    )}
                  </>
                )}
                {b.beat_id === incidentBeat && <Badge variant="outline">first incident</Badge>}
              </h3>
              {paragraphs.length === 0 ? (
                <p className="text-xs text-muted-foreground">No directed sentences in this beat.</p>
              ) : (
                <div className="space-y-2">
                  {paragraphs.map((p, pi) => (
                    <ul key={pi}>
                      {p.map((s, si) => (
                        <SentenceRow key={si} sentence={s} language={language} />
                      ))}
                    </ul>
                  ))}
                </div>
              )}
            </section>
          );
        })}
        {shown < beats.length && (
          <Button variant="secondary" size="sm" onClick={onShowMore}>
            Show {Math.min(BEATS_PER_PAGE, beats.length - shown)} more beats
          </Button>
        )}
      </CardContent>
    </Card>
  );
}
