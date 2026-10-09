"use client";

import { formatTimecode, humanize, langLabel } from "@/lib/format";
import type { CriticReport, CritiqueIssue, CritiqueReport, VisualAuditReport } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { EmptyState } from "@/components/state";
import { cn } from "@/lib/utils";
import { LanguagePicker, type LanguageTabProps, Metric } from "../shared";

const CRITICS: { key: string; label: string; question: string }[] = [
  {
    key: "automation_feel",
    label: "Automation feel",
    question: "Where does the cut feel machine-assembled, formulaic or repetitive?",
  },
  {
    key: "attention",
    label: "Attention",
    question: "Is the viewer overloaded, or left with too little change for too long?",
  },
  {
    key: "visual_accuracy",
    label: "Visual accuracy",
    question: "Could any picture mislead — wrong person or place, or a reveal ahead of the narration?",
  },
  {
    key: "production",
    label: "Production",
    question: "Rhythm, music use, silences, transitions and holds.",
  },
];

const SEVERITY_VARIANT: Record<string, "danger" | "warning" | "outline"> = {
  high: "danger",
  medium: "warning",
  low: "outline",
};

function scoreTone(score: number) {
  if (score >= 80) return "bg-emerald-500";
  if (score >= 60) return "bg-amber-500";
  return "bg-rose-500";
}

export function CritiqueTab({ settings, overview, language, onLanguageChange }: LanguageTabProps) {
  const entry = overview.languages[language] ?? null;
  const critique = entry?.production?.critique ?? null;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <LanguagePicker
          languages={settings.languages}
          value={language}
          onChange={onLanguageChange}
          isAvailable={(l) => overview.languages[l]?.production?.critique != null}
        />
        <p className="text-xs text-muted-foreground">Each language is judged on its own cut — scores are never inherited.</p>
      </div>
      {!entry?.production ? (
        <EmptyState
          title={`No production script in ${langLabel(language)} yet — run a pilot in the Run tab`}
          description="Critics review the production script of each language after it is composed."
        />
      ) : !critique ? (
        <EmptyState
          title="Not reviewed yet"
          description="The critique stage runs right after the production script of this language is composed."
        />
      ) : (
        <>
          {entry.production.audit && <AuditView audit={entry.production.audit} />}
          <CritiqueView critique={critique} />
        </>
      )}
    </div>
  );
}

/** The gate before render: every picture/clip approved for its words. */
function AuditView({ audit }: { audit: VisualAuditReport }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Picture audit</CardTitle>
        <Badge variant={audit.left_out.length ? "warning" : "success"}>
          {audit.approved} approved{audit.left_out.length ? ` · ${audit.left_out.length} left out` : ""}
        </Badge>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-xs text-muted-foreground">
          Every photo and clip was checked against the exact words spoken over it. Rejected ones were replaced (up to{" "}
          {audit.max_redos}×, each replacement checked again); what stayed rejected was left out.
          {audit.symbolic.length > 0 && ` ${audit.symbolic.length} shown with the "symbolic image" label.`}
        </p>
        {audit.replaced.length > 0 && (
          <div>
            <p className="mb-1 text-xs font-medium">Replaced</p>
            <ul className="space-y-1 text-xs">
              {audit.replaced.map((r, i) => (
                <li key={i}>
                  <span className="tabular-nums text-muted-foreground">{formatTimecode(r.at)}</span> {r.from} → {r.to}
                  {r.why[0] && <span className="text-muted-foreground"> — {r.why[0]}</span>}
                </li>
              ))}
            </ul>
          </div>
        )}
        {audit.left_out.length > 0 && (
          <div>
            <p className="mb-1 text-xs font-medium">Left out (no approved picture)</p>
            <ul className="space-y-1 text-xs">
              {audit.left_out.map((r, i) => (
                <li key={i}>
                  <span className="tabular-nums text-muted-foreground">{formatTimecode(r.at)}</span> {r.done}
                  {r.why[0] && <span className="text-muted-foreground"> — {r.why[0]}</span>}
                </li>
              ))}
            </ul>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function CritiqueView({ critique }: { critique: CritiqueReport }) {
  const det = critique.deterministic;
  const after = critique.after_fixes;
  const known = new Set(CRITICS.map((c) => c.key));
  const critics = [
    ...CRITICS.filter((c) => critique.critics[c.key]),
    ...Object.keys(critique.critics)
      .filter((k) => !known.has(k))
      .map((k) => ({ key: k, label: humanize(k), question: "" })),
  ];

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Overall</CardTitle>
          {critique.score != null && (
            <span className="text-2xl font-semibold tabular-nums">{Math.round(critique.score)}</span>
          )}
        </CardHeader>
        <CardContent className="space-y-3">
          {critique.score != null && (
            <Progress value={critique.score} barClassName={scoreTone(critique.score)} />
          )}
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <Metric label="Picture changes / min" value={det.changes_per_minute.toFixed(1)} />
            <Metric label="Music-only share" value={`${Math.round(det.music_only_share * 100)}%`} />
            <Metric label="High-severity issues" value={det.high} tone={det.high ? "danger" : "success"} />
            <Metric label="Fixes applied" value={critique.fixes.length} />
          </div>
          {after && (
            <p className="text-xs text-muted-foreground">
              After fixes: {after.issues.length} deterministic issue{after.issues.length === 1 ? "" : "s"} ·{" "}
              {after.changes_per_minute.toFixed(1)} changes/min · {after.high} high
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Deterministic checks</CardTitle>
          <span className="text-xs text-muted-foreground">rhythm · repetition · holds · reveal · honesty · music</span>
        </CardHeader>
        <CardContent className="p-0">
          <IssueTable issues={det.issues} />
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {critics.map((c) => (
          <CriticCard key={c.key} label={c.label} question={c.question} report={critique.critics[c.key]} />
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Applied fixes</CardTitle>
          <span className="text-xs text-muted-foreground">targeted: one shot at a time, never a full regeneration</span>
        </CardHeader>
        <CardContent>
          {critique.fixes.length === 0 ? (
            <p className="text-sm text-muted-foreground">No fixes were needed.</p>
          ) : (
            <ul className="flex flex-wrap gap-1.5">
              {critique.fixes.map((f, i) => (
                <li key={`${f.shot}-${i}`}>
                  <Badge variant="info">
                    shot {f.shot}: {humanize(f.fix)}
                    {f.to && ` → ${humanize(f.to)}`}
                  </Badge>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function IssueTable({ issues }: { issues: CritiqueIssue[] }) {
  if (issues.length === 0) {
    return <p className="px-4 py-6 text-sm text-emerald-600 dark:text-emerald-400">All checks passed.</p>;
  }
  return (
    <Table className="text-xs">
      <THead>
        <TR className="hover:bg-transparent">
          <TH>Check</TH>
          <TH>Severity</TH>
          <TH>Time</TH>
          <TH>Shot</TH>
          <TH className="min-w-64">Why</TH>
          <TH>Fix</TH>
        </TR>
      </THead>
      <TBody>
        {issues.map((issue, i) => (
          <TR key={i}>
            <TD>{issue.check}</TD>
            <TD>
              <Badge variant={SEVERITY_VARIANT[issue.severity] ?? "outline"}>{issue.severity}</Badge>
            </TD>
            <TD className="tabular-nums">{issue.time ?? "—"}</TD>
            <TD className="tabular-nums">{issue.shot ?? "—"}</TD>
            <TD className="leading-5">{issue.why}</TD>
            <TD>{issue.fix ? humanize(issue.fix) : "—"}</TD>
          </TR>
        ))}
      </TBody>
    </Table>
  );
}

function CriticCard({ label, question, report }: { label: string; question: string; report: CriticReport }) {
  const score = report.score;
  return (
    <Card>
      <CardHeader>
        <CardTitle>{label}</CardTitle>
        <span className={cn("text-lg font-semibold tabular-nums", score == null && "text-muted-foreground")}>
          {score != null ? Math.round(score) : "—"}
        </span>
      </CardHeader>
      <CardContent className="space-y-3">
        {score != null && <Progress value={score} className="h-1" barClassName={scoreTone(score)} />}
        {question && <p className="text-[11px] italic leading-4 text-muted-foreground">{question}</p>}
        {report.summary && <p className="text-sm leading-6">{report.summary}</p>}
        {report.problems.length === 0 ? (
          <p className="text-xs text-muted-foreground">No problems reported.</p>
        ) : (
          <ul className="space-y-2">
            {report.problems.map((p, i) => (
              <li key={i} className="rounded-md border border-border bg-subtle px-3 py-2 text-xs">
                <div className="mb-1 flex flex-wrap items-center gap-1.5">
                  {p.severity && <Badge variant={SEVERITY_VARIANT[p.severity] ?? "outline"}>{p.severity}</Badge>}
                  {p.time && <span className="font-mono">{p.time}</span>}
                  {p.shot != null && <span className="text-muted-foreground">shot {p.shot}</span>}
                  {p.beat_id && <span className="font-mono text-muted-foreground">{p.beat_id}</span>}
                </div>
                {p.why && <p className="leading-5">{p.why}</p>}
                {p.fix && (
                  <p className="mt-1 text-muted-foreground">
                    Fix: <span className="text-foreground">{humanize(p.fix)}</span>
                    {p.fix_detail && ` — ${p.fix_detail}`}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
        {report.model && <p className="font-mono text-[10px] text-muted-foreground">{report.model}</p>}
      </CardContent>
    </Card>
  );
}
