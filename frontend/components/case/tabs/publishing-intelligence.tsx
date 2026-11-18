"use client";

import { useEffect, useRef, useState } from "react";
import { ApiError, apiFileUrl, publishingAiApi, publishingApi, type AiClip, type AiRun, type AiState, type PlanApproval, type PlanApprovalResult, type PlanApprovalRow, type PublicationView, type StudioSnapshot } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const PLATFORM_LABEL: Record<string, string> = { youtube: "YouTube", facebook: "Facebook", instagram: "Instagram", tiktok: "TikTok" };
const SCORE_LABEL: Record<string, string> = { hook_3s: "Hook (3 s)", audience_appeal: "Appeal", curiosity: "Curiosity", spoiler_risk: "Spoiler risk", follower_conversion: "Followers", film_conversion: "Full film" };
const DISABLED = "disabled:opacity-100 disabled:bg-muted disabled:text-muted-foreground disabled:border disabled:border-dashed disabled:border-border disabled:cursor-not-allowed";
const POLL_MS = 2500;

const message = (e: unknown) => (e instanceof ApiError || e instanceof Error ? e.message : String(e));

function Score({ name, value }: { name: string; value: number }) {
  const risky = name === "spoiler_risk";
  const bad = risky ? value >= 7 : value <= 3;
  return <span title={SCORE_LABEL[name]} className={`inline-block min-w-8 rounded px-1.5 py-0.5 text-center text-xs tabular-nums ${bad ? "bg-rose-500/15 text-rose-700 dark:text-rose-400" : "bg-muted"}`}>{value}</span>;
}

function Steps({ run }: { run: AiRun }) {
  return (
    <ol className="space-y-1 text-sm" data-testid="ai-progress">
      {run.progress.map((s) => (
        <li key={s.step} className="flex items-center gap-2" data-testid={`ai-step-${s.step}`}>
          <Badge variant={s.status === "done" ? "success" : s.status === "failed" ? "danger" : s.status === "running" ? "info" : "default"}>{s.status}</Badge>
          <span>{s.label}</span>
          {s.detail && <span className="text-xs text-muted-foreground">{s.detail}</span>}
        </li>
      ))}
    </ol>
  );
}

/** The AI publishing plan: the owner's explicit start, its progress, what it recommends (and why) and what it cost. Nothing here calls a model except the Generate button. */
export function AiPanel({ caseId, language, studio, data, error, refetch, onChanged }: { caseId: number; language: string; studio: StudioSnapshot; data: AiState | null; error: string | null; refetch: () => void; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [proofing, setProofing] = useState(false);
  const [proofread, setProofread] = useState<string | null>(null);
  const wasRunning = useRef(false);
  const running = Boolean(data?.running);

  useEffect(() => {
    if (!running) {
      if (wasRunning.current) { wasRunning.current = false; onChanged(); }
      return;
    }
    wasRunning.current = true;
    const timer = setInterval(refetch, POLL_MS);
    return () => clearInterval(timer);
  }, [running, refetch, onChanged]);

  if (!data) return <Card><CardContent className="py-4 text-sm text-muted-foreground">{error ?? "Loading the AI publishing plan…"}</CardContent></Card>;
  const latest = data.latest;
  const result = latest?.result ?? null;
  const upToDate = Boolean(data.up_to_date);
  const start = async (force: boolean) => {
    setBusy(true); setProblem(null); setConfirming(false);
    try { await publishingAiApi.generate(caseId, language, force); } catch (e) { setProblem(message(e)); } finally { setBusy(false); refetch(); onChanged(); }
  };
  const proofreadNow = async () => {
    setProofing(true); setProblem(null); setProofread(null);
    try {
      const r = await publishingAiApi.proofread(caseId, language);
      const pieces = r.changed.flatMap((c) => c.changes).length;
      setProofread(`Proofread ${r.checked} texts: ${r.changed.length} corrected (${pieces} corrections), ${r.refused.length} answers refused and left as they were. ${r.usage.calls} model calls, EUR ${r.usage.cost_eur.toFixed(4)}.`);
    } catch (e) { setProblem(message(e)); } finally { setProofing(false); refetch(); onChanged(); }
  };
  const label = latest ? (upToDate ? "Regenerate Publishing Plan" : "Update Publishing Plan") : "Generate Publishing Plan";
  const blocked = !data.scheduled || data.emergency_stop || running || busy || !data.estimate;
  const move = async (platform: string, order: string[]) => {
    try { await publishingAiApi.order(caseId, language, platform, order); onChanged(); } catch (e) { setProblem(message(e)); }
  };
  const orderOf = (platform: string): string[] => {
    const pubs = studio.assets.flatMap((a) => a.publications).filter((p) => p.platform === platform && p.kind === "short");
    return [...pubs].sort((a, b) => (a.sequence ?? 99) - (b.sequence ?? 99) || a.asset_key.localeCompare(b.asset_key)).map((p) => p.asset_key);
  };
  const reasons = (platform: string) => Object.fromEntries((result?.analysis.sequences[platform] ?? []).map((s) => [s.clip_key, s.reason]));

  return (
    <Card data-testid="ai-panel">
      <CardHeader>
        <CardTitle>AI publishing plan</CardTitle>
        <div className="flex items-center gap-2">
          {confirming ? (
            <>
              <span className="text-xs text-muted-foreground">Asks the models again and replaces the calendar and the Short order (texts you edited are kept){data.estimate ? ` (about EUR ${data.estimate.eur_low.toFixed(2)}–${data.estimate.eur_high.toFixed(2)})` : ""}.</span>
              <Button type="button" size="sm" onClick={() => void start(true)}>Yes, regenerate</Button>
              <Button type="button" size="sm" variant="ghost" onClick={() => setConfirming(false)}>Cancel</Button>
            </>
          ) : (
            <Button type="button" size="sm" className={DISABLED} disabled={blocked} onClick={() => (latest && upToDate ? setConfirming(true) : void start(false))}>{label}</Button>
          )}
          {language === "fa" && !confirming && (
            <Button type="button" size="sm" variant="outline" className={DISABLED} disabled={!data.scheduled || data.emergency_stop || running || proofing} data-testid="proofread-now" onClick={() => void proofreadNow()}
              title="Sends every Persian text of the Publishing tab to Sonnet to be corrected (spelling, half-spaces, punctuation, grammar, stray English words). Paid: one call for all of them.">{proofing ? "Proofreading…" : "Proofread the Persian texts"}</Button>
          )}
        </div>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        {data.emergency_stop && <p role="alert" className="text-rose-700 dark:text-rose-400">The Emergency Stop is active: no paid call can start.</p>}
        {data.reason && <p className="text-amber-700 dark:text-amber-400">{data.reason}</p>}
        {!running && data.estimate && (
          <p className="text-xs text-muted-foreground" data-testid="ai-estimate">
            Estimated cost of a run: about EUR {data.estimate.eur_low.toFixed(2)}–{data.estimate.eur_high.toFixed(2)} · {data.estimate.calls} model calls · {Object.values(data.estimate.models).join(" + ")}. {data.estimate.basis}
            {!latest && " Nothing is generated until you click."}
          </p>
        )}
        {problem && <p role="alert" className="text-rose-700 dark:text-rose-400">{problem}</p>}
        {proofread && <p role="status" className="text-xs text-emerald-700 dark:text-emerald-400" data-testid="proofread-result">{proofread}</p>}
        {running && data.running && <div><p className="mb-2 font-medium">Generating… (this page updates by itself)</p><Steps run={data.running} /></div>}
        {data.failed && !running && (
          <div role="alert" className="space-y-1 text-rose-700 dark:text-rose-400" data-testid="ai-failed">
            <p className="font-medium">The last run did not finish.</p><p>{data.failed.error}</p><Steps run={data.failed} />
          </div>
        )}
        {latest && result && (
          <>
            <p className="text-xs text-muted-foreground" data-testid="ai-usage">
              Saved plan from {new Date(latest.finished_at ?? latest.started_at).toLocaleString()} · {latest.usage?.calls} model calls · {latest.usage?.input_tokens} in / {latest.usage?.output_tokens} out tokens ·
              EUR {latest.usage?.cost_eur.toFixed(4)} (USD {latest.usage?.cost_usd.toFixed(4)}) · {latest.usage?.models.join(", ")}
              {data.stale && " · the case has changed since: update it to refresh"}
            </p>
            {Object.keys(result.platform_errors).length > 0 && <p className="text-rose-700 dark:text-rose-400">Metadata failed for: {Object.entries(result.platform_errors).map(([p, e]) => `${p} (${e})`).join("; ")}</p>}
            {Object.keys(result.unresolved).length > 0 && (
              <div className="text-amber-700 dark:text-amber-400">Points the checks still found wrong (also shown on each publication):
                <ul className="list-disc pl-5">{Object.entries(result.unresolved).flatMap(([p, items]) => items.map((i, n) => <li key={`${p}-${n}-${i}`}>{PLATFORM_LABEL[p]}: {i}</li>))}</ul>
              </div>
            )}
            <div>
              <p className="mb-1 font-medium">The Shorts, judged (1 poor – 10 excellent; for spoiler risk 10 is high)</p>
              <div className="overflow-x-auto"><table className="w-full min-w-[640px] text-left text-xs"><thead className="text-muted-foreground"><tr><th className="py-1 pr-2">Clip</th>{Object.keys(SCORE_LABEL).map((k) => <th key={k} className="px-1">{SCORE_LABEL[k]}</th>)}<th className="px-2">Why</th></tr></thead>
                <tbody>{result.analysis.clips.map((c: AiClip) => (
                  <tr key={c.clip_key} className="border-t border-border align-top" data-testid={`ai-clip-${c.clip_key}`}>
                    <td className="py-1 pr-2 font-medium">{c.clip_key}</td>
                    {Object.keys(SCORE_LABEL).map((k) => <td key={k} className="px-1 py-1"><Score name={k} value={c.scores[k]} /></td>)}
                    <td className="px-2 py-1 text-muted-foreground">{c.strengths} {c.risks && <span>Risk: {c.risks}</span>}</td>
                  </tr>))}</tbody></table></div>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              {Object.keys(result.analysis.sequences).map((platform) => {
                const order = orderOf(platform);
                return (
                  <div key={platform} className="rounded border border-border p-2" data-testid={`ai-order-${platform}`}>
                    <p className="mb-1 font-medium">{PLATFORM_LABEL[platform]}: order of the Shorts</p>
                    <ol className="space-y-1">
                      {order.map((key, i) => (
                        <li key={key} className="flex items-start gap-2 text-xs" data-testid={`ai-order-${platform}-${key}`}>
                          <span className="w-4 tabular-nums text-muted-foreground">{i + 1}</span><span className="w-8 font-medium">{key}</span>
                          <span className="flex-1 text-muted-foreground">{reasons(platform)[key]}</span>
                          <Button type="button" size="sm" variant="ghost" aria-label={`Move ${key} up on ${platform}`} className={DISABLED} disabled={i === 0} onClick={() => void move(platform, swap(order, i, i - 1))}>↑</Button>
                          <Button type="button" size="sm" variant="ghost" aria-label={`Move ${key} down on ${platform}`} className={DISABLED} disabled={i === order.length - 1} onClick={() => void move(platform, swap(order, i, i + 1))}>↓</Button>
                        </li>))}
                    </ol>
                  </div>);
              })}
            </div>
            <div className="space-y-1 text-xs">
              <p className="font-medium text-sm">Calendar (the proposal; the real times are editable below)</p>
              <p>{result.schedule.summary}</p>
              <ul className="list-disc pl-5 text-muted-foreground">{result.schedule.assumptions.map((a, i) => <li key={`${i}-${a}`}>{a}</li>)}</ul>
              <p className="text-amber-700 dark:text-amber-400" data-testid="ai-notice">{result.schedule.notice}</p>
              {result.schedule.slots.filter((s) => s.moved).map((s, n) => <p key={`${n}-${s.destination}-${s.asset}`} className="text-muted-foreground">{s.destination} {s.asset}: {s.moved}</p>)}
            </div>
            <div className="space-y-1 text-xs" data-testid="ai-routing">
              <p className="font-medium text-sm">Viewer routing advice</p>
              {Object.entries(result.analysis.routing).map(([d, r]) => <p key={d}><span className="font-medium">{d}</span>: {r.method.replaceAll("_", " ")}. <span className="text-muted-foreground">{r.reason}</span></p>)}
              {result.analysis.notes.map((n, i) => <p key={`${i}-${n}`} className="text-muted-foreground">{n}</p>)}
            </div>
            {latest.applied.kept && latest.applied.kept.length > 0 && (
              <p className="text-xs text-muted-foreground">Left as they were: {latest.applied.kept.map((k) => `${k.destination} ${k.asset} (${k.why})`).join("; ")}.</p>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function swap(order: string[], a: number, b: number): string[] {
  const copy = [...order];
  [copy[a], copy[b]] = [copy[b], copy[a]];
  return copy;
}

/** Every publication of the language in time order, each with an editable local time. A change of time voids an approval, as the server's policy says. */
export function CalendarPanel({ studio, onChanged }: { studio: StudioSnapshot; onChanged: () => void }) {
  const rows = studio.assets.flatMap((a) => a.publications.map((p) => ({ p, asset: a.key, kind: a.kind }))).sort((x, y) => (x.p.scheduled_at ?? "9").localeCompare(y.p.scheduled_at ?? "9") || x.p.platform.localeCompare(y.p.platform));
  return (
    <Card data-testid="calendar">
      <CardHeader><CardTitle>Release calendar</CardTitle><span className="text-xs text-muted-foreground">{studio.schedule?.timezone} · every time is editable until it is approved</span></CardHeader>
      <CardContent className="space-y-1">
        {rows.length === 0 && <p className="text-sm text-muted-foreground">Create the plan to see the calendar.</p>}
        {rows.map(({ p, asset, kind }) => <CalendarRow key={p.id} pub={p} asset={asset} kind={kind} onChanged={onChanged} />)}
      </CardContent>
    </Card>
  );
}

function CalendarRow({ pub, asset, kind, onChanged }: { pub: PublicationView; asset: string; kind: string; onChanged: () => void }) {
  const [value, setValue] = useState(pub.scheduled_local ?? "");
  const [seen, setSeen] = useState(pub.scheduled_local ?? "");
  const [error, setError] = useState<string | null>(null);
  if (seen !== (pub.scheduled_local ?? "")) { setSeen(pub.scheduled_local ?? ""); setValue(pub.scheduled_local ?? ""); }
  const dirty = value !== (pub.scheduled_local ?? "");
  const save = async () => {
    setError(null);
    try { await publishingApi.edit(pub.id, value ? { scheduled_local: value } : { clear_schedule: true }); } catch (e) { setError(message(e)); } finally { onChanged(); }
  };
  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-border py-1 text-xs" data-testid={`calendar-${pub.platform}-${pub.kind}-${pub.asset_key}`}>
      <input aria-label={`Time ${pub.platform} ${kind} ${asset}`} type="datetime-local" value={value} disabled={!pub.eligibility.edit} onChange={(e) => setValue(e.target.value)} className="rounded border border-border bg-background px-2 py-1" />
      <Button type="button" size="sm" variant="outline" className={DISABLED} disabled={!dirty || !pub.eligibility.edit} aria-label={`Set time ${pub.platform} ${kind} ${asset}`} onClick={() => void save()}>Set</Button>
      <span className="w-20 font-medium">{PLATFORM_LABEL[pub.platform]}</span><span className="w-16">{kind === "film" ? "Film" : asset}</span>
      <Badge variant={pub.status === "publication_approved" || pub.status === "scheduled" ? "success" : pub.status === "voided" || pub.status === "expired" ? "warning" : "default"}>{pub.status.replaceAll("_", " ")}</Badge>
      {error && <span className="text-rose-700 dark:text-rose-400">{error}</span>}
    </div>
  );
}

function Row({ row, tone }: { row: PlanApprovalRow; tone: "ok" | "out" | "kept" }) {
  return (
    <li className="py-1" data-testid={`approval-${tone}-${row.platform}-${row.kind}-${row.asset_key}`}>
      <span className="font-medium">{PLATFORM_LABEL[row.platform]}</span> {row.kind === "film" ? "film" : `Short ${row.asset_key}`}
      {row.scheduled_local && <span className="text-muted-foreground"> · {row.scheduled_local}</span>}
      {row.notes.map((n, i) => <span key={`${i}-${n}`} className="block text-muted-foreground">{n}</span>)}
      {row.reasons.length > 0 && <ul className="list-disc pl-5 text-rose-700 dark:text-rose-400">{row.reasons.map((r, i) => <li key={`${i}-${r}`}>{r}</li>)}</ul>}
    </li>
  );
}

/** One click that approves what may be approved: the owner first sees which publications will be approved and which are left out, with the reasons, then confirms. */
export function ApprovePlanPanel({ caseId, language, onChanged }: { caseId: number; language: string; onChanged: () => void }) {
  const [preview, setPreview] = useState<PlanApproval | null>(null);
  const [result, setResult] = useState<PlanApprovalResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const run = async (work: () => Promise<void>) => { setBusy(true); setProblem(null); try { await work(); } catch (e) { setProblem(message(e)); } finally { setBusy(false); } };
  return (
    <Card data-testid="approve-plan">
      <CardHeader>
        <CardTitle>Approve the publishing plan</CardTitle>
        <Button type="button" size="sm" className={DISABLED} disabled={busy} onClick={() => void run(async () => { setResult(null); setPreview(await publishingAiApi.approvalPreview(caseId, language)); })}>Approve Publishing Plan</Button>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {!preview && !result && <p className="text-muted-foreground">Shows exactly which publications would be approved and which are left out, and why, before anything is approved. Nothing is scheduled or published by an approval.</p>}
        {problem && <p role="alert" className="text-rose-700 dark:text-rose-400">{problem}</p>}
        {preview && !result && (
          <div className="space-y-3" data-testid="approval-preview">
            {preview.stopped && <p role="alert" className="text-rose-700 dark:text-rose-400">The Emergency Stop is active: nothing can be approved until it is released.</p>}
            <div><p className="font-medium">Will be approved ({preview.approve.length})</p><ul className="divide-y divide-border text-xs">{preview.approve.map((r) => <Row key={r.id} row={r} tone="ok" />)}</ul></div>
            <div><p className="font-medium">Left out ({preview.excluded.length})</p><ul className="divide-y divide-border text-xs">{preview.excluded.map((r) => <Row key={r.id} row={r} tone="out" />)}</ul></div>
            {preview.already_approved.length > 0 && <p className="text-xs text-muted-foreground">Already approved and unchanged: {preview.already_approved.length}.</p>}
            <div className="flex gap-2">
              <Button type="button" size="sm" className={DISABLED} disabled={busy || preview.stopped || preview.approve.length === 0} onClick={() => void run(async () => { setResult(await publishingAiApi.approvePlan(caseId, language)); setPreview(null); onChanged(); })}>Approve {preview.approve.length} publications</Button>
              <Button type="button" size="sm" variant="ghost" onClick={() => setPreview(null)}>Cancel</Button>
            </div>
          </div>
        )}
        {result && (
          <div className="space-y-2" data-testid="approval-result">
            <p className="font-medium text-emerald-700 dark:text-emerald-400">{result.approved.length} publications approved, {result.excluded.length} left out.</p>
            <ul className="divide-y divide-border text-xs">{result.approved.map((r) => <Row key={r.id} row={{ ...r, status: "publication_approved" }} tone="ok" />)}</ul>
            <ul className="divide-y divide-border text-xs">{result.excluded.map((r) => <Row key={r.id} row={r} tone="out" />)}</ul>
            <Button type="button" size="sm" variant="ghost" onClick={() => setResult(null)}>Close</Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function Preview({ url, kind }: { url: string | null; kind: string }) {
  if (!url) return <p className="text-xs text-muted-foreground">No preview available.</p>;
  return <video controls preload="none" data-testid="preview" className={kind === "film" ? "aspect-video w-72 rounded bg-black" : "aspect-[9/16] w-28 rounded bg-black"} src={apiFileUrl(url)} aria-label={`${kind} preview`} />;
}
