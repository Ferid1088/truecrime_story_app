"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Archive, ArrowLeft, Clapperboard, FlaskConical, PenLine } from "lucide-react";
import { api, pollResearchJob } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Button } from "@/components/ui/button";
import { CaseStatusBadge } from "@/components/status-badge";
import { ErrorState } from "@/components/state";
import { Dialog } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";
import { OverviewTab } from "./tabs/overview";
import { SourcesTab } from "./tabs/sources";
import { FactsTab } from "./tabs/facts";
import { TimelineTab } from "./tabs/timeline";
import { ContradictionsTab } from "./tabs/contradictions";
import { StoryTab } from "./tabs/story";
import { AgentRunsTab } from "./tabs/agent-runs";
import { ResearchLanguagesTab } from "./tabs/research-languages";
import { LocalizationsTab } from "./tabs/localizations";
import { VideosTab } from "./tabs/videos";
import { CorpusSearchTab } from "./tabs/corpus-search";

const TABS = [
  "Overview",
  "Sources",
  "Corpus Search",
  "Facts",
  "Timeline",
  "Contradictions",
  "Story",
  "Videos",
  "Research Languages",
  "Localizations",
  "Agent Runs",
] as const;

type Tab = (typeof TABS)[number];

export function CaseWorkspace({ caseId }: { caseId: number }) {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("Overview");
  const [busy, setBusy] = useState<"research" | "archive" | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [researchTick, setResearchTick] = useState(0);
  const [jobStage, setJobStage] = useState<string | null>(null);
  const [confirmArchive, setConfirmArchive] = useState(false);
  const pollAbort = useRef<AbortController | null>(null);

  const { data: caseData, error, loading, refetch } = useApi(() => api.getCase(caseId), [caseId, researchTick]);

  useEffect(() => () => pollAbort.current?.abort(), []);

  async function runResearch() {
    setBusy("research");
    setActionError(null);
    setJobStage("Starting research job…");
    pollAbort.current?.abort();
    const ctl = new AbortController();
    pollAbort.current = ctl;
    try {
      const res = await api.runResearch(caseId);
      if (res.job_id != null) {
        const job = await pollResearchJob(res.job_id, {
          signal: ctl.signal,
          onUpdate: (j) =>
            setJobStage(
              j.status === "queued"
                ? "Research job queued…"
                : "Research running — searching sources in all configured languages…",
            ),
        });
        if (job.status === "failed") {
          throw new Error(job.error || "Research provider unavailable");
        }
      }
      setResearchTick((t) => t + 1);
      setTab("Sources");
    } catch (e) {
      if (!(e instanceof DOMException && e.name === "AbortError")) {
        setActionError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setBusy(null);
      setJobStage(null);
    }
  }

  async function archive() {
    setBusy("archive");
    setActionError(null);
    try {
      await api.updateCase(caseId, { status: "archived" });
      setConfirmArchive(false);
      refetch();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }

  if (loading)
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-2/3" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  if (error) return <ErrorState message={error} onRetry={refetch} />;
  if (!caseData) return null;

  return (
    <div>
      <div className="mb-1">
        <Link
          href="/cases"
          className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-3" /> All cases
        </Link>
      </div>

      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight">{caseData.title}</h1>
          <div className="mt-1.5 flex items-center gap-2">
            <CaseStatusBadge status={caseData.status} />
            <span className="text-xs text-muted-foreground">Case #{caseData.id}</span>
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <Button variant="secondary" size="sm" onClick={runResearch} loading={busy === "research"}>
            <FlaskConical className="size-3.5" /> Run Research
          </Button>
          <Button size="sm" onClick={() => router.push(`/studio?case=${caseId}`)}>
            <PenLine className="size-3.5" /> Generate Story
          </Button>
          <Button variant="secondary" size="sm" onClick={() => router.push(`/documentary/${caseId}`)}>
            <Clapperboard className="size-3.5" /> Documentary
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setConfirmArchive(true)}
            loading={busy === "archive"}
            disabled={caseData.status === "archived"}
          >
            <Archive className="size-3.5" /> Archive
          </Button>
        </div>
      </div>

      {jobStage && (
        <div className="mb-4 flex items-center gap-2 rounded-md border border-border bg-muted/40 px-4 py-3 text-sm text-muted-foreground">
          <span className="size-2 animate-pulse rounded-full bg-amber-500" />
          {jobStage}
        </div>
      )}

      {actionError && (
        <div className="mb-4 rounded-md border border-rose-500/30 bg-rose-500/5 px-4 py-3 text-sm text-rose-600 dark:text-rose-400">
          {actionError}
        </div>
      )}

      <div className="mb-5 flex gap-1 overflow-x-auto border-b border-border">
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={cn(
              "-mb-px cursor-pointer whitespace-nowrap border-b-2 px-3 py-2 text-sm transition-colors",
              tab === t
                ? "border-primary font-medium text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground",
            )}
          >
            {t}
          </button>
        ))}
      </div>

      {tab === "Overview" && <OverviewTab caseData={caseData} />}
      {tab === "Sources" && <SourcesTab caseId={caseId} refreshKey={researchTick} />}
      {tab === "Corpus Search" && <CorpusSearchTab caseId={caseId} />}
      {tab === "Facts" && <FactsTab caseId={caseId} refreshKey={researchTick} />}
      {tab === "Timeline" && <TimelineTab caseId={caseId} refreshKey={researchTick} />}
      {tab === "Contradictions" && <ContradictionsTab caseId={caseId} refreshKey={researchTick} />}
      {tab === "Story" && <StoryTab caseId={caseId} />}
      {tab === "Videos" && <VideosTab caseId={caseId} refreshKey={researchTick} />}
      {tab === "Research Languages" && <ResearchLanguagesTab caseId={caseId} refreshKey={researchTick} />}
      {tab === "Localizations" && <LocalizationsTab caseId={caseId} />}
      {tab === "Agent Runs" && <AgentRunsTab caseId={caseId} refreshKey={researchTick} />}

      <Dialog
        open={confirmArchive}
        onClose={() => setConfirmArchive(false)}
        title="Archive case?"
        description="The case is removed from active views. Its research and story data stay in the database."
      >
        <div className="flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => setConfirmArchive(false)}>
            Cancel
          </Button>
          <Button variant="destructive" size="sm" onClick={archive} loading={busy === "archive"}>
            Archive case
          </Button>
        </div>
      </Dialog>
    </div>
  );
}
