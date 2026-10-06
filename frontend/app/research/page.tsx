"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { FlaskConical, ListChecks } from "lucide-react";
import { api, pollResearchJob } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatDateTime, formatDuration, isRtl } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState } from "@/components/state";
import { CaseStatusBadge, RunStatusBadge } from "@/components/status-badge";
import { Sheet } from "@/components/ui/sheet";
import { TableSkeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import type { ResearchJob } from "@/lib/types";

export default function ResearchPage() {
  const { data: runs, error, loading, refetch } = useApi(() => api.allAgentRuns());
  const { data: cases, refetch: refetchCases } = useApi(() => api.listCases());
  const {
    data: jobs,
    loading: jobsLoading,
    refetch: refetchJobs,
  } = useApi(() => api.listResearchJobs());
  const [busyId, setBusyId] = useState<number | null>(null);
  const [jobStage, setJobStage] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [queriesJob, setQueriesJob] = useState<ResearchJob | null>(null);
  const pollAbort = useRef<AbortController | null>(null);

  useEffect(() => () => pollAbort.current?.abort(), []);

  const queue = (cases ?? []).filter((c) =>
    ["new", "researching", "researched"].includes(c.status),
  );

  async function runResearch(caseId: number) {
    setBusyId(caseId);
    setActionError(null);
    setJobStage("Starting research job…");
    pollAbort.current?.abort();
    const ctl = new AbortController();
    pollAbort.current = ctl;
    try {
      const res = await api.runResearch(caseId);
      if (res.job_id != null) {
        refetchJobs();
        const job = await pollResearchJob(res.job_id, {
          signal: ctl.signal,
          onUpdate: (j) =>
            setJobStage(
              j.status === "queued"
                ? `Job #${j.id} queued…`
                : `Job #${j.id} — ${j.current_stage ?? "running"} via ${j.provider}` +
                  (j.search_calls != null
                    ? ` · ${j.search_calls} searches · ${j.fetch_calls ?? 0} fetches · $${(j.total_cost_usd ?? 0).toFixed(3)}`
                    : ""),
            ),
        });
        if (job.status === "failed") {
          setActionError(job.error || "Research provider unavailable");
        }
      }
      refetch();
      refetchCases();
      refetchJobs();
    } catch (e) {
      if (!(e instanceof DOMException && e.name === "AbortError")) {
        setActionError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setBusyId(null);
      setJobStage(null);
    }
  }

  return (
    <div>
      <PageHeader
        title="Research"
        description="Agent activity and the research queue across all cases."
      />

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

      <div className="mb-6">
        <Card>
          <CardHeader>
            <CardTitle>Research Queue</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {queue.length === 0 ? (
              <div className="p-4">
                <EmptyState title="Queue is empty" description="Cases waiting for or under research will appear here." />
              </div>
            ) : (
              <Table>
                <THead>
                  <TR className="hover:bg-transparent">
                    <TH>Case</TH>
                    <TH>Status</TH>
                    <TH className="text-right">Sources</TH>
                    <TH className="text-right">Facts</TH>
                    <TH className="text-right" />
                  </TR>
                </THead>
                <TBody>
                  {queue.map((c) => (
                    <TR key={c.id}>
                      <TD>
                        <Link href={`/cases/${c.id}`} className="font-medium hover:text-primary">
                          {c.title}
                        </Link>
                      </TD>
                      <TD>
                        <CaseStatusBadge status={c.status} />
                      </TD>
                      <TD className="text-right tabular-nums">{c.sources}</TD>
                      <TD className="text-right tabular-nums">{c.facts}</TD>
                      <TD className="text-right">
                        <Button
                          size="sm"
                          variant="secondary"
                          loading={busyId === c.id}
                          onClick={() => runResearch(c.id)}
                        >
                          <FlaskConical className="size-3.5" /> Run Research
                        </Button>
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="mb-6">
        <Card>
          <CardHeader>
            <CardTitle>Provider Jobs</CardTitle>
            <Button variant="ghost" size="sm" onClick={refetchJobs}>
              Refresh
            </Button>
          </CardHeader>
          <CardContent className="p-0">
            {jobsLoading && (
              <div className="p-4">
                <TableSkeleton rows={3} cols={5} />
              </div>
            )}
            {jobs && jobs.length === 0 && (
              <div className="p-4">
                <EmptyState
                  title="No provider jobs"
                  description="Discovery and deep-research jobs appear here."
                />
              </div>
            )}
            {jobs && jobs.length > 0 && (
              <Table>
                <THead>
                  <TR className="hover:bg-transparent">
                    <TH>Type</TH>
                    <TH>Provider</TH>
                    <TH>Case</TH>
                    <TH>Status</TH>
                    <TH>Stage</TH>
                    <TH>Profile / Model</TH>
                    <TH className="text-right">S / F</TH>
                    <TH className="text-right">Tokens</TH>
                    <TH className="text-right">Cost</TH>
                    <TH>Started</TH>
                    <TH className="text-right">Duration</TH>
                    <TH>Summary</TH>
                    <TH />
                  </TR>
                </THead>
                <TBody>
                  {jobs.map((j) => (
                    <TR key={j.id}>
                      <TD className="font-medium capitalize">{j.job_type}</TD>
                      <TD className="text-muted-foreground">{j.provider}</TD>
                      <TD className="max-w-48">
                        {j.case_id ? (
                          <Link
                            href={`/cases/${j.case_id}`}
                            className="block truncate text-muted-foreground hover:text-primary"
                          >
                            {cases?.find((c) => c.id === j.case_id)?.title ?? `#${j.case_id}`}
                          </Link>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </TD>
                      <TD>
                        <RunStatusBadge status={j.status === "queued" ? "waiting" : j.status} />
                        {j.error_code && (
                          <span className="ml-1 text-[10px] text-rose-500">{j.error_code}</span>
                        )}
                      </TD>
                      <TD className="text-muted-foreground">
                        <Badge variant="outline">{j.current_stage ?? "—"}</Badge>
                      </TD>
                      <TD className="max-w-40">
                        <span className="block truncate text-[11px] text-muted-foreground">
                          {j.profile ?? "—"}
                        </span>
                        {j.model && (
                          <span className="block truncate font-mono text-[10px] text-muted-foreground/60">
                            {j.model}
                          </span>
                        )}
                      </TD>
                      <TD className="text-right tabular-nums text-muted-foreground">
                        {j.search_calls ?? "—"} / {j.fetch_calls ?? "—"}
                      </TD>
                      <TD className="text-right tabular-nums text-muted-foreground">
                        {j.total_tokens != null ? j.total_tokens.toLocaleString() : "—"}
                      </TD>
                      <TD className="text-right tabular-nums text-muted-foreground">
                        {j.total_cost_usd != null ? `$${j.total_cost_usd.toFixed(3)}` : "—"}
                      </TD>
                      <TD className="whitespace-nowrap text-muted-foreground">
                        {formatDateTime(j.started_at ?? j.created_at)}
                      </TD>
                      <TD className="text-right tabular-nums text-muted-foreground">
                        {formatDuration(j.duration_ms)}
                      </TD>
                      <TD className="max-w-64">
                        <span className="block truncate text-xs text-muted-foreground">
                          {j.error ? (
                            <span className="text-rose-500">{j.error}</span>
                          ) : (
                            j.result_summary ?? "—"
                          )}
                        </span>
                        {j.languages_completed && j.languages_completed.length > 0 && (
                          <span className="mt-0.5 block text-[10px] text-muted-foreground/60">
                            langs: {j.languages_completed.join(", ")}
                          </span>
                        )}
                      </TD>
                      <TD>
                        <Button
                          size="sm"
                          variant="ghost"
                          title="Query inspector"
                          onClick={() => setQueriesJob(j)}
                        >
                          <ListChecks className="size-3.5" />
                        </Button>
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Agent Runs</CardTitle>
          <Button variant="ghost" size="sm" onClick={refetch}>
            Refresh
          </Button>
        </CardHeader>
        <CardContent className="p-0">
          {loading && (
            <div className="p-4">
              <TableSkeleton rows={6} cols={5} />
            </div>
          )}
          {error && (
            <div className="p-4">
              <ErrorState message={error} onRetry={refetch} />
            </div>
          )}
          {runs && runs.length === 0 && (
            <div className="p-4">
              <EmptyState title="No agent runs yet" description="Runs are recorded when discovery, research, or writing executes." />
            </div>
          )}
          {runs && runs.length > 0 && (
            <Table>
              <THead>
                <TR className="hover:bg-transparent">
                  <TH>Agent</TH>
                  <TH>Case</TH>
                  <TH>Status</TH>
                  <TH>Started</TH>
                  <TH className="text-right">Duration</TH>
                  <TH>Summary</TH>
                </TR>
              </THead>
              <TBody>
                {runs.map((r) => (
                  <TR key={r.id}>
                    <TD className="font-medium">{r.agent_name}</TD>
                    <TD className="max-w-56">
                      {r.case_id ? (
                        <Link
                          href={`/cases/${r.case_id}`}
                          className="block truncate text-muted-foreground hover:text-primary"
                        >
                          {r.case_title ?? `#${r.case_id}`}
                        </Link>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
                    </TD>
                    <TD>
                      <RunStatusBadge status={r.status} />
                    </TD>
                    <TD className="whitespace-nowrap text-muted-foreground">
                      {formatDateTime(r.started_at)}
                    </TD>
                    <TD className="text-right tabular-nums">{formatDuration(r.duration_ms)}</TD>
                    <TD className="max-w-64">
                      <span className="block truncate text-xs text-muted-foreground">
                        {r.error ? <span className="text-rose-500">{r.error}</span> : r.output_summary ?? r.input_summary ?? "—"}
                      </span>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Sheet
        open={!!queriesJob}
        onClose={() => setQueriesJob(null)}
        title={
          queriesJob
            ? `Queries — job #${queriesJob.id} (${queriesJob.provider})`
            : ""
        }
      >
        {queriesJob && <QueryInspector jobId={queriesJob.id} />}
      </Sheet>
    </div>
  );
}

function QueryInspector({ jobId }: { jobId: number }) {
  const { data: queries, error, loading } = useApi(
    () => api.researchJobQueries(jobId),
    [jobId],
  );
  const [openId, setOpenId] = useState<number | null>(null);

  if (loading) return <TableSkeleton rows={4} cols={3} />;
  if (error) return <ErrorState message={error} onRetry={() => {}} />;
  if (!queries || queries.length === 0)
    return (
      <EmptyState
        title="No recorded queries"
        description="This job predates relational provenance or found nothing."
      />
    );

  return (
    <div className="space-y-2 text-xs">
      {queries.map((q) => (
        <div key={q.id} className="rounded-md border border-border">
          <button
            onClick={() => setOpenId(openId === q.id ? null : q.id)}
            className="flex w-full items-center gap-2 px-3 py-2 text-left hover:bg-muted/40"
          >
            <Badge variant="outline">{q.language}</Badge>
            <span
              className="flex-1 truncate font-medium"
              dir={isRtl(q.language) ? "rtl" : "ltr"}
            >
              {q.query}
            </span>
            <span className="shrink-0 text-muted-foreground">
              r{q.round}
              {q.purpose ? ` · ${q.purpose}` : ""} · {q.results.length} results
            </span>
          </button>
          {openId === q.id && (
            <ul className="space-y-1.5 border-t border-border px-3 py-2">
              {q.results.map((r) => (
                <li key={r.id} className="flex items-start gap-2">
                  <Badge variant={r.accepted ? "success" : "danger"}>
                    {r.accepted ? "accepted" : r.rejection_reason ?? "rejected"}
                  </Badge>
                  <div className="min-w-0 flex-1">
                    <a
                      href={r.final_url ?? r.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="block truncate text-primary hover:underline"
                    >
                      {r.title ?? r.url}
                    </a>
                    <span className="block truncate text-[10px] text-muted-foreground">
                      {r.url}
                      {r.final_url && r.final_url !== r.url
                        ? ` → ${r.final_url}`
                        : ""}
                      {r.result_language ? ` · ${r.result_language}` : ""}
                      {r.fetch_status ? ` · ${r.fetch_status}` : ""}
                      {r.relevance_score != null
                        ? ` · rel ${r.relevance_score.toFixed(2)}`
                        : ""}
                    </span>
                  </div>
                </li>
              ))}
              {q.results.length === 0 && (
                <li className="text-muted-foreground">no results returned</li>
              )}
            </ul>
          )}
        </div>
      ))}
    </div>
  );
}
