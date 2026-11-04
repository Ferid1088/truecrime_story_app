"use client";

import { useMemo, useRef, useState } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import type { RepairProposal, ReviewBlock, ReviewWord } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/state";

const FILTERS = [
  { key: "flagged", label: "Suspected errors", keep: (b: ReviewBlock) => b.flagged.some((w) => w.suggested === "FAIL") },
  { key: "uncertain", label: "Uncertain", keep: (b: ReviewBlock) => b.flagged.some((w) => w.suggested === "UNCERTAIN") },
  { key: "alignment", label: "Alignment not clear", keep: (b: ReviewBlock) => b.alignment.status !== "ALIGNED" },
  { key: "text", label: "Not the approved text", keep: (b: ReviewBlock) => b.voice_text_differs.length > 0 },
  { key: "all", label: "All", keep: () => true },
] as const;

const ALIGN_TONE: Record<string, "success" | "info" | "warning" | "danger" | "default"> = {
  ALIGNED: "success", PARTIAL: "info", AMBIGUOUS: "warning", MISMATCH: "danger", MISSING_REFERENCE: "danger",
};

/** What kind of finding a word is: an acoustically confirmed error needs a validated recognizer; everything else is an observation. */
function finding(w: ReviewWord): { label: string; tone: "danger" | "warning" | "default" } {
  if (w.status === "FAIL") return { label: "acoustically confirmed error", tone: "danger" };
  if (w.suggested === "FAIL") return { label: "suspected error (advisory)", tone: "warning" };
  return { label: "uncertain observation", tone: "default" };
}

function Word({ w, play }: { w: ReviewWord; play: (start: number, end: number) => void }) {
  const f = finding(w);
  return (
    <li className="space-y-1 rounded-md border border-border p-2 text-xs" aria-label={`Word ${w.word}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span lang="fa" className="text-lg">{w.word}</span>
        <Badge variant={f.tone}>{f.label}</Badge>
        {w.advisory && <Badge variant="outline">recognizer not validated</Badge>}
        {w.audio_window && <Button size="sm" variant="secondary" onClick={() => play(w.audio_window!.start, w.audio_window!.end)}>Play the word</Button>}
      </div>
      <p>Expected: <span dir="ltr" className="font-medium">{w.expected ?? "—"}</span> · heard phones: <span dir="ltr">{w.heard || "—"}</span></p>
      {w.candidates.length > 0 && (
        <p dir="ltr" className="text-muted-foreground">
          {w.candidates.map((c) => `${c.reading} (${c.kind}) ${c.loss["1.0"] ?? "—"}`).join(" · ")}
          <span className="ml-1">(loss per frame on the original take: lower fits better)</span>
        </p>
      )}
      <ul className="list-disc ps-4">{w.evidence.map((e, i) => <li key={i}>{e}</li>)}</ul>
    </li>
  );
}

function Proposal({ p, versionId, label, run, reviewer, onChanged }: { p: RepairProposal; versionId: number; label: string; run: string; reviewer: string;
  onChanged: () => void }) {
  const [maxEur, setMaxEur] = useState(String(Math.max(0.01, p.estimated_eur ?? 0).toFixed(2)));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try { await fn(); onChanged(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  return (
    <div className="space-y-2 rounded-md border border-border p-2 text-xs" aria-label={`Repair of ${p.word}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span lang="fa" className="text-base">{p.word}</span>
        <Badge variant="info">{p.status}</Badge>
        <span>likely cause: <span className="font-medium">{p.suspected_root_cause}</span></span>
        <span dir="ltr">target {p.verified_target_reading} /{p.verified_target_phonemes}/</span>
      </div>
      <ul className="list-disc ps-4">{p.evidence.map((e, i) => <li key={i}>{e}</li>)}</ul>
      {p.strategies.map((s, i) => (
        <div key={s.type} className="flex flex-wrap items-center gap-2">
          <Badge variant="outline">{s.type}</Badge>
          <span lang="fa" className="text-base">{s.form}</span>
          <span className="text-muted-foreground">{s.reason}</span>
          <Button size="sm" disabled={busy || !reviewer || p.attempts.some((a) => a.form === s.form)}
            onClick={() => act(() => api.repairPpieWord(versionId, label, run, p.block_id, p.word_id, { strategy: i, max_eur: Number(maxEur), by: reviewer }))}>
            Voice this block again
          </Button>
        </div>
      ))}
      {p.strategies.length > 0 && (
        <label className="flex items-center gap-2">
          Most it may cost (EUR, estimate {p.estimated_eur ?? "—"})
          <Input className="h-7 w-24" aria-label="Most it may cost" value={maxEur} onChange={(e) => setMaxEur(e.target.value)} />
          {!reviewer && <span className="text-rose-600 dark:text-rose-400">enter your name above to authorize</span>}
        </label>
      )}
      {p.attempts.map((a, i) => (
        <div key={i} className="space-y-1 rounded-md bg-muted/40 p-2" aria-label={`Attempt ${i + 1}`}>
          <div className="flex flex-wrap items-center gap-2">
            <span>Attempt {i + 1}: {a.strategy}</span>
            <Badge variant={a.verification.final_decision === "ACCEPTABLE" ? "success" : a.verification.final_decision === "REJECTED" ? "danger" : "outline"}>
              {a.verification.final_decision === "ACCEPTABLE" ? "verified correction" : a.verification.final_decision}
            </Badge>
            <span>target resolved: {String(a.verification.target_error_resolved)} · new errors: {a.verification.new_errors_detected.join(", ") || "none"}</span>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <span>Original</span><audio controls preload="none" src={api.ppieReviewAudioUrl(versionId, label, run, p.block_id)} aria-label="Original take" />
            <span>New</span><audio controls preload="none" src={api.ppieReviewAudioUrl(versionId, label, run, p.block_id, p.word_id, i)} aria-label="New take" />
          </div>
          {p.status !== "ACCEPTED" && p.status !== "REJECTED" && (
            <div className="flex gap-2">
              <Button size="sm" disabled={busy || !reviewer} onClick={() => act(() => api.acceptPpieRepair(versionId, label, run, p.block_id, p.word_id, { attempt: i, by: reviewer }))}>
                Keep the new take
              </Button>
              <Button size="sm" variant="secondary" disabled={busy || !reviewer} onClick={() => act(() => api.rejectPpieRepair(versionId, label, run, p.block_id, p.word_id, reviewer))}>
                Keep the original
              </Button>
            </div>
          )}
        </div>
      ))}
      {error && <p className="text-rose-600 dark:text-rose-400">{error}</p>}
    </div>
  );
}

function Block({ b, versionId, label, run, reviewer, onChanged }: { b: ReviewBlock; versionId: number; label: string; run: string; reviewer: string;
  onChanged: () => void }) {
  const audio = useRef<HTMLAudioElement>(null);
  const play = (start: number, end: number) => {
    const el = audio.current;
    if (!el) return;
    el.currentTime = Math.max(0, start - 0.3);
    void el.play();
    const stop = () => { if (el.currentTime >= end + 0.3) { el.pause(); el.removeEventListener("timeupdate", stop); } };
    el.addEventListener("timeupdate", stop);
  };
  return (
    <div className="space-y-3" aria-label="Block review">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium" dir="ltr">{b.block_id}</span>
        <Badge variant={ALIGN_TONE[b.alignment.status] ?? "default"}>{b.alignment.status}</Badge>
        <span className="text-xs text-muted-foreground">{b.alignment.confidence_note}</span>
      </div>
      <p lang="fa" dir="rtl" className="rounded-md bg-muted/40 p-2 text-base leading-8">{b.text}</p>
      <p className="rounded-md border border-border p-2 text-sm" aria-label="English passage">{b.alignment.english_text || "No English passage matched."}</p>
      {b.alignment.issues.length > 0 && <ul className="list-disc ps-4 text-xs">{b.alignment.issues.map((x, i) => <li key={i}>{x}</li>)}</ul>}
      {b.voice_text_differs.length > 0 && (
        <p className="rounded-md border border-rose-500/30 p-2 text-xs" aria-label="Not the approved text">
          This take was voiced from another text than the approved one: <span lang="fa">{b.voice_text_differs.join(" · ")}</span>
        </p>
      )}
      {b.has_take ? <audio ref={audio} controls preload="none" src={api.ppieReviewAudioUrl(versionId, label, run, b.block_id)} aria-label="Original take" />
        : <p className="text-xs text-muted-foreground">No take of this block in this narration.</p>}
      <p className="text-xs text-muted-foreground">
        PASS {b.counts.PASS ?? 0} · suspected {b.counts.FAIL ?? 0} · uncertain {b.counts.UNCERTAIN ?? 0} · not evaluable {b.counts.NOT_EVALUABLE ?? 0}
      </p>
      <ul className="space-y-2">{b.flagged.map((w) => <Word key={w.n} w={w} play={play} />)}</ul>
      {b.proposals.map((p) => <Proposal key={p.word_id} p={p} versionId={versionId} label={label} run={run} reviewer={reviewer} onChanged={onChanged} />)}
    </div>
  );
}

/** The review of a Persian narration after the voice: alignment with the English story, what the recognizer heard, repairs the owner authorizes. */
export function AudioReview({ versionId, reviewer }: { versionId: number | null; reviewer: string }) {
  const listing = useApi(() => (versionId == null ? Promise.resolve(null) : api.ppieReviews(versionId)), [versionId]);
  const [label, setLabel] = useState<string | null>(null);
  const [run, setRun] = useState<string | null>(null);
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["key"]>("flagged");
  const [picked, setPicked] = useState<string | null>(null);
  const [agents, setAgents] = useState(false);
  const [maxEur, setMaxEur] = useState("0");
  const [started, setStarted] = useState<string | null>(null);
  const narrations = listing.data?.narrations ?? [];
  const current = narrations.find((x) => x.label === label) ?? narrations.find((x) => x.runs.length > 0) ?? narrations[0];
  const runId = run ?? current?.runs[0] ?? null;
  const overview = useApi(() => (versionId == null || !current || !runId ? Promise.resolve(null) : api.ppieReview(versionId, current.label, runId)),
    [versionId, current?.label, runId]);
  const blocks = useMemo(() => (overview.data?.blocks ?? []).filter(FILTERS.find((f) => f.key === filter)!.keep), [overview.data, filter]);
  if (versionId == null) return <EmptyState title="No version" description="This language has no story version yet." />;
  if (listing.error) return <ErrorState message={listing.error} onRetry={listing.refetch} />;
  if (!listing.data) return <Skeleton className="h-64 w-full" />;
  const start = async () => {
    if (!current) return;
    const made = await api.startPpieReview(versionId, current.label, { agents, max_eur: agents ? Number(maxEur) : 0, by: reviewer });
    setStarted(`Review ${made.run_id} started: it takes several minutes. Reload to see it.`);
  };
  const s = overview.data?.summary;
  const block = blocks.find((b) => b.block_id === picked) ?? blocks[0];
  return (
    <div className="space-y-3">
      <p className="rounded-md border border-border p-2 text-xs" aria-label="Recognizer validation">
        {listing.data.validation.validated ? "The recognizer passed its validation on human-labelled clips."
          : `The recognizer is not validated (${listing.data.validation.labelled_clips} human-labelled clips): every decision is advisory, nothing is rejected or voiced again on its own.`}
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <select className="h-8 rounded-md border border-border bg-background px-2 text-sm" aria-label="Narration" value={current?.label ?? ""}
          onChange={(e) => { setLabel(e.target.value); setRun(null); setPicked(null); }}>
          {narrations.map((x) => <option key={x.label} value={x.label}>{x.label} ({x.runs.length} reviews)</option>)}
        </select>
        {current && current.runs.length > 0 && (
          <select className="h-8 rounded-md border border-border bg-background px-2 text-sm" aria-label="Review run" value={runId ?? ""}
            onChange={(e) => { setRun(e.target.value); setPicked(null); }}>
            {current.runs.map((r) => <option key={r} value={r}>{r}</option>)}
          </select>
        )}
        <label className="flex items-center gap-1 text-xs"><input type="checkbox" checked={agents} onChange={(e) => setAgents(e.target.checked)} /> use the agents (billable)</label>
        {agents && <Input className="h-8 w-24" aria-label="Agent limit" value={maxEur} onChange={(e) => setMaxEur(e.target.value)} />}
        <Button size="sm" disabled={!current?.folder_exists || (agents && !reviewer)} onClick={() => void start()}>Start a review</Button>
      </div>
      {started && <p className="text-xs">{started}</p>}
      {!runId ? <EmptyState title="No review" description="This narration has not been reviewed yet." /> : overview.error ? <ErrorState message={overview.error} onRetry={overview.refetch} />
        : !s ? <Skeleton className="h-64 w-full" /> : (
        <>
          <p className="text-xs leading-5 text-muted-foreground" aria-label="Review summary">
            {s.blocks_reviewed} blocks · {s.words.toLocaleString()} words · suggested PASS {s.suggested.PASS ?? 0} · suspected {s.suggested.FAIL ?? 0} · uncertain{" "}
            {s.suggested.UNCERTAIN ?? 0} · not evaluable {s.suggested.NOT_EVALUABLE ?? 0} · {s.proposals} repair proposals · spent {s.spent_by_run_eur} EUR ·
            alignment (uncalibrated): {Object.entries(overview.data!.alignment_summary?.blocks ?? {}).map(([k, v]) => `${k} ${v}`).join(", ")} ·{" "}
            {Object.keys(s.voice_text_differs_from_approved).length} blocks voiced from another text than the approved one
          </p>
          <div className="flex flex-wrap gap-2" role="tablist" aria-label="Blocks">
            {FILTERS.map((f) => (
              <Button key={f.key} size="sm" variant={filter === f.key ? "default" : "secondary"} onClick={() => { setFilter(f.key); setPicked(null); }}>
                {f.label} <span className="opacity-70">({(overview.data?.blocks ?? []).filter(f.keep).length})</span>
              </Button>
            ))}
          </div>
          {!block ? <EmptyState title="No block here" description="No block matches this filter." /> : (
            <div className="grid gap-4 lg:grid-cols-[14rem_1fr]">
              <ul className="max-h-[36rem] space-y-1 overflow-y-auto" aria-label="Reviewed blocks">
                {blocks.map((b) => (
                  <li key={b.block_id}>
                    <button type="button" onClick={() => setPicked(b.block_id)}
                      className={`flex w-full items-center justify-between rounded-md border px-3 py-1.5 text-start text-xs ${b.block_id === block.block_id ? "border-primary bg-primary/5" : "border-border"}`}>
                      <span dir="ltr">{b.block_id.replace(/^[A-Z]+_/, "")}</span>
                      <span>{b.counts.FAIL ?? 0} / {b.counts.UNCERTAIN ?? 0}</span>
                    </button>
                  </li>
                ))}
              </ul>
              <Block key={block.block_id} b={block} versionId={versionId} label={current!.label} run={runId} reviewer={reviewer} onChanged={overview.refetch} />
            </div>
          )}
        </>
      )}
    </div>
  );
}
