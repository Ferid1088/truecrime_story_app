"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { BookOpen, ChevronDown, ChevronRight, Layers } from "lucide-react";
import { api, ApiError } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import type { StoryFull, StoryMeta } from "@/lib/types";
import { estimateMinutes, parseNarrativePlan } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorState } from "@/components/state";
import { Sheet } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { StoryReader } from "@/components/story/reader";
import { Localizations } from "@/components/story/localizations";
import { cn } from "@/lib/utils";

export function LocalizationsTab({ caseId }: { caseId: number }) {
  const router = useRouter();
  const { data, error, loading, refetch } = useApi(
    () => api.getMasterStory(caseId).catch((e) => (e instanceof ApiError && e.status === 404 ? null : Promise.reject(e))),
    [caseId],
  );
  const [openStory, setOpenStory] = useState<StoryFull | null>(null);

  if (loading) return <Skeleton className="h-72" />;
  if (error) return <ErrorState message={error} onRetry={refetch} />;

  const openVersion = (v: StoryMeta) => {
    const load =
      v.kind === "localized" ? api.getLocalization(v.id) : api.getStoryVersion(caseId, v.id);
    load.then(setOpenStory).catch(() => {});
  };

  if (!data) {
    return (
      <EmptyState
        title="No English Master yet"
        description="The canonical English master story is required before any localization can be generated."
        action={
          <Button size="sm" onClick={() => router.push(`/studio?case=${caseId}`)}>
            Open Story Studio
          </Button>
        }
      />
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_300px]">
      <MasterCard master={data.master} />
      <Localizations caseId={caseId} masterReady={data.master.status === "ready"} onOpen={openVersion} />

      <Sheet
        open={!!openStory}
        onClose={() => setOpenStory(null)}
        title={openStory ? `Story v${openStory.version} · ${openStory.language.toUpperCase()}` : ""}
        className="max-w-2xl"
      >
        {openStory && <StoryReader story={openStory} />}
      </Sheet>
    </div>
  );
}

function MasterCard({ master }: { master: StoryFull }) {
  const [showStructure, setShowStructure] = useState(false);
  const [showProblems, setShowProblems] = useState(master.status === "needs_revision");
  const plan = parseNarrativePlan(master.narrative_angle);
  const acts = master.narrative_structure?.acts ?? [];
  const evidenceUsage = (master.narrative_structure as Record<string, unknown> | null)
    ?.evidence_usage as { facts_used?: number; facts_total?: number } | undefined;

  return (
    <Card className="h-fit">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <BookOpen className="size-4" /> English Master
        </CardTitle>
        <Badge variant={master.status === "ready" ? "success" : master.status === "needs_revision" ? "warning" : "outline"}>
          {master.status.replace("_", " ")}
        </Badge>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-4">
          <Metric label="Version" value={`v${master.version}${master.is_best ? " · best" : ""}`} />
          <Metric label="Words" value={master.word_count.toLocaleString()} />
          <Metric label="Duration" value={`≈${estimateMinutes(master.word_count)} min`} />
          <Metric label="Engagement" value={String(Math.round(master.engagement_score))} />
        </div>
        {master.generation_model && (
          <p className="text-xs text-muted-foreground">
            Model: <span className="font-mono text-foreground/80">{master.generation_model}</span>
            {master.generation_provider ? ` (${master.generation_provider})` : ""}
          </p>
        )}
        {plan?.title && (
          <p className="text-xs font-medium text-foreground" dir="auto">{plan.title}</p>
        )}
        {plan?.central_question && (
          <p className="text-xs leading-5 text-muted-foreground" dir="auto">
            {plan.central_question}
          </p>
        )}

        {master.status === "needs_revision" && master.problems.length > 0 && (
          <div className="rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2">
            <button
              className="flex w-full cursor-pointer items-center gap-1.5 text-xs font-medium text-amber-600 dark:text-amber-400"
              onClick={() => setShowProblems((v) => !v)}
            >
              {showProblems ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
              {master.problems.length} quality gate failure{master.problems.length === 1 ? "" : "s"}
            </button>
            {showProblems && (
              <ul className="mt-1.5 list-disc space-y-1 pl-4 text-xs leading-5 text-foreground/80">
                {master.problems.map((p, i) => (
                  <li key={i}>{p}</li>
                ))}
              </ul>
            )}
          </div>
        )}

        {acts.length > 0 && (
          <div>
            <button
              className="flex cursor-pointer items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground"
              onClick={() => setShowStructure((v) => !v)}
            >
              {showStructure ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
              <Layers className="size-3.5" /> Structure ({acts.length} acts)
            </button>
            {showStructure && (
              <ol className="mt-2 space-y-1.5">
                {acts.map((a, i) => (
                  <li key={a.id ?? i} className="rounded-md border border-border px-2.5 py-1.5 text-xs">
                    <p className="font-medium">
                      {a.title ?? a.id ?? `Act ${i + 1}`}
                      {a.word_target ? (
                        <span className="ml-2 font-normal text-muted-foreground tabular-nums">
                          ~{a.word_target} words
                        </span>
                      ) : null}
                    </p>
                    {a.purpose && (
                      <p className="mt-0.5 leading-4 text-muted-foreground">{a.purpose}</p>
                    )}
                  </li>
                ))}
              </ol>
            )}
          </div>
        )}

        {evidenceUsage && (
          <p className="text-xs text-muted-foreground">
            Evidence usage:{" "}
            <span className="font-medium text-foreground tabular-nums">
              {evidenceUsage.facts_used ?? "?"}/{evidenceUsage.facts_total ?? "?"} facts
            </span>
          </p>
        )}
      </CardContent>
    </Card>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div className={cn("rounded-md border border-border px-2 py-1.5")}>
      <p className="text-sm font-semibold tabular-nums leading-none">{value}</p>
      <p className="mt-1 text-[10px] text-muted-foreground">{label}</p>
    </div>
  );
}
