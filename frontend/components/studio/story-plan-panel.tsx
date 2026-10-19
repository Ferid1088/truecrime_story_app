"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { langLabel } from "@/lib/format";
import type { StoryPlan, StoryPlanPreview } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

/** The plan the master is written from: plan, look, redo with a prompt (after a
 *  preview of its exact scope), approve. The writer does not run without it. */
export function StoryPlanPanel({
  caseId,
  language,
  minutes,
  onChanged,
}: {
  caseId: number;
  language: string;
  minutes: number;
  onChanged?: (plan: StoryPlan | null) => void;
}) {
  const [tick, setTick] = useState(0);
  const { data: plan } = useApi<StoryPlan | null>(
    () => api.storyPlan(caseId, language).catch(() => null),
    [caseId, language, tick],
  );
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [instruction, setInstruction] = useState("");
  const [preview, setPreview] = useState<StoryPlanPreview | null>(null);

  async function run(label: string, fn: () => Promise<StoryPlan | StoryPlanPreview | void>) {
    setBusy(label);
    setError(null);
    try {
      const out = await fn();
      if (label === "preview") setPreview(out as StoryPlanPreview);
      else {
        setPreview(null);
        setInstruction("");
        setTick((t) => t + 1);
        onChanged?.((out as StoryPlan) ?? null);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  const mismatch = plan && (plan.target_minutes !== minutes || plan.language !== language);
  return (
    <Card data-testid="story-plan">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          Story plan
          {plan && (
            <Badge variant={plan.status === "approved" ? "success" : "warning"}>
              v{plan.version} · {plan.status}
            </Badge>
          )}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3 p-3 text-xs">
        {!plan && (
          <p className="text-muted-foreground">
            No plan yet. The writer needs an approved plan for this language, length and evidence.
          </p>
        )}
        {plan && (
          <>
            <p>
              <span className="font-medium">{plan.title ?? "Untitled"}</span> — {plan.central_question}
            </p>
            <p className="text-muted-foreground">
              {langLabel(plan.language)} · {plan.target_minutes} min · opening: {plan.opening_strategy}
              {mismatch && " · this plan is for another length or language"}
            </p>
            <ol className="space-y-1.5">
              {plan.beats.map((b, i) => (
                <li key={b.id} className="rounded-md border border-border px-2.5 py-1.5">
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="font-medium">
                      {i + 1}. {b.title}
                    </span>
                    <Badge variant="outline">{b.target_words} words</Badge>
                  </div>
                  <p className="text-muted-foreground">{b.purpose}</p>
                  {b.introduces_people.length > 0 && (
                    <p className="text-muted-foreground">introduces: {b.introduces_people.join(", ")}</p>
                  )}
                  <p className="text-muted-foreground">
                    evidence {b.evidence_ids.length} · reveals {b.reveals.length}
                  </p>
                </li>
              ))}
            </ol>
            {plan.validation.errors.length > 0 && (
              <p className="text-rose-600 dark:text-rose-400">
                Invalid: {plan.validation.errors.map((e) => e.code.replace(/_/g, " ")).join("; ")}
              </p>
            )}
            {plan.validation.warnings.length > 0 && (
              <p className="text-amber-600 dark:text-amber-400">
                Check: {plan.validation.warnings.map((e) => e.code.replace(/_/g, " ")).join("; ")}
              </p>
            )}
            {plan.status === "approved" && (
              <p className="text-muted-foreground">Approved by {plan.approved_by}.</p>
            )}
            <div className="space-y-1.5">
              <input
                className="w-full rounded-md border border-border bg-background px-2 py-1.5 text-xs"
                placeholder="Redo the plan with a prompt, e.g. open on the trial"
                value={instruction}
                onChange={(e) => {
                  setInstruction(e.target.value);
                  setPreview(null);
                }}
                aria-label="Plan redo prompt"
              />
              {preview && (
                <p className="rounded-md border border-border bg-muted/40 px-2 py-1.5">
                  Scope: replaces plan v{plan.version}
                  {preview.approval_is_lost && " and its approval"}; {preview.director_calls} director call;{" "}
                  {preview.masters_written_from_this_plan.length} master version(s) were written from it and are
                  kept.
                </p>
              )}
              <div className="flex gap-2">
                {!preview ? (
                  <Button
                    size="sm"
                    variant="secondary"
                    disabled={busy !== null || !instruction.trim() || plan.status === "superseded"}
                    loading={busy === "preview"}
                    onClick={() => run("preview", () => api.previewStoryPlan(plan.id, { action: "redo", instruction }))}
                  >
                    Preview redo
                  </Button>
                ) : (
                  <Button
                    size="sm"
                    variant="secondary"
                    loading={busy === "redo"}
                    onClick={() => run("redo", () => api.redoStoryPlan(plan.id, { token: preview.token, instruction }))}
                  >
                    Apply redo
                  </Button>
                )}
                <Button
                  size="sm"
                  disabled={busy !== null || plan.status !== "draft" || plan.validation.status === "invalid"}
                  loading={busy === "approve"}
                  onClick={() => run("approve", () => api.approveStoryPlan(plan.id, "operator"))}
                >
                  Approve plan
                </Button>
              </div>
            </div>
          </>
        )}
        {(!plan || plan.status === "superseded" || mismatch) && (
          <Button
            size="sm"
            disabled={busy !== null}
            loading={busy === "create"}
            onClick={() => run("create", () => api.createStoryPlan(caseId, { target_minutes: minutes, language }))}
          >
            Plan the story
          </Button>
        )}
        {error && <p className="text-rose-600 dark:text-rose-400">{error}</p>}
      </CardContent>
    </Card>
  );
}
