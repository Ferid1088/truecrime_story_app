"use client";

import { Check, CircleDashed, Loader2, X } from "lucide-react";
import type { CaseDetail, DocumentaryJob } from "@/lib/types";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatDate, langLabel } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { pipelineStageSource } from "@/components/documentary/stage-control";

type StageState = "waiting" | "running" | "complete" | "failed";

function stageState(done: boolean, active: boolean, failed = false): StageState {
  if (failed) return "failed";
  if (active) return "running";
  return done ? "complete" : "waiting";
}

export function OverviewTab({ caseData, jobs = [] }: { caseData: CaseDetail; jobs?: DocumentaryJob[] }) {
  const researched = ["researched", "writing", "story_ready", "producing", "rendered", "published", "completed"].includes(caseData.status);
  const hasStory = caseData.story_versions > 0;

  const legacyStages: { label: string; state: StageState }[] = [
    { label: "Discovery", state: "complete" },
    { label: "Sources", state: stageState(caseData.sources > 0, false) },
    {
      label: "Research",
      state: stageState(researched, caseData.status === "researching"),
    },
    { label: "Facts", state: stageState(caseData.facts > 0, false) },
    {
      label: "Contradictions",
      state: stageState(researched, caseData.status === "researching"),
    },
    {
      label: "Story Direction",
      state: stageState(!!caseData.narrative_angle, caseData.status === "writing"),
    },
    { label: "Writing", state: stageState(hasStory, caseData.status === "writing") },
    {
      label: "Critique",
      state: stageState(caseData.engagement_score != null, caseData.status === "writing"),
    },
    {
      label: "Final Story",
      state: stageState(["completed", "story_ready", "producing", "rendered", "published"].includes(caseData.status), false),
    },
  ];
  const current = jobs[0];
  const pipelineSource = current?.stages?.length ? pipelineStageSource(current) : null;
  const stages: { label: string; state: StageState }[] = pipelineSource?.length
    ? pipelineSource.map((stage) => ({
        label: stage.name.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()),
        state: stageState(stage.status === "done", stage.status === "running", stage.status === "failed"),
      }))
    : legacyStages;

  const done = stages.filter((s) => s.state === "complete").length;

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      <div className="space-y-4 lg:col-span-2">
        <BudgetPanel caseId={caseData.id} />
        <Card>
          <CardHeader>
            <CardTitle>Case Summary</CardTitle>
          </CardHeader>
          <CardContent>
            {caseData.summary ? (
              <p className="text-sm leading-6 text-foreground/90" dir="auto">
                {caseData.summary}
              </p>
            ) : (
              <p className="text-sm text-muted-foreground">No summary recorded yet.</p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Research</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <Stat label="Sources" value={caseData.sources} />
              <Stat label="Facts" value={caseData.facts} />
              <Stat label="Contradictions" value={caseData.contradictions} />
              <Stat label="Story versions" value={caseData.story_versions} />
            </div>
            <div className="mt-4 border-t border-border pt-3">
              <p className="mb-1.5 text-xs font-medium text-muted-foreground">Languages found in sources</p>
              {caseData.languages_found.length ? (
                <div className="flex flex-wrap gap-1.5">
                  {caseData.languages_found.map((l) => (
                    <Badge key={l} variant="outline">
                      {langLabel(l)}
                    </Badge>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">None yet.</p>
              )}
            </div>
            <ResearchLanguages caseId={caseData.id} />
            <div className="mt-3 grid grid-cols-2 gap-3 text-sm">
              <div>
                <p className="text-xs text-muted-foreground">Created</p>
                <p>{formatDate(caseData.created_at)}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Latest engagement score</p>
                <p>{caseData.engagement_score != null ? Math.round(caseData.engagement_score) : "—"}</p>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card className="h-fit">
        <CardHeader>
          <CardTitle>Pipeline</CardTitle>
          <span className="text-xs text-muted-foreground tabular-nums">
            {done}/{stages.length}
          </span>
        </CardHeader>
        <CardContent className="p-0">
          <ol className="divide-y divide-border">
            {stages.map((s) => (
              <li key={s.label} className="flex items-center justify-between px-4 py-2">
                <span className="text-sm">{s.label}</span>
                <StageIcon state={s.state} />
              </li>
            ))}
          </ol>
        </CardContent>
      </Card>
    </div>
  );
}

function BudgetPanel({ caseId }: { caseId: number }) {
  const { data, loading, refetch } = useApi(() => api.caseCosts(caseId), [caseId]);
  if (loading && !data) return <Card><CardContent className="p-4 text-sm text-muted-foreground">Loading cost panel…</CardContent></Card>;
  if (!data) return null;
  const stopped = data.status === "budget stopped" || Boolean(data.stop_reason);
  return (
    <Card data-testid="case-budget-panel">
      <CardHeader><CardTitle>Case budget</CardTitle></CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div className="grid grid-cols-3 gap-2 text-center">
          <Money label="Cap" value={data.cap_eur} />
          <Money label="Spent" value={data.spent_eur} />
          <Money label="Remaining" value={data.remaining_eur} />
        </div>
        {stopped && <p className="rounded-md border border-amber-500/40 bg-amber-500/10 p-2 text-amber-700">{data.stop_reason || "The budget stopped this case."}</p>}
        <div className="flex flex-wrap gap-2">
          {stopped && <Button size="sm" onClick={async () => { await api.setCaseBudget(caseId, data.cap_eur); refetch(); }}>Continue</Button>}
          <Button size="sm" variant="outline" onClick={async () => { await api.setCaseBudget(caseId, data.cap_eur + 20); refetch(); }}>Raise cap by €20</Button>
        </div>
        {data.calls.length > 0 && <p className="text-xs text-muted-foreground">{data.calls.length} provider calls recorded.</p>}
      </CardContent>
    </Card>
  );
}

function Money({ label, value }: { label: string; value: number }) {
  return <div className="rounded-md border border-border p-2"><p className="font-semibold tabular-nums">€{value.toFixed(2)}</p><p className="text-xs text-muted-foreground">{label}</p></div>;
}

function ResearchLanguages({ caseId }: { caseId: number }) {
  const { data } = useApi(() => api.researchLanguages(caseId), [caseId]);
  const { data: capacity } = useApi(
    () => api.researchCapacity(caseId),
    [caseId],
  );
  if (!data) return null;
  const entries = Object.entries(data.languages).filter(
    ([, v]) => v.searched || v.sources > 0,
  );
  if (!entries.length) return null;
  return (
    <div className="mt-4 border-t border-border pt-3">
      <p className="mb-1.5 text-xs font-medium text-muted-foreground">
        Research languages
      </p>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        {entries.map(([lang, v]) => (
          <div
            key={lang}
            className="rounded-md border border-border px-3 py-2 text-xs"
          >
            <div className="mb-1 flex items-center justify-between">
              <span className="font-medium">{langLabel(lang)}</span>
              <Badge variant="outline">{v.sources} sources</Badge>
            </div>
            <p className="text-muted-foreground">
              {v.evidence_items} evidence items
              {v.unique_evidence > 0 && ` · ${v.unique_evidence} unique`}
              {v.full_text_sources > 0 && ` · ${v.full_text_sources} full-text`}
            </p>
            {!v.sources && v.queries.length > 0 && (
              <p className="mt-0.5 text-muted-foreground">
                searched, no sources found
              </p>
            )}
          </div>
        ))}
      </div>
      {capacity && (
        <p className="mt-2 text-xs text-muted-foreground">
          Evidence supports ~{capacity.estimated_supported_minutes} min of
          narration
          {capacity.status === "insufficient_for_requested_length" &&
            ` — more research recommended for a ${capacity.requested_minutes}-min Master`}
        </p>
      )}
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border border-border p-3">
      <p className="text-lg font-semibold tabular-nums leading-none">{value}</p>
      <p className="mt-1 text-xs text-muted-foreground">{label}</p>
    </div>
  );
}

function StageIcon({ state }: { state: StageState }) {
  if (state === "complete") return <Check className="size-4 text-emerald-500" />;
  if (state === "running") return <Loader2 className="size-4 animate-spin text-amber-500" />;
  if (state === "failed") return <X className="size-4 text-rose-500" />;
  return <CircleDashed className="size-4 text-muted-foreground" />;
}
