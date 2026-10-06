"use client";

import { useRef, useState } from "react";
import { ExternalLink, Play } from "lucide-react";
import { api, pollResearchJob } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { langLabel } from "@/lib/format";
import type { ClaimClusterItem, DossierEntry } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Sheet } from "@/components/ui/sheet";
import { EmptyState, ErrorState } from "@/components/state";
import { TableSkeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { cn } from "@/lib/utils";

const TRANSCRIPT_STATUS: Record<string, string> = {
  discovered: "discovered",
  pending: "pending",
  available: "transcript",
  unavailable: "no transcript",
  processed: "processed",
  failed: "failed",
};

function transcriptVariant(s: string): "success" | "warning" | "default" | "danger" {
  if (s === "processed" || s === "available") return "success";
  if (s === "failed" || s === "unavailable") return "danger";
  return "warning";
}

function independenceVariant(
  s: string,
): "success" | "warning" | "default" {
  if (s === "primary" || s === "independent_secondary") return "success";
  if (s === "likely_derivative") return "warning";
  return "default";
}

function ts(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return h > 0
    ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`
    : `${m}:${String(sec).padStart(2, "0")}`;
}

type View = "videos" | "clusters" | "dossier";

export function VideosTab({
  caseId,
  refreshKey,
}: {
  caseId: number;
  refreshKey: number;
}) {
  const [view, setView] = useState<View>("videos");
  const [busy, setBusy] = useState(false);
  const [jobStage, setJobStage] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [selected, setSelected] = useState<number | null>(null);
  const pollAbort = useRef<AbortController | null>(null);

  const {
    data: videos,
    error,
    loading,
    refetch,
  } = useApi(() => api.listVideos(caseId), [caseId, refreshKey, tick]);

  async function runVideoResearch() {
    setBusy(true);
    setActionError(null);
    setJobStage("Starting video research job…");
    pollAbort.current?.abort();
    const ctl = new AbortController();
    pollAbort.current = ctl;
    try {
      const res = await api.startVideoResearch(caseId);
      if (res.job_id != null) {
        const job = await pollResearchJob(res.job_id, {
          signal: ctl.signal,
          onUpdate: (j) =>
            setJobStage(
              j.status === "queued"
                ? "Video research queued…"
                : "Video research running — discovering, fetching transcripts, extracting claims…",
            ),
        });
        if (job.status === "failed") {
          throw new Error(job.error || "Video research failed");
        }
      }
      setTick((t) => t + 1);
    } catch (e) {
      if (!(e instanceof DOMException && e.name === "AbortError")) {
        setActionError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setBusy(false);
      setJobStage(null);
    }
  }

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div className="flex gap-1">
          {(
            [
              ["videos", "Videos"],
              ["clusters", "Claim Clusters"],
              ["dossier", "Dossier"],
            ] as [View, string][]
          ).map(([v, label]) => (
            <button
              key={v}
              onClick={() => setView(v)}
              className={cn(
                "cursor-pointer rounded-md px-2.5 py-1.5 text-xs font-medium transition-colors",
                view === v
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground hover:text-foreground",
              )}
            >
              {label}
            </button>
          ))}
        </div>
        <Button
          size="sm"
          variant="secondary"
          onClick={runVideoResearch}
          loading={busy}
        >
          <Play className="size-3.5" /> Run Video Research
        </Button>
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

      {view === "videos" && (
        <>
          {loading && <TableSkeleton rows={4} cols={6} />}
          {error && <ErrorState message={error} onRetry={refetch} />}
          {videos && videos.length === 0 && (
            <EmptyState
              title="No videos researched yet"
              description="Video research discovers multilingual YouTube coverage, fetches legitimate transcripts, and extracts timestamped claims that merge into the evidence layer."
              action={
                <Button size="sm" variant="secondary" onClick={runVideoResearch} loading={busy}>
                  <Play className="size-3.5" /> Run Video Research
                </Button>
              }
            />
          )}
          {videos && videos.length > 0 && (
            <Card>
              <CardContent className="p-0">
                <Table>
                  <THead>
                    <TR className="hover:bg-transparent">
                      <TH>Video</TH>
                      <TH>Channel</TH>
                      <TH>Language</TH>
                      <TH>Type</TH>
                      <TH>Transcript</TH>
                      <TH>Independence</TH>
                      <TH className="text-right">Value</TH>
                      <TH className="text-right">Claims</TH>
                    </TR>
                  </THead>
                  <TBody>
                    {videos.map((v) => (
                      <TR
                        key={v.id}
                        className="cursor-pointer"
                        onClick={() => setSelected(v.id)}
                      >
                        <TD className="max-w-72">
                          <span className="block truncate font-medium">{v.title}</span>
                        </TD>
                        <TD className="text-muted-foreground">{v.channel_name ?? "—"}</TD>
                        <TD className="text-muted-foreground">{langLabel(v.language)}</TD>
                        <TD>
                          <Badge variant="outline">{v.classification.replace(/_/g, " ")}</Badge>
                        </TD>
                        <TD>
                          <Badge variant={transcriptVariant(v.transcript_status)}>
                            {TRANSCRIPT_STATUS[v.transcript_status] ?? v.transcript_status}
                          </Badge>
                        </TD>
                        <TD>
                          <Badge variant={independenceVariant(v.source_independence)}>
                            {v.source_independence.replace(/_/g, " ")}
                          </Badge>
                        </TD>
                        <TD className="text-right tabular-nums">
                          {v.value_score != null ? v.value_score.toFixed(1) : "—"}
                        </TD>
                        <TD className="text-right tabular-nums">{v.claim_count}</TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </CardContent>
            </Card>
          )}
        </>
      )}

      {view === "clusters" && <ClustersView caseId={caseId} tick={tick} />}
      {view === "dossier" && <DossierView caseId={caseId} tick={tick} />}

      <Sheet
        open={selected != null}
        onClose={() => setSelected(null)}
        title={videos?.find((v) => v.id === selected)?.title ?? "Video"}
      >
        {selected != null && <VideoDetailSheet caseId={caseId} videoId={selected} />}
      </Sheet>
    </div>
  );
}

function VideoDetailSheet({ caseId, videoId }: { caseId: number; videoId: number }) {
  const { data: v, error, loading } = useApi(
    () => api.getVideo(caseId, videoId),
    [caseId, videoId],
  );
  const [showSegments, setShowSegments] = useState(false);

  if (loading) return <TableSkeleton rows={4} cols={2} />;
  if (error) return <ErrorState message={error} />;
  if (!v) return null;

  return (
    <div className="space-y-4 text-sm">
      <div>
        <p className="font-medium leading-snug">{v.title}</p>
        <a
          href={v.url}
          target="_blank"
          rel="noopener noreferrer"
          className="mt-1 inline-flex items-center gap-1 text-xs text-primary hover:underline"
        >
          open on {v.platform} <ExternalLink className="size-3" />
        </a>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <Field label="Channel" value={v.channel_name ?? "—"} />
        <Field label="Language" value={langLabel(v.language)} />
        <Field label="Classification" value={v.classification.replace(/_/g, " ")} />
        <Field label="Independence" value={v.source_independence.replace(/_/g, " ")} />
        <Field label="Transcript" value={v.transcript_status} />
        <Field label="Transcript type" value={v.transcript_type ?? "—"} />
        <Field label="Duration" value={v.duration_seconds != null ? ts(v.duration_seconds) : "—"} />
        <Field
          label="Novel info"
          value={v.novel_information_ratio != null ? `${Math.round(v.novel_information_ratio * 100)}%` : "—"}
        />
        <Field label="Value score" value={v.value_score?.toFixed(2) ?? "—"} />
        <Field label="Claims" value={String(v.claim_count)} />
      </div>

      {v.insights.length > 0 && (
        <div>
          <p className="mb-1.5 text-xs font-medium text-muted-foreground">
            Narrative insights ({v.insights.length})
          </p>
          <ul className="space-y-1.5">
            {v.insights.map((i) => (
              <li key={i.id} className="rounded-md border border-border px-2.5 py-1.5 text-xs">
                <Badge variant="info" className="mr-1.5">{i.type.replace(/_/g, " ")}</Badge>
                {i.canonical_text_en}
              </li>
            ))}
          </ul>
        </div>
      )}

      {v.claims.length > 0 && (
        <div>
          <p className="mb-1.5 text-xs font-medium text-muted-foreground">
            Extracted claims ({v.claims.length}) — each traceable to a timestamp
          </p>
          <ul className="max-h-72 space-y-1.5 overflow-y-auto">
            {v.claims.map((c) => (
              <li key={c.id} className="rounded-md border border-border px-2.5 py-1.5 text-xs">
                <div className="mb-0.5 flex flex-wrap items-center gap-1.5">
                  <Badge variant="outline">{c.claim_type.replace(/_/g, " ")}</Badge>
                  <Badge
                    variant={
                      c.verification_status === "strongly_supported"
                        ? "success"
                        : c.verification_status === "supported"
                          ? "info"
                          : "default"
                    }
                  >
                    {c.verification_status.replace(/_/g, " ")}
                  </Badge>
                  <span className="tabular-nums text-muted-foreground">
                    {ts(c.timestamp_start)}
                  </span>
                  {c.promoted_fact_id != null && (
                    <Badge variant="accent">→ Fact F{String(c.promoted_fact_id).padStart(3, "0")}</Badge>
                  )}
                </div>
                <p>{c.canonical_claim_en}</p>
                {c.original_claim && c.original_claim !== c.canonical_claim_en && (
                  <p className="mt-0.5 text-muted-foreground" dir="auto">{c.original_claim}</p>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {v.segments.length > 0 && (
        <div>
          <button
            onClick={() => setShowSegments((s) => !s)}
            className="cursor-pointer text-xs font-medium text-muted-foreground hover:text-foreground"
          >
            {showSegments ? "Hide" : "Show"} transcript ({v.segments.length} segments)
          </button>
          {showSegments && (
            <div className="mt-1.5 max-h-72 space-y-2 overflow-y-auto rounded-md border border-border bg-subtle p-3">
              {v.segments.map((s) => (
                <div key={s.id}>
                  <p className="text-[10px] tabular-nums text-muted-foreground">
                    {ts(s.start_seconds)}–{ts(s.end_seconds)}
                  </p>
                  <p className="whitespace-pre-wrap text-xs leading-5 text-foreground/80" dir="auto">
                    {s.text}
                  </p>
                  {s.canonical && s.canonical !== s.text && (
                    <p className="whitespace-pre-wrap text-xs leading-5 text-foreground/60">
                      {s.canonical}
                    </p>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ClustersView({ caseId, tick }: { caseId: number; tick: number }) {
  const { data: clusters, error, loading } = useApi(
    () => api.listClaimClusters(caseId),
    [caseId, tick],
  );
  if (loading) return <TableSkeleton rows={4} cols={4} />;
  if (error) return <ErrorState message={error} />;
  if (!clusters || clusters.length === 0)
    return (
      <EmptyState
        title="No claim clusters yet"
        description="Clusters appear after video research extracts claims — cross-video claims deduplicate here and show how many independent source families support each fact."
      />
    );
  return (
    <div className="space-y-2">
      {clusters.map((cl: ClaimClusterItem) => (
        <Card key={cl.id}>
          <CardContent className="p-4">
            <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
              <Badge
                variant={
                  cl.support_status === "strongly_supported"
                    ? "success"
                    : cl.support_status === "supported"
                      ? "info"
                      : "default"
                }
              >
                {cl.support_status.replace(/_/g, " ")}
              </Badge>
              <Badge variant="outline">{cl.claim_type.replace(/_/g, " ")}</Badge>
              <span className="text-xs text-muted-foreground">
                {cl.independent_source_family_count} independent{" "}
                {cl.independent_source_family_count === 1 ? "family" : "families"} ·{" "}
                {cl.member_count} {cl.member_count === 1 ? "claim" : "claims"}
              </span>
              {cl.promoted_fact_id != null && (
                <Badge variant="accent">promoted to Fact</Badge>
              )}
            </div>
            <p className="text-sm">{cl.canonical_claim_en}</p>
            <ul className="mt-2 space-y-1">
              {cl.claims.map((m) => (
                <li
                  key={m.id}
                  className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground"
                >
                  <span className="tabular-nums">{ts(m.timestamp_start)}</span>
                  <span className="truncate">{m.channel ?? m.video ?? "video"}</span>
                  <span>{langLabel(m.original_language)}</span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}

function DossierView({ caseId, tick }: { caseId: number; tick: number }) {
  const { data: d, error, loading } = useApi(
    () => api.getDossier(caseId),
    [caseId, tick],
  );
  if (loading) return <TableSkeleton rows={5} cols={3} />;
  if (error) return <ErrorState message={error} />;
  if (!d) return null;

  const sections: [string, DossierEntry[]][] = [
    ["Verified facts", d.verified_facts],
    ["Human details", d.human_details],
    ["Scene details", d.scene_details],
    ["Investigation details", d.investigation_details],
    ["Physical evidence", d.physical_evidence],
    ["Quotes", d.quotes],
    ["Historical context", d.historical_context],
  ];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
        <span>{d.stats.evidence_items} evidence items</span>
        <span>{d.stats.transcript_claims} transcript claims</span>
        <span>{d.stats.claim_clusters} clusters</span>
        <span>{d.stats.videos} videos</span>
        <span>{d.stats.insights} insights</span>
      </div>

      {sections.map(([label, items]) =>
        items.length === 0 ? null : (
          <div key={label}>
            <p className="mb-1.5 text-xs font-medium text-muted-foreground">
              {label} ({items.length})
            </p>
            <ul className="space-y-1.5">
              {items.map((e) => (
                <li key={e.evidence_id} className="rounded-md border border-border px-3 py-2 text-xs">
                  <div className="mb-0.5 flex flex-wrap items-center gap-1.5">
                    <span className="font-mono text-muted-foreground">{e.evidence_id}</span>
                    {e.independent_source_count != null && e.independent_source_count > 0 && (
                      <Badge variant="outline">
                        {e.independent_source_count} src
                      </Badge>
                    )}
                    {e.languages && e.languages.length > 1 && (
                      <Badge variant="info">{e.languages.join(" · ")}</Badge>
                    )}
                    {e.status && <Badge variant="default">{e.status}</Badge>}
                  </div>
                  <p>{e.canonical_claim_en}</p>
                  {e.source_refs.length > 0 && (
                    <p className="mt-1 text-muted-foreground">
                      {e.source_refs.map((r, i) => (
                        <span key={i}>
                          {i > 0 && " · "}
                          {r.kind === "transcript_claim"
                            ? `${r.video_title ?? "video"} @ ${r.timestamp_label ?? "?"}`
                            : (r.title ?? `source ${r.source_id}`)}
                        </span>
                      ))}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ),
      )}

      {d.narrative_questions.length > 0 && (
        <div>
          <p className="mb-1.5 text-xs font-medium text-muted-foreground">
            Narrative questions ({d.narrative_questions.length})
          </p>
          <ul className="space-y-1.5">
            {d.narrative_questions.map((q, i) => (
              <li key={i} className="rounded-md border border-border px-3 py-2 text-xs">
                <Badge variant="info" className="mr-1.5">{q.type.replace(/_/g, " ")}</Badge>
                {q.text}
              </li>
            ))}
          </ul>
        </div>
      )}

      {d.verified_facts.length === 0 && d.unverified_claims.length === 0 && (
        <EmptyState
          title="Dossier is empty"
          description="Run research (and video research) first — the dossier merges verified evidence from every source kind."
        />
      )}
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-0.5">{value}</p>
    </div>
  );
}
