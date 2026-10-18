"use client";

import type { AuditItem, DraftChecks, EvidenceAuditReport } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const eur = (v: number | null | undefined) => (v == null ? "—" : `€${v.toFixed(2)}`);
const n = (v: number | null | undefined) => (v == null ? "—" : v.toLocaleString());
const SEVERITIES = ["high", "medium", "low"] as const;

function variant(severity: string): "danger" | "warning" | "default" {
  return severity === "high" ? "danger" : severity === "medium" ? "warning" : "default";
}

function Item({ item }: { item: AuditItem }) {
  return (
    <div className="mt-1 rounded border border-border p-2 text-xs" data-testid="audit-item">
      <Badge variant={variant(item.severity)}>{item.severity}</Badge>{" "}
      <span className="text-muted-foreground">{item.kind} · {item.act || "whole script"}</span>
      <p className="mt-1 italic">“{item.quote}”</p>
      <p className="mt-1"><b>Claim:</b> {item.claim}</p>
      <div className="mt-1">
        <b>Supplied evidence:</b>{" "}
        {item.evidence_facts.length === 0 ? (
          <span className="text-muted-foreground">none cited</span>
        ) : (
          <ul className="ml-3 list-disc">
            {item.evidence_facts.map((f) => (
              <li key={f.id}><b>{f.id}</b> {f.text ?? "(not in the evidence)"}</li>
            ))}
          </ul>
        )}
      </div>
      <p className="mt-1"><b>The material says:</b> {item.evidence}</p>
      <p className="mt-1">{item.problem}</p>
      <p className="mt-1 text-muted-foreground">Minimal correction: {item.correction}</p>
    </div>
  );
}

export function AuditPanel({ audit }: { audit: EvidenceAuditReport | null }) {
  return (
    <div className="space-y-2 rounded-md border border-border p-3" data-testid="audit-model">
      <div className="flex flex-wrap items-center gap-2 text-sm font-medium">
        Evidence audit
        <Badge variant="info">model: {audit?.models?.join(", ") || "gpt-6.1-sol"}</Badge>
        {audit && (
          <Badge variant={audit.status === "ok" ? (audit.verdict === "pass" ? "success" : "warning") : "danger"}>
            {audit.status === "ok" ? audit.verdict : audit.status}
          </Badge>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        Report only, nothing was changed. A pass means only that the auditor found no unsupported claim in the
        material it was given; it does not show the draft is correct.
      </p>
      {!audit ? (
        <p className="text-xs">Not run yet. Running the pipeline audits the draft once, after it is written.</p>
      ) : (
        <>
          {audit.error && <p className="text-xs text-destructive">{audit.error}</p>}
          {audit.usage && (
            <p className="text-xs text-muted-foreground">
              {audit.usage.calls} calls · {n(audit.usage.tokens_in)} tokens in, {n(audit.usage.tokens_cached)} cached ·{" "}
              {n(audit.usage.tokens_reasoning)} reasoning · {n(audit.usage.tokens_out)} out · {eur(audit.usage.cost_eur)}
            </p>
          )}
          {audit.counts && (
            <p className="text-xs" data-testid="audit-counts">
              {SEVERITIES.map((s) => `${s} ${audit.counts?.severity[s] ?? 0}`).join(" · ")}
              {Object.keys(audit.counts.kind).length > 0 && " — "}
              {Object.entries(audit.counts.kind).map(([k, v]) => `${k} ${v}`).join(" · ")}
            </p>
          )}
          {audit.summary && <p className="text-xs">{audit.summary}</p>}
          {audit.chunks?.filter((c) => c.status === "failed").map((c) => (
            <p key={c.acts.join()} className="text-xs text-destructive">
              Failed (acts {c.acts.join(", ")}): {c.error}
            </p>
          ))}
          {SEVERITIES.map((severity) => {
            const items = (audit.items ?? []).filter((i) => i.severity === severity);
            return items.length === 0 ? null : (
              <div key={severity}>
                <p className="text-xs font-medium">{severity} ({items.length})</p>
                {items.map((i) => <Item key={i.id} item={i} />)}
              </div>
            );
          })}
        </>
      )}
    </div>
  );
}

function NumbersPanel({ checks }: { checks: DraftChecks | null }) {
  const report = checks?.numbers_and_names;
  return (
    <div className="space-y-1 rounded-md border border-border p-3 text-xs" data-testid="audit-numbers">
      <div className="flex flex-wrap items-center gap-2 text-sm font-medium">
        Numbers and names <Badge>no model</Badge>
      </div>
      <p className="text-muted-foreground">Computed by code: figures and names the draft states that no fact, excerpt or source title does.</p>
      {!report ? <p>Not computed yet.</p> : (
        <>
          <p><b>Figures not in the evidence ({report.unsupported_figures.length})</b></p>
          {report.unsupported_figures.map((f) => <p key={f.figure} className="ml-2">{f.figure} · …{f.context}…</p>)}
          <p><b>Names not in the evidence ({report.names_not_in_evidence.length})</b></p>
          {report.names_not_in_evidence.length > 0 && <p className="ml-2">{report.names_not_in_evidence.join(", ")}</p>}
        </>
      )}
    </div>
  );
}

function CoveragePanel({ checks }: { checks: DraftChecks | null }) {
  const report = checks?.coverage;
  return (
    <div className="space-y-1 rounded-md border border-border p-3 text-xs" data-testid="audit-coverage">
      <div className="flex flex-wrap items-center gap-2 text-sm font-medium">
        Coverage <Badge>no language model</Badge>
      </div>
      <p className="text-muted-foreground">Embedding similarity only: which facts of the evidence pack the draft tells.</p>
      {!report ? <p>Not computed yet.</p> : report.skipped ? <p>Skipped: {report.skipped}.</p> : (
        <>
          <p>{report.told} of {report.facts_in_pack} facts told.</p>
          {(report.key_facts_not_told?.length ?? 0) > 0 && (
            <p><b>Key facts not told:</b> {report.key_facts_not_told?.join(", ")}</p>
          )}
        </>
      )}
    </div>
  );
}

export function EvidenceAudit({ audit, checks }: { audit: EvidenceAuditReport | null; checks: DraftChecks | null }) {
  return (
    <Card data-testid="evidence-audit">
      <CardHeader>
        <CardTitle>Checks of the English draft</CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-1 gap-3 text-sm xl:grid-cols-3">
        <AuditPanel audit={audit} />
        <NumbersPanel checks={checks} />
        <CoveragePanel checks={checks} />
      </CardContent>
    </Card>
  );
}
