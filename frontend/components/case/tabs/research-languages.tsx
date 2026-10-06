"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight, SearchCheck, SearchX } from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { langLabel, isRtl } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorState } from "@/components/state";
import { Skeleton } from "@/components/ui/skeleton";
import type { ResearchDepth, ResearchLanguageStats } from "@/lib/types";
import { cn } from "@/lib/utils";

export function ResearchLanguagesTab({ caseId, refreshKey = 0 }: { caseId: number; refreshKey?: number }) {
  const { data, error, loading, refetch } = useApi(
    () => api.researchLanguages(caseId),
    [caseId, refreshKey],
  );
  const { data: depth } = useApi(() => api.researchDepth(caseId), [caseId, refreshKey]);
  const { data: jobs } = useApi(() => api.listResearchJobs(caseId), [caseId, refreshKey]);
  const latestJob = jobs?.find((j) => j.job_type === "research");

  if (loading)
    return (
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-36" />
        ))}
      </div>
    );
  if (error) return <ErrorState message={error} onRetry={refetch} />;
  if (!data || Object.keys(data.languages).length === 0)
    return (
      <EmptyState
        title="No research yet"
        description="Run multilingual research to see per-language coverage here."
      />
    );

  return (
    <div>
      {depth && <ResearchDepthPanel depth={depth} />}
      <div className="mb-4 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
        <span>
          Canonical language:{" "}
          <span className="font-medium text-foreground">{langLabel(data.canonical_language)}</span>
        </span>
        <span>
          Total sources:{" "}
          <span className="font-medium text-foreground tabular-nums">{data.total_sources}</span>
        </span>
        {latestJob?.result_summary && (
          <span className="w-full text-[11px] text-muted-foreground">
            Latest run: {latestJob.result_summary}
          </span>
        )}
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {Object.entries(data.languages).map(([lang, v]) => (
          <LanguageCard key={lang} lang={lang} stats={v} />
        ))}
      </div>
    </div>
  );
}

function LanguageCard({ lang, stats: v }: { lang: string; stats: ResearchLanguageStats }) {
  const [showQueries, setShowQueries] = useState(false);
  const searched = v.searched || v.sources > 0 || v.queries.length > 0;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          {langLabel(lang)}
          {lang !== "en" && <span className="font-mono text-[10px] uppercase text-muted-foreground">{lang}</span>}
        </CardTitle>
        {searched ? (
          v.sources > 0 ? (
            <Badge variant="success">
              <SearchCheck className="size-3" /> {v.sources} source{v.sources === 1 ? "" : "s"}
            </Badge>
          ) : (
            <Badge variant="warning">
              <SearchX className="size-3" /> no useful sources
            </Badge>
          )
        ) : (
          <Badge variant="outline">not searched</Badge>
        )}
      </CardHeader>
      <CardContent className="space-y-3 text-xs">
        <div className="grid grid-cols-3 gap-2">
          <Metric label="Evidence items" value={v.evidence_items} />
          <Metric label="Unique evidence" value={v.unique_evidence} />
          <Metric label="Families" value={v.source_families ?? 0} />
        </div>
        {(v.search_calls != null || v.sources_found != null || v.cost_usd != null) && (
          <div className="rounded-md border border-border bg-subtle px-2.5 py-2 text-[10px] text-muted-foreground">
            <p className="tabular-nums">
              {v.search_calls ?? 0} searches · {v.fetch_calls ?? 0} fetches ·{" "}
              {v.sources_found ?? 0} found / {v.sources_accepted ?? 0} accepted
              {v.rejected ? ` · ${v.rejected} rejected` : ""} · $
              {(v.cost_usd ?? 0).toFixed(3)}
            </p>
            {v.stop_reason && (
              <p className="mt-1 text-muted-foreground/70">stop: {v.stop_reason}</p>
            )}
          </div>
        )}
        <div className="flex flex-wrap gap-1.5">
          <DepthBadge label="full" count={v.full_text ?? 0} variant="success" />
          <DepthBadge label="partial" count={v.partial_text ?? 0} variant="warning" />
          <DepthBadge label="summary" count={v.summary_only ?? 0} variant="default" />
          <DepthBadge label="metadata" count={(v.metadata_only ?? 0) + (v.unavailable ?? 0)} variant="outline" />
          <span className="ml-auto text-[10px] text-muted-foreground tabular-nums">
            {v.chunks ?? 0} chunks
          </span>
        </div>

        {searched && v.sources === 0 && (
          <p className="text-muted-foreground">Searched — no useful sources found.</p>
        )}
        {!searched && (
          <p className="text-muted-foreground">This language was not covered by the last research run.</p>
        )}

        {v.queries.length > 0 && (
          <div>
            <button
              onClick={() => setShowQueries((s) => !s)}
              className="flex cursor-pointer items-center gap-1 text-muted-foreground hover:text-foreground"
            >
              {showQueries ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
              {v.queries.length} native quer{v.queries.length === 1 ? "y" : "ies"}
            </button>
            {showQueries && (
              <ul
                className={cn(
                  "mt-1.5 space-y-1 rounded-md border border-border bg-subtle p-2.5 text-foreground/80",
                  isRtl(lang) && "text-right",
                )}
                dir={isRtl(lang) ? "rtl" : "ltr"}
              >
                {v.queries.map((q, i) => (
                  <li key={i} className="leading-5">
                    {q}
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border border-border px-2 py-1.5">
      <p className="text-sm font-semibold tabular-nums leading-none">{value}</p>
      <p className="mt-1 text-[10px] text-muted-foreground">{label}</p>
    </div>
  );
}

function DepthBadge({
  label,
  count,
  variant,
}: {
  label: string;
  count: number;
  variant: "success" | "warning" | "default" | "outline";
}) {
  if (!count) return null;
  return (
    <Badge variant={variant}>
      {count} {label}
    </Badge>
  );
}

function evidenceBadge(e?: import("@/lib/types").EvidenceReadiness) {
  switch (e?.status) {
    case "ready":
      return { label: "Ready", variant: "success" as const };
    case "blocked_provider":
      return {
        label: "Blocked — provider authorization required",
        variant: "danger" as const,
      };
    case "failed":
      return { label: "Extraction failed", variant: "danger" as const };
    case "incomplete":
      return { label: "Incomplete", variant: "warning" as const };
    default:
      return { label: "Unknown", variant: "outline" as const };
  }
}

function masterBadge(m?: import("@/lib/types").MasterReadiness) {
  switch (m?.status) {
    case "ready":
      return { label: "Ready", variant: "success" as const };
    case "provider_blocked":
      return { label: "Blocked — provider", variant: "danger" as const };
    case "incomplete_evidence":
      return { label: "Blocked — evidence incomplete", variant: "warning" as const };
    case "insufficient_research":
      return { label: "Blocked — more research needed", variant: "warning" as const };
    case "failed":
      return { label: "Blocked — extraction failed", variant: "danger" as const };
    default:
      return { label: "Unknown", variant: "outline" as const };
  }
}

function ReadinessRow({
  label,
  badge,
  children,
}: {
  label: string;
  badge: { label: string; variant: "success" | "warning" | "danger" | "outline" | "default" };
  children?: React.ReactNode;
}) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div>
        <p className="font-medium">{label}</p>
        {children && (
          <p className="mt-0.5 text-muted-foreground">{children}</p>
        )}
      </div>
      <Badge variant={badge.variant}>{badge.label}</Badge>
    </div>
  );
}

function ResearchDepthPanel({ depth }: { depth: ResearchDepth }) {
  const cap = depth.capacity;
  const src = depth.source_readiness;
  const ev = depth.evidence_readiness;
  const mst = depth.master_readiness;
  const master = masterBadge(mst);
  return (
    <Card className="mb-4">
      <CardHeader>
        <CardTitle>Research Depth</CardTitle>
        <Badge variant={master.variant}>{master.label}</Badge>
      </CardHeader>
      <CardContent className="space-y-3 text-xs">
        <ReadinessRow
          label="Source Depth"
          badge={
            src?.status === "ready"
              ? { label: "Ready", variant: "success" }
              : { label: "Insufficient", variant: "warning" }
          }
        >
          {src
            ? `${src.retrieved_sources} retrieved · ${src.independent_source_families} families` +
              (src.reasons.length ? ` — ${src.reasons.join("; ")}` : "")
            : undefined}
        </ReadinessRow>

        <ReadinessRow label="Evidence Depth" badge={evidenceBadge(ev)}>
          {ev &&
            `${ev.evidence_items} items · ${ev.human_details} human · ${ev.scene_details} scene · ${ev.quotes} quotes` +
              (ev.detail ? ` — ${ev.detail}` : "")}
        </ReadinessRow>

        <ReadinessRow
          label="Supported Duration"
          badge={
            cap.status === "ready"
              ? { label: `~${cap.estimated_supported_minutes} min`, variant: "success" }
              : { label: `~${cap.estimated_supported_minutes} min`, variant: "warning" }
          }
        >
          {cap.requested_minutes} requested
          {cap.weak_areas && cap.weak_areas.length > 0
            ? ` · weak: ${cap.weak_areas.join(", ")}`
            : ""}
        </ReadinessRow>

        <ReadinessRow label="Master Generation" badge={master}>
          {mst?.reason}
        </ReadinessRow>

        <div className="grid grid-cols-2 gap-2 border-t border-border pt-2 sm:grid-cols-4">
          <Metric label="Full-text" value={depth.by_status["full_text"] ?? 0} />
          <Metric label="Partial" value={depth.by_status["partial_text"] ?? 0} />
          <Metric label="Summary-only" value={depth.by_status["summary_only"] ?? 0} />
          <Metric label="Chunks" value={depth.chunks} />
        </div>

        {depth.gap_plan.length > 0 && (
          <ul className="space-y-1 border-t border-border pt-2">
            {depth.gap_plan.map((g, i) => (
              <li key={i} className="flex items-start gap-2">
                <Badge variant={g.priority === "high" ? "danger" : "outline"}>
                  {g.type.replace(/_/g, " ")}
                </Badge>
                <span className="text-muted-foreground">{g.reason}</span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
