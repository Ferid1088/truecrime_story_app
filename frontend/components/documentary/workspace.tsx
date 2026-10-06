"use client";

import { useCallback, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ArrowLeft, FolderOpen, RefreshCw } from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { TabBar, tabId, tabPanelId } from "@/components/ui/tabs";
import { ErrorState } from "@/components/state";
import { CaseStatusBadge } from "@/components/status-badge";
import { useJobPoller } from "./hooks";
import { InlineAlert, isJobActive, productionLanguages, stageLabel } from "./shared";
import { RunTab } from "./tabs/run";
import { BlueprintTab } from "./tabs/blueprint";
import { LanguagesTab } from "./tabs/languages";
import { VoiceTab } from "./tabs/voice";
import { VisualLibraryTab } from "./tabs/visual-library";
import { TimelineTab } from "./tabs/timeline";
import { MusicTab } from "./tabs/music";
import { ProductionScriptTab } from "./tabs/production-script";
import { CritiqueTab } from "./tabs/critique";
import { RenderTab } from "./tabs/render";

const TABS = [
  "Run",
  "Blueprint",
  "Language Versions",
  "Voice",
  "Visual Library",
  "Timeline",
  "Music & Sound",
  "Production Script",
  "Critique",
  "Render",
] as const;

type Tab = (typeof TABS)[number];

const ID_PREFIX = "documentary";

export function DocumentaryWorkspace({ caseId }: { caseId: number }) {
  const router = useRouter();
  const [tab, setTab] = useState<Tab>("Run");
  const [tick, setTick] = useState(0);
  const [masterChoice, setMasterChoice] = useState<number | null>(null);
  const [languageChoice, setLanguageChoice] = useState<string | null>(null);
  const refresh = useCallback(() => setTick((t) => t + 1), []);

  const caseState = useApi(() => api.getCase(caseId), [caseId]);
  const settingsState = useApi(() => api.documentarySettings(), []);
  const overviewState = useApi(
    () => api.documentaryOverview(caseId, masterChoice),
    [caseId, masterChoice, tick],
    { keepPrevious: true },
  );
  const overview = overviewState.data;
  const { job, track, pollError } = useJobPoller(overview?.jobs[0] ?? null, refresh);

  const caseData = caseState.data;
  const settings = settingsState.data;
  const fatal = caseState.error ?? settingsState.error ?? (overview ? null : overviewState.error);

  if (fatal) {
    return (
      <ErrorState
        message={fatal}
        onRetry={() => {
          caseState.refetch();
          settingsState.refetch();
          overviewState.refetch();
        }}
      />
    );
  }
  if (!caseData || !settings || !overview) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-2/3" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  const masterId = masterChoice ?? overview.master_version_id;
  const language =
    languageChoice ?? productionLanguages(settings.languages, overview)[0] ?? settings.languages[0] ?? "en";
  const common = { caseId, settings, overview, refreshKey: tick };
  const languageProps = { ...common, language, onLanguageChange: setLanguageChoice, masterId };

  return (
    <div>
      <div className="mb-1">
        <Link
          href="/documentary"
          className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
        >
          <ArrowLeft className="size-3" /> All documentaries
        </Link>
      </div>

      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight">{caseData.title}</h1>
          <div className="mt-1.5 flex flex-wrap items-center gap-2">
            <Badge variant="accent">Documentary</Badge>
            <CaseStatusBadge status={caseData.status} />
            <span className="text-xs text-muted-foreground">
              Case #{caseData.id} · films run {settings.film_minutes.min}–{settings.film_minutes.max} min
            </span>
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          <Button variant="ghost" size="sm" onClick={refresh} loading={overviewState.loading}>
            {!overviewState.loading && <RefreshCw className="size-3.5" />} Refresh
          </Button>
          <Button variant="secondary" size="sm" onClick={() => router.push(`/cases/${caseId}`)}>
            <FolderOpen className="size-3.5" /> Case workspace
          </Button>
        </div>
      </div>

      {job && isJobActive(job) && tab !== "Run" && (
        <div
          role="status"
          className="mb-4 flex flex-wrap items-center gap-2 rounded-md border border-border bg-muted/40 px-4 py-3 text-sm text-muted-foreground"
        >
          <span className="size-2 animate-pulse rounded-full bg-amber-500" aria-hidden />
          Job #{job.id} {job.status}
          {job.stage && job.status === "running" && ` — ${stageLabel(job.stage)}`} · {Math.round(job.progress * 100)}%
          <Button variant="link" size="sm" className="h-auto" onClick={() => setTab("Run")}>
            Show progress
          </Button>
        </div>
      )}
      {pollError && (
        <InlineAlert tone="warning" className="mb-4">
          Lost contact with the API while following job #{job?.id} — retrying every few seconds. ({pollError})
        </InlineAlert>
      )}
      {overviewState.error && (
        <InlineAlert tone="error" className="mb-4">
          Could not refresh the production overview: {overviewState.error}{" "}
          <button type="button" onClick={refresh} className="cursor-pointer font-medium underline">
            Retry
          </button>
        </InlineAlert>
      )}

      <TabBar tabs={TABS} value={tab} onChange={setTab} idPrefix={ID_PREFIX} label="Documentary production" />

      <div role="tabpanel" id={tabPanelId(ID_PREFIX)} aria-labelledby={tabId(ID_PREFIX, TABS.indexOf(tab))}>
        {tab === "Run" && (
          <RunTab
            caseId={caseId}
            settings={settings}
            overview={overview}
            masterId={masterId}
            job={job}
            onMasterChange={setMasterChoice}
            onJobChange={track}
            onRefresh={refresh}
          />
        )}
        {tab === "Blueprint" && <BlueprintTab {...common} />}
        {tab === "Language Versions" && <LanguagesTab {...common} />}
        {tab === "Voice" && <VoiceTab {...languageProps} />}
        {tab === "Visual Library" && <VisualLibraryTab {...common} />}
        {tab === "Timeline" && <TimelineTab {...languageProps} />}
        {tab === "Music & Sound" && <MusicTab {...languageProps} />}
        {tab === "Production Script" && <ProductionScriptTab {...languageProps} />}
        {tab === "Critique" && <CritiqueTab {...languageProps} />}
        {tab === "Render" && <RenderTab {...common} job={job} />}
      </div>
    </div>
  );
}
