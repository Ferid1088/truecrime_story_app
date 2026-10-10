"use client";

import { Check, CircleDashed, Loader2, X } from "lucide-react";
import type { CaseDetail } from "@/lib/types";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatDate, langLabel } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type StageState = "waiting" | "running" | "complete" | "failed";

function stageState(done: boolean, active: boolean, failed = false): StageState {
  if (failed) return "failed";
  if (active) return "running";
  return done ? "complete" : "waiting";
}

export function OverviewTab({ caseData }: { caseData: CaseDetail }) {
  const researched = ["researched", "writing", "story_ready", "producing", "rendered", "published", "completed"].includes(caseData.status);
  const hasStory = caseData.story_versions > 0;

  const stages: { label: string; state: StageState }[] = [
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

  const done = stages.filter((s) => s.state === "complete").length;

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      <div className="space-y-4 lg:col-span-2">
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
