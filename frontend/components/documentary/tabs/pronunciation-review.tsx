"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { isRtl, langLabel } from "@/lib/format";
import type { PronunciationEntry, PronunciationOccurrence } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/state";
import { BlockReview } from "./block-review";
import { NarrationPreviews } from "./narration-preview";
import { PhoneticBenchmark } from "./phonetic-benchmark";
import { PronunciationPlanView } from "./pronunciation-plan";
import { ReadingConflicts } from "./reading-conflicts";
import { InlineAlert, LanguagePicker, type LanguageTabProps } from "../shared";

const STATUS_LABEL: Record<string, string> = {
  legacy_unverified: "Legacy — unverified",
  candidate: "Candidate",
  human_approved: "Approved by you",
  auto_verified: "Auto-verified",
  unresolved: "Unresolved",
  uncertain: "Uncertain",
  rejected: "Rejected",
  contested: "Contested",
};
const STATUS_TONE: Record<string, "warning" | "success" | "danger" | "info" | "default"> = {
  legacy_unverified: "warning",
  candidate: "info",
  human_approved: "success",
  auto_verified: "info",
  unresolved: "warning",
  uncertain: "default",
  rejected: "danger",
  contested: "danger",
};
const REVIEWER_KEY = "pronunciation-reviewer";

/** The waveform of a clip with the playing position. */
function Waveform({ peaks, progress }: { peaks: number[]; progress: number }) {
  return (
    <div className="flex h-12 items-center gap-px" dir="ltr" aria-hidden="true">
      {peaks.map((p, i) => (
        <span
          key={i}
          className={i / peaks.length <= progress ? "bg-primary" : "bg-muted-foreground/40"}
          style={{ height: `${Math.max(4, p * 100)}%`, width: 3 }}
        />
      ))}
    </div>
  );
}

/** One place of the word in the narration: the words around it with the one being said highlighted, the waveform and loopable playback. */
function Occurrence({ occurrence, audioUrl, versionId, lang }: { occurrence: PronunciationOccurrence; audioUrl: string; versionId: number; lang: string }) {
  const audio = useRef<HTMLAudioElement>(null);
  const [now, setNow] = useState<number>(occurrence.start);
  const [playing, setPlaying] = useState<"word" | "sentence" | null>(null);
  const [loop, setLoop] = useState(true);
  const peaks = useApi(() => api.pronunciationPeaks(versionId, occurrence.words[0].start, occurrence.words[occurrence.words.length - 1].end), [versionId, occurrence.start]);
  const first = occurrence.words[0].start;
  const last = occurrence.words[occurrence.words.length - 1].end;
  const span = useCallback(
    (mode: "word" | "sentence") => (mode === "word" ? [occurrence.start, occurrence.end] : [first, last]) as [number, number],
    [occurrence.start, occurrence.end, first, last],
  );

  function play(mode: "word" | "sentence") {
    const el = audio.current;
    if (!el) return;
    el.currentTime = span(mode)[0];
    setPlaying(mode);
    void el.play();
  }
  function stop() {
    audio.current?.pause();
    setPlaying(null);
  }
  function tick() {
    const el = audio.current;
    if (!el || !playing) return;
    setNow(el.currentTime);
    const [from, to] = span(playing);
    if (el.currentTime >= to) {
      if (loop) el.currentTime = from;
      else stop();
    }
  }
  const activeWord = occurrence.words.findIndex((w) => now >= w.start && now < w.end);
  const progress = last > first ? Math.min(1, Math.max(0, (now - first) / (last - first))) : 0;

  return (
    <li className="space-y-2 rounded-md border border-border p-3" data-testid="pronunciation-occurrence">
      <audio ref={audio} src={audioUrl} preload="none" onTimeUpdate={tick} onEnded={stop} />
      <p lang={lang} dir={isRtl(lang) ? "rtl" : "ltr"} className="text-base leading-9">
        {occurrence.words.map((w, i) => (
          <span
            key={`${w.start}-${i}`}
            className={[
              "rounded px-0.5",
              i === occurrence.hit ? "bg-amber-500/25 font-semibold" : "",
              i === activeWord ? "outline outline-2 outline-primary" : "",
            ].join(" ")}
          >
            {w.text}{" "}
          </span>
        ))}
      </p>
      {peaks.data && <Waveform peaks={peaks.data.peaks} progress={progress} />}
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="secondary" onClick={() => play("word")}>
          Play word
        </Button>
        <Button size="sm" variant="secondary" onClick={() => play("sentence")}>
          Play sentence
        </Button>
        {playing && (
          <Button size="sm" variant="secondary" onClick={stop}>
            Stop
          </Button>
        )}
        <Checkbox label="loop" checked={loop} onChange={(e) => setLoop(e.target.checked)} className="text-xs text-muted-foreground" />
        <span className="text-xs text-muted-foreground">
          {occurrence.block_id} · {occurrence.start.toFixed(2)}–{occurrence.end.toFixed(2)} s
        </span>
        {occurrence.form !== "exact" && <Badge variant="warning">written differently: {occurrence.written}</Badge>}
      </div>
    </li>
  );
}

function Detail({
  entry,
  versionId,
  lang,
  reviewer,
  onChanged,
}: {
  entry: PronunciationEntry;
  versionId: number | null;
  lang: string;
  reviewer: string;
  onChanged: () => void;
}) {
  const [pronunciation, setPronunciation] = useState(entry.approved_pronunciation ?? entry.expected_irab);
  const [ipa, setIpa] = useState(entry.approved_ipa ?? "");
  const [scope, setScope] = useState<"context" | "global">(entry.scope);
  const [reason, setReason] = useState("");
  const [supersede, setSupersede] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const places = useApi(() => (versionId == null ? Promise.resolve(null) : api.pronunciationOccurrences(entry.id, versionId)), [entry.id, versionId]);
  const rtl = isRtl(lang);

  async function act(name: string, run: () => Promise<unknown>) {
    setBusy(name);
    setError(null);
    try {
      await run();
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }
  const named = reviewer.trim() !== "";

  return (
    <div className="space-y-4" data-testid="pronunciation-detail">
      <div className="flex flex-wrap items-baseline gap-3">
        <span lang={lang} dir={rtl ? "rtl" : "ltr"} className="text-3xl font-semibold">
          {entry.surface}
        </span>
        <Badge variant={STATUS_TONE[entry.status] ?? "default"}>{STATUS_LABEL[entry.status] ?? entry.status}</Badge>
        <span className="text-xs text-muted-foreground">version {entry.version}</span>
        {entry.reviewer && <span className="text-xs text-muted-foreground">· {entry.reviewer}</span>}
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1">
          <p className="text-xs font-medium text-muted-foreground">Expected pronunciation · اعراب مورد انتظار</p>
          <p lang={lang} dir={rtl ? "rtl" : "ltr"} className="rounded-md bg-muted/50 px-3 py-2 text-xl" data-testid="expected-irab">
            {entry.expected_irab || "—"}
          </p>
        </div>
        <div className="space-y-1">
          <p className="text-xs font-medium text-muted-foreground">Observed / heard wrong · تلفظ شنیده‌شده</p>
          <p lang={lang} dir="auto" className="rounded-md bg-muted/50 px-3 py-2 text-xl">
            {entry.observed_wrong || "— (not observed yet)"}
          </p>
        </div>
      </div>

      {entry.sources.length > 0 && (
        <div className="space-y-1">
          <p className="text-xs font-medium text-muted-foreground">Where this entry comes from — kept as it was, not trusted</p>
          <ul className="space-y-1 text-xs">
            {entry.sources.map((s) => (
              <li key={`${s.table}:${s.id}`} className="rounded border border-border px-2 py-1">
                <span className="font-mono">{s.table}</span> · {s.source} · {s.kind}
                {s.latin ? <> · Latin <span className="font-mono">{s.latin}</span></> : null}
                {s.reading ? <> · reading <span className="font-mono" dir="auto">{s.reading}</span></> : null}
                {s.hits ? <> · proven {s.hits}×</> : null}
              </li>
            ))}
            {entry.different_readings.map((d) => (
              <li key={d.from} className="text-amber-700 dark:text-amber-400">
                Another source reads it differently ({d.from}): <span dir="auto">{d.reading}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="space-y-2 rounded-md border border-border p-3">
        <p className="text-sm font-medium">Your decision</p>
        <label className="block space-y-1 text-xs text-muted-foreground">
          Correct pronunciation with vowel marks · اعراب درست (editable)
          <Input
            lang={lang}
            dir={rtl ? "rtl" : "ltr"}
            className="h-10 text-xl"
            aria-label={`Pronunciation of ${entry.surface}`}
            value={pronunciation}
            onChange={(e) => setPronunciation(e.target.value)}
          />
        </label>
        <label className="block space-y-1 text-xs text-muted-foreground">
          IPA (only when you are sure; optional)
          <Input dir="ltr" className="h-8 font-mono text-xs" aria-label="IPA" value={ipa} onChange={(e) => setIpa(e.target.value)} placeholder="ʔæhd" />
        </label>
        <div className="flex flex-wrap items-center gap-4 text-xs">
          <label className="flex items-center gap-2">
            Applies to
            <select className="rounded border border-border bg-background px-2 py-1" value={scope} onChange={(e) => setScope(e.target.value as "context" | "global")}>
              <option value="context">this context only</option>
              <option value="global">every case (global rule)</option>
            </select>
          </label>
          {entry.status === "human_approved" && (
            <Checkbox label="replace my earlier approval" checked={supersede} onChange={(e) => setSupersede(e.target.checked)} className="text-xs text-muted-foreground" />
          )}
        </div>
        <Input className="h-8 text-xs" aria-label="Note" placeholder="Note (optional)" value={reason} onChange={(e) => setReason(e.target.value)} />
        {!named && <InlineAlert tone="warning">Write your name above first: an approval is only valid with a named reviewer.</InlineAlert>}
        {error && <InlineAlert tone="error">{error}</InlineAlert>}
        <div className="flex flex-wrap gap-2">
          <Button
            loading={busy === "approve"}
            disabled={!named || pronunciation.trim() === ""}
            onClick={() =>
              act("approve", () =>
                api.approvePronunciation(entry.id, { pronunciation: pronunciation.trim(), reviewer, scope, ipa: ipa.trim() || undefined, supersede, reason }),
              )
            }
          >
            Approve
          </Button>
          <Button variant="secondary" loading={busy === "uncertain"} disabled={!named} onClick={() => act("uncertain", () => api.uncertainPronunciation(entry.id, reviewer, reason))}>
            Mark uncertain
          </Button>
          <Button variant="secondary" loading={busy === "reject"} disabled={!named} onClick={() => act("reject", () => api.rejectPronunciation(entry.id, reviewer, reason))}>
            Reject
          </Button>
        </div>
      </div>

      <div className="space-y-2">
        <p className="text-sm font-medium">Where it is spoken in the narration · جای واژه در روایت</p>
        {versionId == null && <p className="text-xs text-muted-foreground">This language has no narration to listen to yet.</p>}
        {places.error && <InlineAlert tone="error">{places.error}</InlineAlert>}
        {versionId != null && !places.data && !places.error && <Skeleton className="h-16 w-full" />}
        {places.data && places.data.occurrences.length === 0 && (
          <p className="text-xs text-muted-foreground">The word is not in this narration, or the narration is not rendered.</p>
        )}
        {places.data?.audio_url && versionId != null && (
          <ul className="space-y-2">
            {places.data.occurrences.map((o, i) => (
              <Occurrence key={`${o.start}-${i}`} occurrence={o} audioUrl={places.data!.audio_url!} versionId={versionId} lang={lang} />
            ))}
          </ul>
        )}
        {places.data && places.data.total > places.data.occurrences.length && (
          <p className="text-xs text-muted-foreground">
            Showing {places.data.occurrences.length} of {places.data.total} places.
          </p>
        )}
      </div>
    </div>
  );
}

/** The pronunciation memory: every entry (the imported ones wait here as legacy and unverified), the places of the word in the narration with
 * synchronized playback, and the reviewer's explicit decisions. */
export function PronunciationReviewTab({ settings, overview, refreshKey, language, onLanguageChange }: LanguageTabProps) {
  const versionId = overview.languages[language]?.version_id ?? null;
  const [status, setStatus] = useState<string>("legacy_unverified");
  const [selected, setSelected] = useState<number | null>(null);
  const [view, setView] = useState<"entries" | "plan" | "conflicts" | "benchmark" | "preview" | "voice" | "host">(() => {
    try {
      const wanted = typeof window === "undefined" ? null : window.sessionStorage.getItem("pronunciation-view");
      if (wanted === "voice") window.sessionStorage.removeItem("pronunciation-view");       // the Monitoring shortcut asks for the voice review once
      return wanted === "voice" ? "voice" : "entries";
    } catch {
      return "entries";
    }
  });
  const [reviewer, setReviewer] = useState(() => {
    try {
      return typeof window === "undefined" ? "" : (window.localStorage.getItem(REVIEWER_KEY) ?? "");
    } catch {
      return ""; // private window: the name is asked again
    }
  });
  const state = useApi(() => api.pronunciationEntries(language, status === "all" ? undefined : status), [language, status, refreshKey]);

  function rename(value: string) {
    setReviewer(value);
    try {
      window.localStorage.setItem(REVIEWER_KEY, value);
    } catch {
      /* not remembered */
    }
  }

  // The Monitoring shortcut names the language whose narration waits for the voice review (asked for once).
  useEffect(() => {
    try {
      const wanted = window.sessionStorage.getItem("pronunciation-language");
      if (wanted) {
        window.sessionStorage.removeItem("pronunciation-language");
        onLanguageChange(wanted);
      }
    } catch { /* the tab keeps its language */ }
  }, [onLanguageChange]);

  // The tab opens on the language that has a narration to listen to.
  const fallback = versionId == null ? settings.languages.find((l) => overview.languages[l] != null) : undefined;
  useEffect(() => {
    if (fallback) onLanguageChange(fallback);
  }, [fallback, onLanguageChange]);

  const entries = useMemo(() => state.data?.entries ?? [], [state.data]);
  const current = entries.find((e) => e.id === selected) ?? entries[0] ?? null;

  return (
    <div className="space-y-4">
      <LanguagePicker languages={settings.languages} value={language} onChange={onLanguageChange} isAvailable={(l) => overview.languages[l] != null} />
      <Card>
        <CardHeader>
          <CardTitle>Pronunciation review — {langLabel(language)}</CardTitle>
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            Reviewer
            <Input className="h-7 w-36 text-xs" aria-label="Reviewer name" value={reviewer} onChange={(e) => rename(e.target.value)} placeholder="your name" />
          </label>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex gap-2" role="tablist" aria-label="View">
            <Button size="sm" variant={view === "entries" ? "default" : "secondary"} onClick={() => setView("entries")}>Entries</Button>
            <Button size="sm" variant={view === "plan" ? "default" : "secondary"} onClick={() => setView("plan")}>Why words change</Button>
            <Button size="sm" variant={view === "conflicts" ? "default" : "secondary"} onClick={() => setView("conflicts")}>Conflicting readings</Button>
            <Button size="sm" variant={view === "benchmark" ? "default" : "secondary"} onClick={() => setView("benchmark")}>Phonetic benchmark</Button>
            <Button size="sm" variant={view === "voice" ? "default" : "secondary"} onClick={() => setView("voice")}>Voice review</Button>
            {language === "fa" && <Button size="sm" variant={view === "host" ? "default" : "secondary"} onClick={() => setView("host")}>Host voice review</Button>}
            <Button size="sm" variant={view === "preview" ? "default" : "secondary"} onClick={() => setView("preview")}>Narration preview</Button>
          </div>
          {view === "voice" && (versionId != null ? <BlockReview key={versionId} versionId={versionId} /> : <EmptyState title="No narration yet" description="The voice review opens when this language has a narration." />)}
          {view === "host" && language === "fa" && (versionId != null ? <BlockReview key={`host-${versionId}`} versionId={versionId} kind="host" /> : <EmptyState title="No narration yet" description="The host voice review opens when this language has a narration." />)}
          {view === "preview" && <NarrationPreviews />}
          {view === "plan" && <PronunciationPlanView versionId={versionId} language={language} />}
          {view === "conflicts" && <ReadingConflicts versionId={versionId} language={language} reviewer={reviewer} />}
          {view === "benchmark" && <PhoneticBenchmark language={language} reviewer={reviewer} />}
          {view === "entries" && <>
          <p className="text-xs leading-5 text-muted-foreground">
            Entries imported from the earlier systems are <strong>legacy and unverified</strong>: none is a pronunciation reference yet. The app will verify
            them itself from audio evidence once its phonetic recognizer has passed an independent evaluation (it is off until then); when you decide, your
            decision outranks the app&apos;s. Listen to the word in the narration, correct the vowel marks if needed, then approve, reject or mark it uncertain.
          </p>
          <div className="flex flex-wrap gap-2" role="tablist" aria-label="Status">
            {["legacy_unverified", "auto_verified", "human_approved", "unresolved", "uncertain", "rejected", "all"].map((s) => (
              <Button key={s} size="sm" variant={status === s ? "default" : "secondary"} onClick={() => { setStatus(s); setSelected(null); }}>
                {s === "all" ? "All" : STATUS_LABEL[s]} {s !== "all" && <span className="opacity-70">({state.data?.counts[s] ?? 0})</span>}
              </Button>
            ))}
          </div>
          {state.error && <ErrorState message={state.error} onRetry={state.refetch} />}
          {!state.data && !state.error && <Skeleton className="h-48 w-full" />}
          {state.data && entries.length === 0 && <EmptyState title="Nothing to review here" description="No entry has this status." />}
          {state.data && entries.length > 0 && current && (
            <div className="grid gap-4 lg:grid-cols-[16rem_1fr]">
              <ul className="max-h-[32rem] space-y-1 overflow-y-auto" aria-label="Entries">
                {entries.map((e) => (
                  <li key={e.id}>
                    <button
                      type="button"
                      onClick={() => setSelected(e.id)}
                      className={`flex w-full items-center justify-between gap-2 rounded-md border px-3 py-1.5 text-start text-sm ${e.id === current.id ? "border-primary bg-primary/5" : "border-border"}`}
                    >
                      <span lang={language} dir={isRtl(language) ? "rtl" : "ltr"} className="text-base">
                        {e.surface}
                      </span>
                      <Badge variant={STATUS_TONE[e.status] ?? "default"}>
                        {STATUS_LABEL[e.status] ?? e.status}
                      </Badge>
                    </button>
                  </li>
                ))}
              </ul>
              <Detail key={current.id} entry={current} versionId={versionId} lang={language} reviewer={reviewer} onChanged={state.refetch} />
            </div>
          )}
          </>}
        </CardContent>
      </Card>
    </div>
  );
}
