"use client";

import { Search } from "lucide-react";
import type { StatusCheck, StatusHistoryEntry } from "@/lib/types";
import { formatDateTime, humanize } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { ResolutionBadge } from "@/components/status-badge";
import { cn } from "@/lib/utils";
import { Collapsible, ResolutionChange, SourceLinks, pct } from "./shared";

const CHANGER_LABEL: Record<string, string> = {
  discovery: "Discovery",
  verifier: "Status verifier",
  monitor: "Unsolved-case monitor",
  research: "Research",
  user: "You (manual)",
};

/** Every status change of a case — who, why, from which sources — newest first. */
export function StatusTimeline({ history }: { history: StatusHistoryEntry[] }) {
  if (!history.length) {
    return <p className="text-sm text-muted-foreground">No status changes recorded yet.</p>;
  }
  const rows = [...history].reverse();
  return (
    <ol className="space-y-4 border-l border-border pl-4">
      {rows.map((h) => (
        <li key={h.id} className="relative">
          <span
            className={cn(
              "absolute -left-[21px] top-1.5 size-2.5 rounded-full border-2 border-card",
              h.new_status === "SOLVED"
                ? "bg-emerald-500"
                : h.new_status === "UNSOLVED"
                  ? "bg-rose-500"
                  : h.new_status === "STATUS_UNDER_REVIEW"
                    ? "bg-amber-500"
                    : "bg-muted-foreground",
            )}
            aria-hidden
          />
          <div className="flex flex-wrap items-center gap-2">
            <ResolutionChange from={h.previous_status} to={h.new_status} />
            <Badge variant="outline">{CHANGER_LABEL[h.changed_by] ?? humanize(h.changed_by)}</Badge>
            {h.confidence != null && (
              <span className="text-xs tabular-nums text-muted-foreground">confidence {pct(h.confidence)}</span>
            )}
            <span className="text-xs text-muted-foreground">{formatDateTime(h.created_at)}</span>
          </div>
          {h.reason && <p className="mt-1 max-w-3xl text-xs leading-5 text-foreground/90">{h.reason}</p>}
          <SourceLinks sources={h.sources} className="mt-1" />
        </li>
      ))}
    </ol>
  );
}

const OUTCOME: Record<string, { label: string; variant: "outline" | "info" | "success" | "warning" | "default" | "danger" }> = {
  no_signal: { label: "No signal", variant: "outline" },
  signal: { label: "Signal found", variant: "info" },
  confirmed_change: { label: "Status changed", variant: "success" },
  not_confirmed: { label: "Not confirmed", variant: "warning" },
  unchanged: { label: "Unchanged", variant: "default" },
  error: { label: "Error", variant: "danger" },
};

export function OutcomeBadge({ outcome }: { outcome: string }) {
  const meta = OUTCOME[outcome] ?? { label: humanize(outcome), variant: "default" as const };
  return <Badge variant={meta.variant}>{meta.label}</Badge>;
}

function factText(f: StatusCheck["new_facts"][number]): { text: string; url?: string } {
  if (typeof f === "string") return { text: f };
  return { text: f.fact ?? "", url: f.url };
}

/** Monitor checks: fast signal scans and deep verifications, newest first. */
export function StatusChecks({ checks, compact = false }: { checks: StatusCheck[]; compact?: boolean }) {
  if (!checks.length) {
    return (
      <p className="text-sm text-muted-foreground">
        The monitor has not checked this case yet. It watches UNSOLVED and under-review cases about twice a week.
      </p>
    );
  }
  return (
    <ul className="divide-y divide-border rounded-md border border-border">
      {checks.map((c) => {
        const changed = c.previous_status && c.current_status && c.previous_status !== c.current_status;
        return (
          <li key={c.id} className="space-y-1.5 px-3 py-2.5 text-xs">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-muted-foreground">{formatDateTime(c.created_at)}</span>
              <Badge variant={c.stage === "deep" ? "accent" : "outline"} title={c.stage === "deep"
                ? "Deep: signal pages fetched and judged by the status verifier"
                : "Fast: searches and signal words only — no fetch, no LLM"}>
                {c.stage === "deep" ? "Deep check" : "Fast check"}
              </Badge>
              <OutcomeBadge outcome={c.outcome} />
              {changed ? (
                <ResolutionChange from={c.previous_status} to={c.current_status} />
              ) : (
                <ResolutionBadge status={c.current_status ?? c.previous_status} />
              )}
              {c.confidence != null && (
                <span className="tabular-nums text-muted-foreground">confidence {pct(c.confidence)}</span>
              )}
              <span className="ml-auto tabular-nums text-muted-foreground" title="Cost of this check">
                {c.search_calls} search{c.search_calls === 1 ? "" : "es"} · {c.fetch_calls} fetch
                {c.fetch_calls === 1 ? "" : "es"} · {c.llm_calls} LLM call{c.llm_calls === 1 ? "" : "s"}
              </span>
            </div>
            {c.reason && <p className="leading-5 text-foreground/90">{c.reason}</p>}
            {c.signals.length > 0 && (
              <div>
                <p className="mb-0.5 font-medium text-muted-foreground">Signals</p>
                <ul className="space-y-0.5">
                  {c.signals.map((s, i) => (
                    <li key={`${s.signal}-${s.url}-${i}`} className="flex min-w-0 flex-wrap items-baseline gap-1.5">
                      <Badge variant="info">{humanize(s.signal)}</Badge>
                      <span className="text-muted-foreground">“{s.term}”</span>
                      {s.url ? (
                        <a
                          href={s.url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="min-w-0 break-words text-primary hover:underline"
                        >
                          {s.title || s.url}
                        </a>
                      ) : (
                        <span>{s.title}</span>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {c.sources.length > 0 && (
              <div>
                <p className="mb-0.5 font-medium text-muted-foreground">Sources</p>
                <SourceLinks sources={c.sources} />
              </div>
            )}
            {c.new_facts.length > 0 && !compact && (
              <div>
                <p className="mb-0.5 font-medium text-muted-foreground">New facts</p>
                <ul className="list-disc space-y-0.5 pl-4">
                  {c.new_facts.map((f, i) => {
                    const { text, url } = factText(f);
                    return (
                      <li key={i}>
                        {text}
                        {url && (
                          <a
                            href={url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="ml-1 text-primary hover:underline"
                          >
                            source
                          </a>
                        )}
                      </li>
                    );
                  })}
                </ul>
              </div>
            )}
            {c.queries.length > 0 && !compact && (
              <Collapsible title={<span className="flex items-center gap-1"><Search className="size-3" aria-hidden /> Queries</span>} count={c.queries.length}>
                <ul className="space-y-0.5 text-muted-foreground">
                  {c.queries.map((q, i) => (
                    <li key={i}>
                      <span className="text-foreground">{q.query}</span>
                      {q.language && ` · ${q.language}`}
                      {q.time_range && ` · last ${q.time_range}`}
                    </li>
                  ))}
                </ul>
              </Collapsible>
            )}
          </li>
        );
      })}
    </ul>
  );
}
