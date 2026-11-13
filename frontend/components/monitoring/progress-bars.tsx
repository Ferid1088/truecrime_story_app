"use client";

import { useState } from "react";
import Link from "next/link";
import { AlertTriangle, Check } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { duration } from "@/components/documentary/monitoring-parts";
import { LANGUAGES } from "@/lib/format";
import type { MonitoringJob, MonitoringPhase } from "@/lib/types";

/** The colour of the filled part of a segment. */
const FILL: Record<MonitoringPhase["state"], string> = {
  pending: "bg-muted-foreground/40",
  waiting: "bg-sky-500",
  running: "bg-sky-500 animate-pulse",
  done: "bg-emerald-500",
  degraded: "bg-amber-500",
  failed: "bg-rose-500",
  blocked: "bg-rose-500/70",
};

/** How much of a phase is finished, 0 to 1: its finished steps over its steps (a phase the job does not plan has none). */
function fraction(p: MonitoringPhase): number {
  return p.total ? Math.min(p.done / p.total, 1) : 0;
}

const STATE_WORD: Record<MonitoringPhase["state"], string> = {
  pending: "not started",
  waiting: "waiting",
  running: "running",
  done: "done",
  degraded: "done with a warning",
  failed: "failed",
  blocked: "blocked",
};

type Selected = { row: string; phase: MonitoringPhase } | null;

function Bar({ row, phases, selected, onSelect }: { row: string; phases: MonitoringPhase[]; selected: Selected; onSelect: (s: Selected) => void }) {
  return (
    <div className="flex flex-1 gap-1" role="group" aria-label={`${row} progress`}>
      {phases.map((p) => {
        const active = selected?.row === row && selected.phase.key === p.key;
        const percent = Math.round(fraction(p) * 100);
        return (
          <button
            key={p.key}
            type="button"
            disabled={p.total === 0}
            onClick={() => onSelect(active ? null : { row, phase: p })}
            title={`${p.label}: ${STATE_WORD[p.state]}${p.total ? ` - ${percent} % (${p.done} of ${p.total} steps)` : ""}`}
            aria-label={`${row} ${p.label}: ${percent} %, ${STATE_WORD[p.state]}`}
            data-state={p.state}
            data-percent={percent}
            className={`relative h-4 min-w-0 flex-1 overflow-hidden rounded-sm bg-muted-foreground/15 outline-offset-2 disabled:cursor-default ${p.total === 0 ? "opacity-40" : ""} ${active ? "outline outline-2 outline-foreground" : ""}`}
          >
            <span className={`absolute inset-y-0 left-0 transition-[width] duration-500 ${FILL[p.state]}`} style={{ width: `${percent}%` }} />
          </button>
        );
      })}
    </div>
  );
}

/** The share of a language's phases that are finished (every phase counts the same). */
function overall(phases: MonitoringPhase[]): number {
  return Math.round((phases.reduce((sum, p) => sum + fraction(p), 0) / Math.max(phases.length, 1)) * 100);
}

function Detail({ phase }: { phase: MonitoringPhase }) {
  return (
    <div className="mt-2 rounded-md border border-border bg-muted/30 p-3 text-xs" data-testid="progress-detail">
      <p className="mb-2 font-medium">{phase.label} - {STATE_WORD[phase.state]}</p>
      <ul className="space-y-1">
        {phase.stages.map((s) => (
          <li key={s.name} className="flex flex-wrap justify-between gap-2">
            <span className="font-mono">{s.name}</span>
            <span className="tabular-nums text-muted-foreground">
              {s.status}{s.attempts > 1 ? ` · ${s.attempts} tries` : ""}{s.seconds != null ? ` · ${duration(s.seconds)}` : ""}
            </span>
            {s.error && <span className="w-full text-rose-700">{s.error}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Legend({ phases }: { phases: MonitoringPhase[] }) {
  return (
    <div className="flex gap-1 pl-[7.5rem] pr-[9rem] text-[10px] text-muted-foreground">
      {phases.map((p) => <span key={p.key} className="min-w-0 flex-1 truncate">{p.label}</span>)}
    </div>
  );
}

function CaseProgress({ job, title }: { job: MonitoringJob; title: string }) {
  const [selected, setSelected] = useState<Selected>(null);
  const progress = job.phase_bars;
  const running = job.status === "running" || job.status === "queued" || job.status === "cancelling";
  const labelOf = (code: string) => LANGUAGES[code] ?? code.toUpperCase();
  return (
    <div className="space-y-2 border-b border-border pb-4 last:border-0 last:pb-0" data-testid="progress-case">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Link href={`/documentary/${job.case_id}?tab=Monitoring`} className="font-medium hover:underline">{title}</Link>
          <Badge variant={running ? "info" : job.status === "failed" ? "danger" : "default"}>{running ? job.status : `last job ${job.status}`}</Badge>
        </div>
        <span className="text-xs tabular-nums text-muted-foreground">
          Job #{job.id} · {duration(job.age_s)}
        </span>
      </div>
      <Legend phases={progress.shared.phases} />
      <div className="flex items-center gap-2">
        <span className="w-28 shrink-0 text-xs text-muted-foreground">Shared</span>
        <Bar row="Shared" phases={progress.shared.phases} selected={selected} onSelect={setSelected} />
        <span className="w-36 shrink-0" />
      </div>
      {progress.languages[0] && <Legend phases={progress.languages[0].phases} />}
      {progress.languages.map((r) => (
        <div key={r.language} className="flex items-center gap-2">
          <span className="w-28 shrink-0 truncate text-xs font-medium">{labelOf(r.language)}</span>
          <Bar row={r.language} phases={r.phases} selected={selected} onSelect={setSelected} />
          <span className="w-36 shrink-0 text-right text-xs text-muted-foreground">
            <span className="mr-1 tabular-nums font-medium text-foreground">{overall(r.phases)} %</span>
            {r.finished ? <span className="inline-flex items-center gap-1 text-emerald-600"><Check className="size-3" /></span>
              : r.stuck ? <span className="inline-flex items-center gap-1 text-rose-600"><AlertTriangle className="size-3" /> slow</span>
              : r.current ?? "-"}
          </span>
        </div>
      ))}
      {selected && <Detail phase={selected.phase} />}
    </div>
  );
}

export function ProgressCard({ jobs }: { jobs: MonitoringJob[] }) {
  return (
    <Card>
      <CardHeader><CardTitle>Progress by language</CardTitle></CardHeader>
      <CardContent className="space-y-4" data-testid="monitoring-progress">
        {jobs.length === 0 && <p className="text-xs text-muted-foreground">Nothing is running and nothing stopped lately.</p>}
        {jobs.map((j) => <CaseProgress key={j.id} job={j} title={j.case_title ?? `Case ${j.case_id}`} />)}
      </CardContent>
    </Card>
  );
}
