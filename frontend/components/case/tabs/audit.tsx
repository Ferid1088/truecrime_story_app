"use client";

import Link from "next/link";
import { Check, Repeat, X } from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatDate, formatDateTime, formatTimecode, humanize, langLabel } from "@/lib/format";
import type { AuditProduction, CaseAudit, SuggestionRecord } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { TableSkeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { ErrorState } from "@/components/state";
import { ResolutionBadge } from "@/components/status-badge";
import {
  Collapsible,
  FieldLabel,
  FilmStateBadge,
  ProductionTypeBadge,
  ResolutionChange,
  SourceLinks,
  filmTitle,
  openingLabel,
  pct,
} from "@/components/lifecycle/shared";
import { StatusChecks, StatusTimeline } from "@/components/lifecycle/status-history";

const TIERS: Record<number, string> = {
  1: "Tier 1 — exact case evidence or footage",
  2: "Tier 2 — the exact person, place or object",
  3: "Tier 3 — the exact city, building or area",
  4: "Tier 4 — contextually accurate licensed imagery",
  5: "Tier 5 — generic atmosphere (last resort)",
};

function TierBadge({ tier }: { tier: number | null }) {
  if (tier == null) return <span className="text-muted-foreground">—</span>;
  return (
    <Badge variant={tier <= 2 ? "success" : tier === 3 ? "info" : tier === 4 ? "outline" : "warning"} title={TIERS[tier]}>
      T{tier}
    </Badge>
  );
}

/** Why the system decided what it decided — read from stored data only. */
export function AuditTab({ caseId }: { caseId: number }) {
  const { data, error, loading, refetch } = useApi(() => api.caseAudit(caseId), [caseId]);

  if (loading) return <TableSkeleton rows={6} cols={4} />;
  if (error) return <ErrorState message={error} onRetry={refetch} />;
  if (!data) return null;

  const own = data.suggestions.filter((s) => s.case_id === caseId && s.duplicate_of_case_id !== caseId);
  const duplicates = data.suggestions.filter((s) => s.duplicate_of_case_id === caseId);

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Why this case was suggested</CardTitle>
          <span className="text-xs text-muted-foreground">
            origin: {data.case.origin ? humanize(data.case.origin) : "unknown"}
          </span>
        </CardHeader>
        <CardContent>
          {own.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {data.case.origin === "manual"
                ? "Created by hand — no discovery suggestion behind it."
                : "No stored suggestion led to this case."}
            </p>
          ) : (
            <ul className="space-y-3">
              {own.map((s) => (
                <SuggestionWhy key={s.id} s={s} />
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Duplicates pointing here</CardTitle>
          <span className="text-xs text-muted-foreground">suggestions rejected as this case</span>
        </CardHeader>
        <CardContent>
          {duplicates.length === 0 ? (
            <p className="text-sm text-muted-foreground">No later suggestion was rejected as a duplicate of this case.</p>
          ) : (
            <ul className="divide-y divide-border rounded-md border border-border">
              {duplicates.map((s) => (
                <li key={s.id} className="px-3 py-2 text-xs">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium">{s.title}</span>
                    <ResolutionBadge status={s.resolution_status} />
                    <span className="text-muted-foreground">{formatDateTime(s.created_at)}</span>
                  </div>
                  <p className="mt-1 leading-5 text-foreground/90">{s.duplicate_reason || s.suggestion_reason || "—"}</p>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Status changes</CardTitle>
            <ResolutionBadge status={data.case.resolution_status} confidence={data.case.resolution_confidence} />
          </CardHeader>
          <CardContent>
            <StatusTimeline history={data.status_history} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Monitor checks</CardTitle>
            <span className="text-xs text-muted-foreground">latest {data.status_checks.length}</span>
          </CardHeader>
          <CardContent>
            <StatusChecks checks={data.status_checks} compact />
          </CardContent>
        </Card>
      </div>

      {data.productions.length === 0 ? (
        <Card>
          <CardHeader>
            <CardTitle>Productions</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted-foreground">
              No production script yet — pictures, maps and music decisions appear here once a documentary is composed.
            </p>
          </CardContent>
        </Card>
      ) : (
        data.productions.map((p) => <ProductionAudit key={p.production_script_id} p={p} />)
      )}

      <FilmsAndFollowUps data={data} />
    </div>
  );
}

function SuggestionWhy({ s }: { s: SuggestionRecord }) {
  return (
    <li className="space-y-2 rounded-md border border-border px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">{s.title}</span>
        <Badge variant="outline">{s.state}</Badge>
        <span className="text-xs text-muted-foreground">Status at suggestion:</span>
        <ResolutionBadge status={s.resolution_status} confidence={s.resolution_confidence} />
        <span className="text-xs text-muted-foreground">{formatDateTime(s.created_at)}</span>
      </div>
      {s.suggestion_reason && (
        <div>
          <FieldLabel>Reason</FieldLabel>
          <p className="text-xs leading-5 text-foreground/90">{s.suggestion_reason}</p>
        </div>
      )}
      {s.resolution_evidence && (
        <div>
          <FieldLabel>Status evidence</FieldLabel>
          <p className="text-xs leading-5 text-foreground/90">{s.resolution_evidence}</p>
        </div>
      )}
      <p className="text-xs text-muted-foreground">
        Incident {s.incident_date || "—"} · latest development {s.latest_development_date || "—"}
        {s.location && ` · ${s.location}`}
        {s.rank_score != null && ` · rank score ${s.rank_score.toFixed(2)}`}
        {s.recency_score != null && ` · recency ${s.recency_score.toFixed(2)}`}
      </p>
      <SourceLinks sources={s.source_urls} max={5} />
    </li>
  );
}

function ProductionAudit({ p }: { p: AuditProduction }) {
  return (
    <Card>
      <CardHeader className="flex-wrap gap-2">
        <CardTitle className="flex flex-wrap items-center gap-2">
          Production · {langLabel(p.language)}
          <Badge variant="outline">{p.mode}</Badge>
          <ProductionTypeBadge type={p.production_type} />
        </CardTitle>
        <span className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          script #{p.production_script_id} · case status in the script
          <ResolutionBadge status={p.case_status} />
        </span>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-sm">
          <span className="text-muted-foreground">Opening strategy: </span>
          <span className="font-medium">{openingLabel(p.opening_strategy)}</span>
        </p>

        <Collapsible title="Pictures — why each was chosen" count={p.pictures.length}>
          {p.pictures.length === 0 ? (
            <p className="text-xs text-muted-foreground">No picture usage recorded.</p>
          ) : (
            <div className="-mx-4">
              <Table className="text-xs">
                <THead>
                  <TR className="hover:bg-transparent">
                    <TH>Time</TH>
                    <TH>Kind</TH>
                    <TH>Tier</TH>
                    <TH>Asset</TH>
                    <TH className="min-w-48">Sentence</TH>
                    <TH className="min-w-56">Why</TH>
                    <TH>Repeat</TH>
                  </TR>
                </THead>
                <TBody>
                  {p.pictures.map((u, i) => (
                    <TR key={i}>
                      <TD className="whitespace-nowrap tabular-nums">
                        {formatTimecode(u.start, { tenths: true })}
                        {u.seconds != null && <span className="block text-muted-foreground">{u.seconds.toFixed(1)} s</span>}
                      </TD>
                      <TD>{u.kind ?? "—"}</TD>
                      <TD>
                        <TierBadge tier={u.tier} />
                      </TD>
                      <TD className="max-w-48">
                        <span className="font-mono">{u.asset ?? "—"}</span>
                        {u.title && <span className="block truncate text-muted-foreground" title={u.title}>{u.title}</span>}
                      </TD>
                      <TD className="leading-5 text-muted-foreground">{u.sentence || "—"}</TD>
                      <TD className="leading-5">{u.reason || "—"}</TD>
                      <TD className="leading-5">
                        {u.appearance > 1 ? (
                          <span className="inline-flex flex-col gap-0.5">
                            <span className="inline-flex items-center gap-1">
                              <Repeat className="size-3" aria-hidden /> #{u.appearance}
                              {u.repeat_justified === true ? (
                                <Check className="size-3 text-emerald-500" aria-label="justified" />
                              ) : u.repeat_justified === false ? (
                                <X className="size-3 text-rose-500" aria-label="not justified" />
                              ) : null}
                            </span>
                            {u.repeat_reason && <span className="text-muted-foreground">{u.repeat_reason}</span>}
                          </span>
                        ) : (
                          <span className="text-muted-foreground">first</span>
                        )}
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </div>
          )}
        </Collapsible>

        <Collapsible title="Maps — why each was shown" count={p.maps.length} defaultOpen={p.maps.length > 0 && p.maps.length <= 6}>
          {p.maps.length === 0 ? (
            <p className="text-xs text-muted-foreground">No map in this production.</p>
          ) : (
            <ul className="space-y-1.5 text-xs">
              {p.maps.map((m, i) => (
                <li key={i} className="rounded border border-border bg-subtle px-2 py-1.5">
                  <span className="font-medium tabular-nums">{formatTimecode(m.start, { tenths: true })}</span>
                  {m.sentence && <span className="text-muted-foreground"> · “{m.sentence}”</span>}
                  <p className="mt-0.5 leading-5">{m.reason || "—"}</p>
                </li>
              ))}
            </ul>
          )}
        </Collapsible>

        <Collapsible title="Music & silence — why each cue" count={p.music.length} defaultOpen={p.music.length > 0 && p.music.length <= 12}>
          {p.music.length === 0 ? (
            <p className="text-xs text-muted-foreground">No music cue recorded (narration only).</p>
          ) : (
            <div className="-mx-4">
              <Table className="text-xs">
                <THead>
                  <TR className="hover:bg-transparent">
                    <TH>Time</TH>
                    <TH>Purpose</TH>
                    <TH>Mood</TH>
                    <TH>Track</TH>
                    <TH className="min-w-56">Why here</TH>
                    <TH className="min-w-56">Why this track</TH>
                  </TR>
                </THead>
                <TBody>
                  {p.music.map((m, i) => (
                    <TR key={i}>
                      <TD className="whitespace-nowrap tabular-nums">
                        {formatTimecode(m.start, { tenths: true })}–{formatTimecode(m.end, { tenths: true })}
                      </TD>
                      <TD>{humanize(m.purpose)}</TD>
                      <TD>{m.mood ?? "—"}</TD>
                      <TD className="font-mono">{m.track ?? (m.purpose === "silence" ? "room tone" : "—")}</TD>
                      <TD className="leading-5">{m.why || "—"}</TD>
                      <TD className="leading-5 text-muted-foreground">{m.selection_reason || "—"}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </div>
          )}
        </Collapsible>

        <Collapsible
          title="Visual search requests"
          count={p.search_requests.length}
          defaultOpen={p.search_requests.length > 0 && p.search_requests.length <= 6}
        >
          {p.search_requests.length === 0 ? (
            <p className="text-xs text-muted-foreground">No production-time search was needed.</p>
          ) : (
            <ul className="space-y-1.5 text-xs">
              {p.search_requests.map((r, i) => (
                <li key={i} className="space-y-1 rounded border border-border bg-subtle px-2 py-1.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{r.entity_name || r.entity || "—"}</span>
                    <span className="font-mono text-muted-foreground">{r.beat_id}</span>
                    {r.source && <Badge variant="outline">{humanize(r.source)}</Badge>}
                    <Badge variant={r.used ? "success" : r.assets_found.length ? "warning" : "outline"}>
                      {r.used ? "found & used" : r.assets_found.length ? `${r.assets_found.length} found, not used` : "nothing found"}
                    </Badge>
                  </div>
                  {r.sentence && <p className="text-muted-foreground">“{r.sentence}”</p>}
                  {r.why && <p className="leading-5">{r.why}</p>}
                  {r.queries.length > 0 && (
                    <p className="text-muted-foreground">Queries: {r.queries.join(" · ")}</p>
                  )}
                  {r.assets_found.length > 0 && (
                    <p className="font-mono text-muted-foreground">{r.assets_found.join(", ")}</p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </Collapsible>
      </CardContent>
    </Card>
  );
}

function FilmsAndFollowUps({ data }: { data: CaseAudit }) {
  if (!data.films.length && !data.follow_ups.length) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Films & follow-ups</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {data.films.length > 0 && (
          <ul className="space-y-1 text-xs">
            {data.films.map((f) => (
              <li key={f.id} className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{filmTitle(f)}</span>
                <FilmStateBadge state={f.state} />
                <ProductionTypeBadge type={f.production_type} />
                <span className="text-muted-foreground">
                  {langLabel(f.language)} · opening {openingLabel(f.opening_strategy)} · made while
                </span>
                <ResolutionBadge status={f.status_at_production} />
                {f.published_at && <span className="text-muted-foreground">· published {formatDate(f.published_at)}</span>}
              </li>
            ))}
          </ul>
        )}
        {data.follow_ups.length > 0 && (
          <div>
            <FieldLabel>Follow-up candidates</FieldLabel>
            <ul className="space-y-1.5 text-xs">
              {data.follow_ups.map((fu) => (
                <li key={fu.id} className="rounded border border-border px-2 py-1.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <ResolutionChange from={fu.previous_status} to={fu.new_status} />
                    <Badge variant="outline">{humanize(fu.state)}</Badge>
                    {fu.confidence != null && <span className="text-muted-foreground">confidence {pct(fu.confidence)}</span>}
                    <span className="text-muted-foreground">{formatDateTime(fu.created_at)}</span>
                    {fu.follow_up_job_id != null && (
                      <Link href={`/documentary/${fu.case_id}`} className="text-primary hover:underline">
                        job #{fu.follow_up_job_id}
                      </Link>
                    )}
                  </div>
                  {fu.development && <p className="mt-1 leading-5">{fu.development}</p>}
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
