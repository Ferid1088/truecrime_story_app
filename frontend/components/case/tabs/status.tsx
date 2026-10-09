"use client";

import { useId, useState } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatDateTime, humanize } from "@/lib/format";
import type { CaseDetail, ResolutionStatus } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Skeleton, TableSkeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { ErrorState } from "@/components/state";
import { RESOLUTION_STATUSES, ResolutionBadge, resolutionLabel, toResolution } from "@/components/status-badge";
import { FieldLabel, pct } from "@/components/lifecycle/shared";
import { StatusChecks, StatusTimeline } from "@/components/lifecycle/status-history";

/**
 * The case's resolution status: current value with its evidence, the
 * full history (who, why, sources), the monitor's checks, and a form to
 * set the status by hand.
 */
export function StatusTab({ caseData, onChanged }: { caseData: CaseDetail; onChanged: () => void }) {
  const caseId = caseData.id;
  const [tick, setTick] = useState(0);
  const resolution = useApi(() => api.caseResolution(caseId), [caseId, tick], { keepPrevious: true });
  const checks = useApi(() => api.caseStatusChecks(caseId), [caseId, tick], { keepPrevious: true });
  const current = resolution.data;

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
      <div className="min-w-0 space-y-4 lg:col-span-2">
        <Card>
          <CardHeader>
            <CardTitle>Case status</CardTitle>
            {current?.resolution_checked_at && (
              <span className="text-xs text-muted-foreground">
                last checked {formatDateTime(current.resolution_checked_at)}
              </span>
            )}
          </CardHeader>
          <CardContent className="space-y-4">
            {!current && resolution.error ? (
              <ErrorState message={resolution.error} onRetry={resolution.refetch} />
            ) : !current ? (
              <Skeleton className="h-20" />
            ) : (
              <>
                <div className="flex flex-wrap items-center gap-3">
                  <ResolutionBadge status={current.resolution_status} size="lg" />
                  <span className="text-sm text-muted-foreground">
                    confidence <span className="font-medium tabular-nums text-foreground">{pct(current.resolution_confidence)}</span>
                  </span>
                </div>
                <div>
                  <FieldLabel>Summary</FieldLabel>
                  <p className="text-sm leading-6 text-foreground/90">
                    {current.resolution_summary || "No summary recorded — the status has not been verified with sources yet."}
                  </p>
                </div>
              </>
            )}
            <Identity caseData={caseData} />
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Status history</CardTitle>
            {current && <span className="text-xs text-muted-foreground">{current.history.length} change{current.history.length === 1 ? "" : "s"}</span>}
          </CardHeader>
          <CardContent>
            {current ? <StatusTimeline history={current.history} /> : !resolution.error && <TableSkeleton rows={3} cols={2} />}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Monitor checks</CardTitle>
            <span className="text-xs text-muted-foreground">fast signal scans · deep verifications</span>
          </CardHeader>
          <CardContent>
            {checks.data ? (
              <StatusChecks checks={checks.data} />
            ) : checks.error ? (
              <ErrorState message={checks.error} onRetry={checks.refetch} />
            ) : (
              <TableSkeleton rows={3} cols={3} />
            )}
          </CardContent>
        </Card>
      </div>

      <div className="space-y-4">
        <SetStatusForm
          caseId={caseId}
          current={toResolution(current?.resolution_status ?? caseData.resolution_status)}
          onSaved={() => {
            setTick((t) => t + 1);
            onChanged();
          }}
        />
      </div>
    </div>
  );
}

function Identity({ caseData: c }: { caseData: CaseDetail }) {
  const people = c.people ?? [];
  const aliases = c.aliases ?? [];
  return (
    <div className="grid grid-cols-1 gap-3 border-t border-border pt-3 text-sm sm:grid-cols-2">
      <div>
        <FieldLabel>Location</FieldLabel>
        <p>{c.location || "—"}</p>
      </div>
      <div>
        <FieldLabel>Incident · latest development</FieldLabel>
        <p>
          {c.incident_date || "—"} · {c.latest_development_date || "—"}
        </p>
      </div>
      <div>
        <FieldLabel>People</FieldLabel>
        <p>{people.length ? people.join(", ") : "—"}</p>
      </div>
      <div>
        <FieldLabel>Also known as</FieldLabel>
        <p>{aliases.length ? aliases.join(" · ") : "—"}</p>
      </div>
      <div>
        <FieldLabel>Origin</FieldLabel>
        <p>{c.origin ? <Badge variant="outline">{humanize(c.origin)}</Badge> : "—"}</p>
      </div>
    </div>
  );
}

function SetStatusForm({
  caseId,
  current,
  onSaved,
}: {
  caseId: number;
  current: ResolutionStatus;
  onSaved: () => void;
}) {
  const [status, setStatus] = useState<ResolutionStatus | null>(null);
  const [reason, setReason] = useState("");
  const [sources, setSources] = useState("");
  const [summary, setSummary] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<string | null>(null);
  const ids = useId();

  const chosen = status ?? current;
  const urls = sources.split(/\s+/).filter(Boolean);
  const badUrls = urls.filter((u) => !/^https?:\/\//i.test(u));
  const tooShort = reason.trim().length < 3;

  async function submit() {
    setBusy(true);
    setError(null);
    setSaved(null);
    try {
      await api.setCaseResolution(caseId, {
        status: chosen,
        reason: reason.trim(),
        sources: urls,
        summary: summary.trim() || null,
      });
      setSaved(`Status set to ${resolutionLabel(chosen)}.`);
      setReason("");
      setSources("");
      setSummary("");
      setStatus(null);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Set status manually</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <p className="text-xs leading-5 text-muted-foreground">
          Your decision is recorded in the history with your reason and sources. UNSOLVED and under-review cases
          are watched by the monitor; an UNSOLVED film shows a status card and an &ldquo;(Unsolved)&rdquo; YouTube title.
        </p>
        <div>
          <label htmlFor={`${ids}-status`} className="mb-1 block text-xs font-medium text-muted-foreground">
            Status
          </label>
          <Select
            id={`${ids}-status`}
            value={chosen}
            onChange={(e) => setStatus(e.target.value as ResolutionStatus)}
          >
            {RESOLUTION_STATUSES.map((s) => (
              <option key={s} value={s}>
                {resolutionLabel(s)}
                {s === current ? " (current)" : ""}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <label htmlFor={`${ids}-reason`} className="mb-1 block text-xs font-medium text-muted-foreground">
            Reason (required)
          </label>
          <Textarea
            id={`${ids}-reason`}
            rows={3}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="e.g. Court convicted the suspect on 12 March 2025"
          />
        </div>
        <div>
          <label htmlFor={`${ids}-sources`} className="mb-1 block text-xs font-medium text-muted-foreground">
            Sources (one URL per line)
          </label>
          <Textarea
            id={`${ids}-sources`}
            rows={2}
            value={sources}
            onChange={(e) => setSources(e.target.value)}
            placeholder="https://…"
            aria-invalid={badUrls.length > 0 || undefined}
          />
          {badUrls.length > 0 && (
            <p className="mt-1 text-[11px] text-rose-500">Not a URL: {badUrls.join(", ")}</p>
          )}
        </div>
        <div>
          <label htmlFor={`${ids}-summary`} className="mb-1 block text-xs font-medium text-muted-foreground">
            Summary (optional)
          </label>
          <Input
            id={`${ids}-summary`}
            value={summary}
            onChange={(e) => setSummary(e.target.value)}
            placeholder="One sentence: the decisive development"
          />
        </div>
        {error && <p className="text-xs text-rose-500">{error}</p>}
        {saved && <p className="text-xs text-emerald-600 dark:text-emerald-400">{saved}</p>}
        <Button className="w-full" size="sm" onClick={submit} loading={busy} disabled={tooShort || badUrls.length > 0}>
          Save status
        </Button>
      </CardContent>
    </Card>
  );
}
