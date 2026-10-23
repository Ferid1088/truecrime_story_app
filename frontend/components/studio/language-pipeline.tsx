"use client";

import { useEffect, useState } from "react";
import { Check, ChevronDown, ChevronRight, Play, Sparkles } from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import type {
  BackgroundJob,
  CritiqueItem,
  GlossaryView,
  LanguagePipelineState,
  PipelineEstimate,
  FilmFactReport,
  PipelineDraft,
  PipelineLanguage,
  PipelineStage,
} from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { ErrorState } from "@/components/state";
import { Skeleton } from "@/components/ui/skeleton";
import { langLabel } from "@/lib/format";
import { AuditPanel, EvidenceAudit } from "@/components/studio/evidence-audit";

const MINUTES = [45, 50, 55, 60, 65, 70, 75, 80];
const LANGUAGE_ORDER = ["en", "fa", "de", "ar"];
const byLanguageOrder = (a: PipelineLanguage, b: PipelineLanguage) =>
  LANGUAGE_ORDER.indexOf(a.language) - LANGUAGE_ORDER.indexOf(b.language);
const STAGE_LABEL: Record<string, string> = {
  rewrite: "2 · Native rewrite",
  inspection: "3 · Independent inspection",
  final: "4 · Final text",
};

const eur = (v: number | null | undefined) => (v == null ? "—" : `€${v.toFixed(2)}`);
const n = (v: number | null | undefined) => (v == null ? "—" : v.toLocaleString());

function statusVariant(status: string): "success" | "warning" | "danger" | "default" {
  if (status === "ready") return "success";
  if (status === "needs_revision") return "danger";
  return "default";
}

export function LanguagePipeline({ caseId }: { caseId: number }) {
  const [minutes, setMinutes] = useState(60);
  const { data: state, error, loading, refetch } = useApi<LanguagePipelineState>(
    () => api.languagePipeline(caseId),
    [caseId],
    { keepPrevious: true },
  );
  const { data: estimate } = useApi<PipelineEstimate>(
    () => api.languagePipelineEstimate(caseId, minutes),
    [caseId, minutes],
    { keepPrevious: true },
  );
  const [job, setJob] = useState<BackgroundJob | null>(null);
  const [starting, setStarting] = useState<string | null>(null);
  const [startError, setStartError] = useState<string | null>(null);

  // While a job runs: follow it, and refresh the stored stages every few seconds.
  useEffect(() => {
    if (!job || job.status === "done" || job.status === "failed") return;
    const timer = setInterval(async () => {
      try {
        const next = await api.backgroundJob(job.id);
        setJob(next);
        refetch();
      } catch {
        /* the next tick retries */
      }
    }, 8000);
    return () => clearInterval(timer);
  }, [job, refetch]);

  async function run(languages?: string[]) {
    setStarting(languages?.join(",") ?? "all");
    setStartError(null);
    try {
      const started = await api.runLanguagePipeline(caseId, {
        languages,
        requested_minutes: minutes,
      });
      setJob(await api.backgroundJob(started.job_id));
    } catch (e) {
      setStartError(e instanceof Error ? e.message : String(e));
    } finally {
      setStarting(null);
    }
  }

  if (loading && !state) return <Skeleton className="h-72" />;
  if (error && !state) return <ErrorState message={error} onRetry={refetch} />;
  if (!state) return null;

  const running = job != null && (job.status === "queued" || job.status === "running");
  const events = ((job?.result as { events?: Record<string, unknown>[] } | undefined)?.events ?? []).slice(-6);

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Language pipeline</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <p className="text-muted-foreground">
            One English draft of 90 minutes is written once from the shared story plan and all the evidence. An
            independent auditor reads it once against the evidence and reports; nothing is changed by the report.
            The draft is then approved automatically and every language, English included, re-tells it
            (stage 2), is criticised by an independent inspector (stage 3) and finished from that critique
            (stage 4): exactly three model calls per language, no retry. Each film text is held to markers and
            length only; its numbers (and, for Latin-script languages, names) are compared with the draft in a
            report.
          </p>
          <div className="flex flex-wrap items-end gap-3">
            <label className="text-xs text-muted-foreground">
              Film length (minutes)
              <Select
                className="mt-1 w-28"
                value={String(minutes)}
                onChange={(e) => setMinutes(Number(e.target.value))}
              >
                {MINUTES.map((m) => (
                  <option key={m} value={m}>
                    {m}–{m + 10}
                  </option>
                ))}
              </Select>
            </label>
            <Button onClick={() => run()} loading={starting === "all"} disabled={running}>
              <Play className="size-3.5" /> Run all languages
            </Button>
            {state.draft && (
              <span className="text-xs text-muted-foreground">
                Stages that already exist are not run again; a failed language resumes at its failed stage.
              </span>
            )}
          </div>
          {startError && <p className="text-sm text-destructive">{startError}</p>}
          {job && (
            <div className="rounded-md border border-border p-2 text-xs">
              <div className="flex items-center gap-2">
                <Badge variant={job.status === "failed" ? "danger" : job.status === "done" ? "success" : "info"}>
                  job {job.id} · {job.status}
                </Badge>
                {job.error && <span className="text-destructive">{job.error}</span>}
              </div>
              <ul className="mt-1 space-y-0.5 text-muted-foreground">
                {events.map((e, i) => (
                  <li key={i}>
                    {String(e.event)} {e.stage ? `· ${String(e.stage)}` : ""} {e.language ? `· ${String(e.language)}` : ""}
                    {e.words ? ` · ${String(e.words)} words` : ""}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {estimate && (
            <div className="rounded-md border border-dashed border-border p-2 text-xs">
              <b>ESTIMATE, not a price and not a spend:</b> {estimate.calls} calls ({estimate.large_calls} large: the draft by scene and the Sol stages; the rest are the gate validators), about {n(Math.round(estimate.total_tokens_in / 1000))}k tokens in and{" "}
              {n(Math.round(estimate.total_tokens_out / 1000))}k out, planning price {eur(estimate.total_eur)}.
              Actual so far (ledger): <b>{eur(state.cost_total_eur)}</b>
              {state.calls_total != null && <> in {state.calls_total} calls</>}.
              {estimate.vs_actual && estimate.vs_actual.some((r) => r.actual_calls != null) && (
                <details className="mt-1">
                  <summary className="cursor-pointer">Estimate against the ledger, per stage and language</summary>
                  <table className="mt-1 w-full">
                    <thead>
                      <tr className="text-left">
                        <th>Stage</th><th>Language</th><th>Est. calls</th><th>Actual calls</th>
                        <th>Estimate</th><th>Actual</th><th>Actual / estimate</th>
                      </tr>
                    </thead>
                    <tbody>
                      {estimate.vs_actual.map((r) => (
                        <tr key={`${r.stage}-${r.language ?? ""}`}>
                          <td>{r.stage}</td><td>{r.language ?? "shared"}</td>
                          <td>{n(r.estimated_calls)}</td><td>{n(r.actual_calls)}</td>
                          <td>{eur(r.estimated_eur)}</td><td>{eur(r.actual_eur)}</td>
                          <td>{r.ratio == null ? "—" : `×${r.ratio.toFixed(2)}`}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  <p className="text-muted-foreground">
                    Planning figures are calibrated on the ledger of case 8 (every attempt, failed and repaired
                    ones included). The gate rows of that case also hold its first validator runs, which read the
                    whole evidence pack and cost several times more than today&apos;s.
                  </p>
                </details>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {[...state.languages].sort(byLanguageOrder).map((lang) => (
          <LanguageCard
            key={lang.language}
            caseId={caseId}
            lang={lang}
            draft={state.draft}
            busy={starting === lang.language}
            running={running}
            onRun={() => run([lang.language])}
            onChanged={refetch}
            minutes={minutes}
            onJob={setJob}
          />
        ))}
      </div>

      <DraftCard caseId={caseId} draft={state.draft} />
      {state.draft && <EvidenceAudit audit={state.draft.evidence_audit} checks={state.draft.draft_checks} />}
      <RetiredMethod rows={state.retired_method} />

      <Glossary caseId={caseId} />
      <CostTable state={state} />
    </div>
  );
}

function DraftCard({ caseId, draft }: { caseId: number; draft: PipelineDraft | null }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState<string | null>(null);
  useEffect(() => {
    if (!open || text != null) return;
    api.languagePipeline(caseId, true).then((s) => setText(s.draft?.text ?? ""));
  }, [open, text, caseId]);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Shared English draft (stage 1, written once)</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-sm">
        {!draft ? (
          <p className="text-muted-foreground">Not written yet. Running the pipeline writes it first.</p>
        ) : (
          <>
            <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
              <span>v{draft.id}</span>
              <span>{langLabel(draft.language)}</span>
              <span>{draft.model}</span>
              <span>{n(draft.words)} words · {draft.minutes} min</span>
              <span>cost {eur(draft.cost_eur)}</span>
              <span title={draft.prompt_sha256 ?? ""}>
                prompt {draft.prompt?.split("/").pop()} {draft.prompt_sha256?.slice(0, 8)}
              </span>
              {draft.approval && (
                <span data-testid="draft-approval">
                  approved {draft.approval.by === "automatic" ? "automatically" : `by ${draft.approval.by}`} ·{" "}
                  {new Date(draft.approval.at).toLocaleString()}
                </span>
              )}
            </div>
            {draft.plan_note && (
              <p className="rounded-md border border-border bg-muted/40 px-2 py-1.5 text-xs text-muted-foreground"
                 data-testid="plan-note">
                {draft.plan_note}
              </p>
            )}
            <Button variant="ghost" size="sm" onClick={() => setOpen(!open)}>
              {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />} Read the draft
            </Button>
            {open && (
              <div className="max-h-[60vh] overflow-auto whitespace-pre-wrap rounded-md border border-border p-3 text-sm leading-7">
                {text ?? "Loading…"}
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

function LanguageCard({
  caseId, lang, draft, busy, running, onRun, onChanged, minutes, onJob,
}: {
  caseId: number;
  lang: PipelineLanguage;
  draft: PipelineDraft | null;
  busy: boolean;
  running: boolean;
  onRun: () => void;
  onChanged: () => void;
  minutes: number;
  onJob: (job: BackgroundJob) => void;
}) {
  const final = lang.stages.final;
  const [reading, setReading] = useState<string | null>(null);
  const [instruction, setInstruction] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function approve() {
    setError(null);
    try {
      await api.approveFinal(caseId, lang.language);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function improve() {
    setError(null);
    try {
      const started = await api.improveFinal(caseId, lang.language, instruction, minutes);
      onJob(await api.backgroundJob(started.job_id));
      setInstruction("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  async function read() {
    if (reading != null) return setReading(null);
    setReading((await api.getFinal(caseId, lang.language)).text ?? "");
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          {langLabel(lang.language)}
          {final && <Badge variant={statusVariant(final.status)}>{final.status.replace("_", " ")}</Badge>}
          {lang.approved && (
            <Badge variant="success" data-testid="final-approval">
              <Check className="size-3" /> approved {final?.approved_by === "automatic" ? "automatically" : ""}
              {final?.approved_at ? ` · ${new Date(final.approved_at).toLocaleString()}` : ""}
            </Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        {!final && lang.waiting_reason && (
          <p className="rounded-md border border-border bg-muted/40 px-2 py-1.5 text-xs text-muted-foreground"
             data-testid="waiting-reason">
            {lang.waiting_reason}
          </p>
        )}
        {draft && (
          <p className="text-xs text-muted-foreground">
            Re-told from the English draft v{draft.id} and the shared plan; it reads no evidence.
            {lang.blocking_gates && ` Blocking gates: ${lang.blocking_gates.join(", ")}.`}
          </p>
        )}
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-muted-foreground">
              <th className="py-1">Stage</th><th>Model</th><th>Effort</th><th>Min</th><th>Words</th><th>€</th>
            </tr>
          </thead>
          <tbody>
            {(["rewrite", "inspection", "final"] as const).map((k) => {
              const s = lang.stages[k];
              return (
                <tr key={k} className="border-t border-border">
                  <td className="py-1">{STAGE_LABEL[k]}</td>
                  <td>{s?.model ?? "—"}</td>
                  <td>{s?.reasoning_effort ?? "—"}</td>
                  <td>{k === "inspection" ? "—" : (s?.minutes ?? "—")}</td>
                  <td>{k === "inspection" ? "—" : s ? n(s.words) : "—"}</td>
                  <td>{s ? eur(s.cost_eur) : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>

        {lang.critique && <Critique critique={lang.critique} />}
        {lang.gates && <Gates lang={lang} />}
        {lang.act_lengths && <ActLengths lengths={lang.act_lengths} />}
        {lang.evidence_audit && (
          <details className="rounded-md border border-border p-2 text-xs" data-testid="final-audit">
            <summary className="cursor-pointer">
              <b>Evidence audit of this final text</b> (report only, never blocks): {(lang.evidence_audit.items ?? []).length} items
            </summary>
            <AuditPanel audit={lang.evidence_audit} />
          </details>
        )}
        {lang.length_correction && <BoundedLengthCorrection report={lang.length_correction} final={final} />}
        {lang.fact_report && <FactReport report={lang.fact_report} />}

        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={onRun} loading={busy} disabled={running}>
            <Play className="size-3.5" /> {final ? "Resume" : "Start"} {lang.language}
          </Button>
          {final && (
            <>
              <Button size="sm" variant="secondary" onClick={read}>Read final</Button>
              {!lang.approved && !lang.automatic_approval && (
                <Button size="sm" variant="outline" onClick={approve}>
                  <Check className="size-3.5" /> Approve final
                </Button>
              )}
            </>
          )}
        </div>
        {error && <p className="text-xs text-destructive">{error}</p>}
        {reading != null && <FilmText text={reading} direction={lang.direction} />}
        {final && (
          <div className="space-y-1">
            <Textarea
              rows={2}
              placeholder="Tell the final writer what to change (runs stage 4 again, through the gates)"
              value={instruction}
              onChange={(e) => setInstruction(e.target.value)}
            />
            <Button size="sm" variant="ghost" onClick={improve} disabled={running || instruction.trim().length < 3}>
              <Sparkles className="size-3.5" /> Improve final
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

/** A film text in its own writing direction. An act marker is a technical id: it stays left-to-right
 *  on its own line, so right-to-left text next to it cannot scramble it. */
function FilmText({ text, direction }: { text: string; direction: "ltr" | "rtl" }) {
  return (
    <div
      dir={direction}
      data-testid="film-text"
      className="max-h-[50vh] overflow-auto rounded-md border border-border p-3 leading-7"
    >
      {text.split(/\n{2,}/).map((paragraph, i) =>
        /^\[\[ACT:[^\]]+\]\]$/.test(paragraph.trim()) ? (
          <div key={i} dir="ltr" className="my-2 text-left font-mono text-xs text-muted-foreground" data-testid="film-marker">
            {paragraph.trim()}
          </div>
        ) : (
          <p key={i} className="mb-3 whitespace-pre-wrap">{paragraph}</p>
        ),
      )}
    </div>
  );
}

function Critique({ critique }: { critique: NonNullable<PipelineLanguage["critique"]> }) {
  const [open, setOpen] = useState(false);
  const scores = Object.entries(critique.scores ?? {}).filter(([, v]) => v != null);
  return (
    <div className="rounded-md border border-border p-2">
      <button className="flex w-full items-center gap-1 text-left text-xs font-medium" onClick={() => setOpen(!open)}>
        {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        Inspector critique · {critique.items.length} items
        {critique.verdict && <Badge variant={critique.verdict === "pass" ? "success" : "warning"}>{critique.verdict}</Badge>}
      </button>
      {scores.length > 0 && (
        <p className="mt-1 text-[11px] text-muted-foreground">
          {scores.map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(" · ")}
        </p>
      )}
      {open && (
        <div className="mt-2 space-y-2">
          <p className="text-xs">{critique.summary}</p>
          {critique.items.map((item: CritiqueItem, i: number) => (
            <div key={i} className="rounded border border-border p-2 text-xs">
              <Badge variant={item.severity === "high" ? "danger" : item.severity === "medium" ? "warning" : "default"}>
                {item.severity}
              </Badge>{" "}
              <span className="text-muted-foreground">{item.kind} · {item.act}</span>
              {item.quote && <p dir="auto" className="mt-1 italic">“{item.quote}”</p>}
              <p className="mt-1">{item.problem}</p>
              <p className="mt-1 text-muted-foreground">→ {item.instruction}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function RetiredMethod({ rows }: { rows: LanguagePipelineState["retired_method"] }) {
  if (!rows || rows.length === 0) return null;
  return (
    <details className="rounded-md border border-dashed border-border p-3 text-xs" data-testid="retired-method">
      <summary className="cursor-pointer">
        <Badge>retired method</Badge> {rows.length} rows of the fact-check loop (history; never the source of a stage)
      </summary>
      <table className="mt-2 w-full">
        <thead>
          <tr className="text-left text-muted-foreground">
            <th>Version</th><th>Kind</th><th>Round</th><th>€</th><th>Reason</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-t border-border">
              <td>v{r.id}</td><td>{r.kind}</td><td>{r.round ?? "—"}</td><td>{eur(r.cost_eur)}</td>
              <td>{r.retired?.reason ?? r.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

function FactReport({ report }: { report: FilmFactReport }) {
  const added = report.added.figures.length + report.added.names.length;
  const missing = report.missing.figures.length + report.missing.names.length;
  return (
    <details className="rounded-md border border-border p-2 text-xs" data-testid="fact-report">
      <summary className="cursor-pointer">
        <b>Film text against the master</b> (report only, never blocks): {added} added, {missing} missing
      </summary>
      <p className="mt-1 text-muted-foreground">
        Compared by code, no model call. {report.note} Whether a difference is an error is your reading.
      </p>
      <p className="mt-1"><b>Added (in the film text, not in the master)</b></p>
      {report.added.figures.map((f) => (
        <p key={f.figure} className="ml-2">{f.figure}{f.context ? ` · …${f.context}…` : ""}</p>
      ))}
      {report.added.names.length > 0 && <p className="ml-2">Names: {report.added.names.join(", ")}</p>}
      {added === 0 && <p className="ml-2 text-muted-foreground">Nothing.</p>}
      <p className="mt-1"><b>Missing (in the master, not in the film text)</b></p>
      {report.missing.figures.length > 0 && <p className="ml-2">Figures: {report.missing.figures.join(", ")}</p>}
      {report.missing.names.length > 0 && <p className="ml-2">Names: {report.missing.names.join(", ")}</p>}
      {report.missing.evidence_names.length > 0 && (
        <p className="ml-2">People and places of the evidence: {report.missing.evidence_names.join(", ")}</p>
      )}
      {missing === 0 && <p className="ml-2 text-muted-foreground">Nothing.</p>}
      {report.script_hygiene && (
        <div className="mt-2" data-testid="script-hygiene">
          <p><b>Script hygiene</b> (information only)</p>
          {Object.entries(report.script_hygiene)
            .filter(([, v]) => typeof v === "object")
            .map(([name, v]) => {
              const item = v as { count: number; example: { line: number; column: number; context: string } | null };
              return (
                <p key={name} className="ml-2">
                  {name.replace(/_/g, " ")}: {item.count}
                  {item.example && ` · first at line ${item.example.line}, column ${item.example.column}: …${item.example.context}…`}
                </p>
              );
            })}
        </div>
      )}
    </details>
  );
}

function ActLengths({ lengths }: { lengths: NonNullable<PipelineLanguage["act_lengths"]> }) {
  return (
    <details className="rounded-md border border-border p-2 text-xs" data-testid="act-lengths">
      <summary className="cursor-pointer">
        <b>Words per act against the target</b> (report only, never blocks)
      </summary>
      <table className="mt-1 w-full">
        <thead>
          <tr className="text-left text-muted-foreground"><th>Act</th><th>Words</th><th>Target</th><th>Share</th></tr>
        </thead>
        <tbody>
          {Object.entries(lengths).map(([act, l]) => (
            <tr key={act} className="border-t border-border">
              <td>{act}</td>
              <td>{n(l.words)}</td>
              <td>{n(l.target)}</td>
              <td>
                {l.ratio != null ? `${Math.round(l.ratio * 100)}%` : "—"}{" "}
                <Badge variant={l.within_tolerance ? "success" : "warning"}>{l.within_tolerance ? "on target" : "off target"}</Badge>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

function BoundedLengthCorrection({
  report, final,
}: { report: NonNullable<PipelineLanguage["length_correction"]>; final: PipelineStage | undefined }) {
  const changed = final?.length_corrected ? final.acts_changed : (report.acts_changed ?? []);
  return (
    <div className="rounded-md border border-border p-2 text-xs" data-testid="length-correction">
      <div className="flex flex-wrap items-center gap-1">
        <b>Bounded length correction</b>
        <Badge variant={report.status === "stored" ? "success" : report.status === "rejected" ? "danger" : "warning"}>
          {report.status}
        </Badge>
        <span className="text-muted-foreground">(one call at most, after the final, for acts that are too short)</span>
      </div>
      {report.reason && <p className="mt-1">{report.reason}</p>}
      {report.shortfalls && report.shortfalls.length > 0 && (
        <table className="mt-1 w-full">
          <thead>
            <tr className="text-left text-muted-foreground">
              <th>Act</th><th>Words</th><th>Target</th><th>Short by</th><th>Draft has</th><th>Sent</th>
            </tr>
          </thead>
          <tbody>
            {report.shortfalls.map((a) => (
              <tr key={a.act_id} className="border-t border-border">
                <td>{a.act_id}</td><td>{n(a.current_words)}</td><td>{n(a.target_words)}</td>
                <td>{n(a.shortfall_words)}</td><td>{n(a.master_words)}</td><td>{a.qualifies ? "yes" : "no"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {changed.length > 0 && (
        <p className="mt-1" data-testid="length-corrected-acts">
          <Badge variant="warning">length-corrected, not inspected</Badge> Read these acts: {changed.join(", ")}
        </p>
      )}
      {report.repetition && Object.entries(report.repetition).map(([act, r]) => (
        <p key={act} className="mt-1 text-muted-foreground">
          {act}: repeated word sequences {(r.trigram_before * 100).toFixed(1)}% → {(r.trigram_after * 100).toFixed(1)}%
          {r.likely_padded && <> <Badge variant="warning">likely padded</Badge></>}
        </p>
      ))}
    </div>
  );
}

function Gates({ lang }: { lang: PipelineLanguage }) {
  const gates = lang.gates!;
  const reports = gates.reports ?? {};
  const orig = lang.originality as
    | { pass?: boolean; worst?: { longest_run?: number; ngram_share?: number }; near_verbatim_count?: number; transcripts_compared?: number }
    | null;
  return (
    <div className="rounded-md border border-border p-2 text-xs">
      <div className="flex flex-wrap items-center gap-1">
        <b>Gates</b>
        <Badge variant={gates.pass ? "success" : "danger"}>{gates.pass ? "all passed" : `failed: ${gates.failures.join(", ")}`}</Badge>
      </div>
      <div className="mt-1 flex flex-wrap gap-1">
        {Object.entries(reports).map(([name, r]) => (
          <Badge key={name} variant={(r as { pass?: boolean }).pass === false ? "danger" : "success"}>{name}</Badge>
        ))}
      </div>
      {orig && (
        <p className="mt-1 text-muted-foreground">
          Originality: {orig.pass ? "passed" : "failed"} · longest shared run {orig.worst?.longest_run ?? 0} words ·{" "}
          {((orig.worst?.ngram_share ?? 0) * 100).toFixed(1)}% of word sequences · {orig.near_verbatim_count ?? 0} near-verbatim
          sentences · {orig.transcripts_compared ?? 0} transcript wordings compared
        </p>
      )}
      {gates.details.map((d) => (
        <div key={d.gate} className="mt-1">
          <b>{d.gate}</b>: {d.problem}
          {d.passages.slice(0, 3).map((p, i) => (
            <p key={i} dir="auto" className="ml-2 text-muted-foreground">• {p}</p>
          ))}
        </div>
      ))}
      {lang.gate_history && lang.gate_history.length > 1 && (
        <p className="mt-1 text-muted-foreground">Stage 4 ran {lang.gate_history.length} times (gate repairs).</p>
      )}
    </div>
  );
}

function Glossary({ caseId }: { caseId: number }) {
  const { data: glossary, error, refetch } = useApi<GlossaryView>(() => api.glossary(caseId), [caseId], {
    keepPrevious: true,
  });
  const [view, setView] = useState<GlossaryView | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const shown = view ?? glossary;

  async function act(label: string, work: () => Promise<GlossaryView | null>) {
    setBusy(label);
    setProblem(null);
    try {
      const next = await work();
      if (next) setView(next);
      else refetch();
    } catch (e) {
      setProblem(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2">
          Names and places glossary
          {shown?.languages.map((l) => (
            <Badge key={l} variant={shown.approved[l] ? "success" : "warning"}>
              {langLabel(l)} {shown.approved[l] ? "approved" : "not approved"}
            </Badge>
          ))}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 text-xs">
        <p className="text-muted-foreground">
          How each person and place of the evidence is spelled in each language. The facts check requires every
          name of an evidence item a text tells in one of the approved spellings; separate several spellings with
          “ | ”. Editing an entry makes it a proposal again until you approve the language.
        </p>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => act("propose", () => api.proposeGlossary(caseId))}>
            {busy === "propose" ? "Proposing…" : "Propose spellings"}
          </Button>
          {shown?.languages.map((l) => (
            <Button key={l} size="sm" disabled={busy !== null || shown.approved[l]} onClick={() => act(`approve-${l}`, () => api.approveGlossary(caseId, l))}>
              <Check className="mr-1 h-3 w-3" /> Approve {langLabel(l)}
            </Button>
          ))}
        </div>
        {(problem || error) && <ErrorState message={problem ?? String(error)} />}
        {shown && shown.entities.length === 0 && <p>No glossary yet. “Propose spellings” creates it from the evidence.</p>}
        {shown && shown.entities.length > 0 && (
          <div className="max-h-96 overflow-auto">
            <table className="w-full">
              <thead>
                <tr className="text-left">
                  <th className="pr-2">Entity</th>
                  {shown.languages.map((l) => <th key={l} className="pr-2">{langLabel(l)}</th>)}
                </tr>
              </thead>
              <tbody>
                {shown.entities.map((e) => (
                  <tr key={e.entity} className="align-top">
                    <td className="pr-2">
                      {e.entity} <span className="text-muted-foreground">({e.kind}, {e.evidence_ids.length})</span>
                    </td>
                    {shown.languages.map((l) => (
                      <td key={l} className="pr-2">
                        <input
                          dir="auto"
                          aria-label={`${e.entity} in ${l}`}
                          className="w-full rounded border bg-background px-1 py-0.5"
                          defaultValue={(e.languages[l]?.spellings ?? []).join(" | ")}
                          key={`${e.entity}-${l}-${(e.languages[l]?.spellings ?? []).join("|")}-${e.languages[l]?.status}`}
                          onBlur={(ev) => {
                            const next = ev.target.value.split("|").map((x) => x.trim()).filter(Boolean);
                            if (next.length === 0 || next.join(" | ") === (e.languages[l]?.spellings ?? []).join(" | ")) return;
                            act(`edit-${e.entity}-${l}`, () => api.editGlossary(caseId, l, e.entity, next));
                          }}
                        />
                        <Badge variant={e.languages[l]?.status === "approved" ? "success" : "default"}>
                          {e.languages[l]?.status ?? "—"}
                        </Badge>
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function CostTable({ state }: { state: LanguagePipelineState }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Cost per stage and language (actual ledger)</CardTitle>
      </CardHeader>
      <CardContent>
        {state.ledger.length === 0 ? (
          <p className="text-xs text-muted-foreground">Nothing spent yet.</p>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-muted-foreground">
                <th className="py-1">Stage</th><th>Lang</th><th>Calls</th><th>In</th><th>Cached</th>
                <th>Reasoning</th><th>Out</th><th>Caching saved</th><th>€</th>
              </tr>
            </thead>
            <tbody>
              {state.ledger.map((r) => (
                <tr key={`${r.stage}:${r.language}`} className="border-t border-border">
                  <td className="py-1">{r.stage.replace("language_", "")}</td>
                  <td>{r.language ?? "shared"}</td>
                  <td>{r.calls}</td>
                  <td>{n(r.tokens_in)}</td>
                  <td>{n(r.tokens_cached)} ({Math.round(r.cache_share * 100)}%)</td>
                  <td>{n(r.tokens_reasoning)}</td>
                  <td>{n(r.tokens_out)}</td>
                  <td>{eur(r.cache_saving_eur)}</td>
                  <td>{eur(r.eur)}</td>
                </tr>
              ))}
              <tr className="border-t border-border font-medium">
                <td className="py-1" colSpan={2}>Total (draft counted once)</td>
                <td>{state.calls_total}</td><td colSpan={4} /><td>{eur(state.ledger.reduce((a, r) => a + r.cache_saving_eur, 0))}</td><td>{eur(state.cost_total_eur)}</td>
              </tr>
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  );
}
