"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { api } from "@/lib/api";
import { formatDateTime, humanize, isRtl, langLabel } from "@/lib/format";
import { useApi } from "@/lib/hooks";
import type { AudioPlanBeat, BlueprintQuestion, ChapterPlanRecord, ValidationIssue } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { EmptyState } from "@/components/state";
import { type DocumentaryTabProps, LevelBadge } from "../shared";

const QUESTION_STATUS: Record<BlueprintQuestion["status"], "success" | "warning" | "info" | "outline"> = {
  answered: "success",
  unresolved: "warning",
  open: "info",
  never_opened: "outline",
};

const BLUEPRINT_STATUS: Record<string, "success" | "warning" | "danger"> = {
  valid: "success",
  needs_review: "warning",
  invalid: "danger",
};

function Ids({ ids }: { ids: string[] | undefined }) {
  if (!ids?.length) return <span className="text-muted-foreground">—</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {ids.map((id) => (
        <span key={id} className="rounded bg-muted px-1 font-mono text-[10px]">
          {id}
        </span>
      ))}
    </span>
  );
}

function AfterCell({ beat }: { beat: AudioPlanBeat | undefined }) {
  if (!beat) return <span className="text-muted-foreground">—</span>;
  const { type, seconds, mood } = beat.after;
  return (
    <span className="whitespace-nowrap">
      {humanize(type)}
      {seconds ? <span className="tabular-nums text-muted-foreground"> {seconds.toFixed(1)}s</span> : null}
      {mood && type !== "breath" && type !== "end" && (
        <span className="text-muted-foreground"> · {mood}</span>
      )}
    </span>
  );
}

function IssueList({ title, issues, tone }: { title: string; issues: ValidationIssue[]; tone: "danger" | "warning" }) {
  const [open, setOpen] = useState(tone === "danger");
  if (!issues.length) return null;
  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex cursor-pointer items-center gap-1.5 text-xs font-medium text-muted-foreground hover:text-foreground"
      >
        {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        <Badge variant={tone}>{issues.length}</Badge> {title}
      </button>
      {open && (
        <ul className="mt-1.5 space-y-1 text-[11px] leading-4">
          {issues.map((issue, i) => {
            const { code, ...rest } = issue;
            const extra = Object.entries(rest)
              .map(([k, v]) => `${humanize(k)} ${Array.isArray(v) ? v.join(", ") : typeof v === "object" ? JSON.stringify(v) : String(v)}`)
              .join(" · ");
            return (
              <li key={i} className="rounded border border-border bg-subtle px-2 py-1">
                <span className="font-medium">{humanize(code)}</span>
                {extra && <span className="text-muted-foreground"> — {extra}</span>}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

const CHAPTER_STATUS: Record<ChapterPlanRecord["status"], "success" | "warning" | "danger"> = {
  approved: "success",
  partial: "warning",
  no_texts: "danger",
};

/** Chapter titles, the film title and the timeline labels in every
 * language — each approved by the chapter auditor; what it still
 * rejected after the redos is listed (the card then shows only the
 * chapter number / the date). */
function ChaptersCard({ caseId, versionId, refreshKey }: { caseId: number; versionId: number | null; refreshKey: number }) {
  const { data } = useApi(() => api.chapters(caseId, versionId), [caseId, versionId, refreshKey]);
  if (!data) return null;
  const langs = data.languages;
  const text = (t: Record<string, string> | undefined, lang: string) => t?.[lang];
  const cell = (t: Record<string, string> | undefined, lang: string, fallback: string) => {
    const v = text(t, lang);
    return v ? (
      <span dir={isRtl(lang) ? "rtl" : undefined}>{v}</span>
    ) : (
      <span className="text-muted-foreground" title="left out — the card shows only this">
        {fallback}
      </span>
    );
  };
  const rounds = data.audit.rounds?.length ?? 0;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Chapters &amp; timeline</CardTitle>
        <Badge variant={CHAPTER_STATUS[data.status] ?? "outline"}>{humanize(data.status)}</Badge>
      </CardHeader>
      <CardContent className="space-y-3 text-sm">
        <p className="text-xs text-muted-foreground">
          Every text approved by the chapter auditor in every language ({rounds} round{rounds === 1 ? "" : "s"}, at most{" "}
          {data.audit.max_redos} redos). A chapter card sits at the end of the chapter break before its act
          {data.plan.cold_open ? "; after the cold open the film's title comes first" : ""}. Timeline cards show only the
          dates the story has already told.
        </p>
        <Table>
          <THead>
            <TR>
              <TH>Card</TH>
              {langs.map((l) => (
                <TH key={l}>{langLabel(l)}</TH>
              ))}
            </TR>
          </THead>
          <TBody>
            {data.plan.cold_open && (
              <TR>
                <TD className="whitespace-nowrap text-muted-foreground">Film title</TD>
                {langs.map((l) => (
                  <TD key={l}>{cell(data.plan.film_title, l, "—")}</TD>
                ))}
              </TR>
            )}
            {data.plan.chapters.map((c) => (
              <TR key={c.act_id}>
                <TD className="whitespace-nowrap text-muted-foreground">
                  Chapter {c.number} <span className="font-mono text-[10px]">{c.first_beat}</span>
                </TD>
                {langs.map((l) => (
                  <TD key={l}>{cell(c.title, l, "number only")}</TD>
                ))}
              </TR>
            ))}
            {data.plan.events.map((e) => (
              <TR key={e.id}>
                <TD className="whitespace-nowrap text-muted-foreground">
                  {e.date} <span className="font-mono text-[10px]">from {e.first_beat}</span>
                </TD>
                {langs.map((l) => (
                  <TD key={l}>{cell(e.label, l, "date only")}</TD>
                ))}
              </TR>
            ))}
          </TBody>
        </Table>
        {data.audit.left_out.length > 0 && (
          <div className="rounded-md border border-amber-500/40 bg-amber-500/5 p-2.5 text-xs">
            <p className="mb-1 font-medium">Left out (still rejected after the redos)</p>
            <ul className="list-disc space-y-0.5 pl-4">
              {data.audit.left_out.map((x) => (
                <li key={x.key}>
                  <span className="font-mono">{x.key}</span> ({x.languages.map(langLabel).join(", ")}): {x.reason}
                </li>
              ))}
            </ul>
          </div>
        )}
        {data.audit.error && <p className="text-xs text-destructive">{data.audit.error}</p>}
      </CardContent>
    </Card>
  );
}

export function BlueprintTab({ caseId, overview, refreshKey }: DocumentaryTabProps) {
  const record = overview.blueprint;
  if (!record) {
    return (
      <EmptyState
        title="No blueprint yet"
        description="The editorial blueprint is the first stage of every run: it divides the master story into beats with a job in the film. Start a pilot in the Run tab."
      />
    );
  }
  const bp = record.blueprint;
  const beats = bp.beats ?? [];
  const questions = bp.questions ?? [];
  const audioPlan = overview.audio_plan;
  const audioByBeat = new Map((audioPlan?.plan.beats ?? []).map((b) => [b.beat_id, b]));
  const arcs = Object.entries(bp.arcs ?? {}).filter(([, v]) => v);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_300px]">
        <Card>
          <CardHeader>
            <CardTitle>Editorial strategy</CardTitle>
            <Badge variant={BLUEPRINT_STATUS[record.status] ?? "outline"}>{humanize(record.status)}</Badge>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <Field label="Central question" value={bp.central_question} emphasis />
            <Field label="Editorial thesis" value={bp.editorial_thesis} />
            <Field label="Human thread" value={bp.human_thread} />
            {arcs.length > 0 && (
              <div>
                <p className="mb-1 text-xs font-medium text-muted-foreground">Arcs</p>
                <dl className="space-y-1 text-xs leading-5">
                  {arcs.map(([k, v]) => (
                    <div key={k} className="flex gap-2">
                      <dt className="w-24 shrink-0 capitalize text-muted-foreground">{k}</dt>
                      <dd dir="auto">{v}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="h-fit">
          <CardHeader>
            <CardTitle>Blueprint v{record.version}</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-xs">
            <dl className="grid grid-cols-2 gap-y-1.5">
              <dt className="text-muted-foreground">Beats</dt>
              <dd className="tabular-nums">{beats.length}</dd>
              <dt className="text-muted-foreground">Listener questions</dt>
              <dd className="tabular-nums">{questions.length}</dd>
              <dt className="text-muted-foreground">Words</dt>
              <dd className="tabular-nums">{beats.reduce((n, b) => n + (b.words ?? 0), 0).toLocaleString()}</dd>
              <dt className="text-muted-foreground">Created</dt>
              <dd>{formatDateTime(record.created_at)}</dd>
              {record.generation_model && (
                <>
                  <dt className="text-muted-foreground">Model</dt>
                  <dd className="truncate font-mono text-[10px]" title={record.generation_model}>
                    {record.generation_model}
                  </dd>
                </>
              )}
              <dt className="text-muted-foreground">Audio plan</dt>
              <dd>{audioPlan ? `v${audioPlan.version} · ${humanize(audioPlan.status)}` : "not yet"}</dd>
            </dl>
            <IssueList title="structural errors" issues={record.validation.errors ?? []} tone="danger" />
            <IssueList title="warnings for review" issues={record.validation.warnings ?? []} tone="warning" />
          </CardContent>
        </Card>
      </div>

      <ChaptersCard caseId={caseId} versionId={overview.master_version_id ?? null} refreshKey={refreshKey} />

      <Card>
        <CardHeader>
          <CardTitle>Beats</CardTitle>
          <span className="text-xs text-muted-foreground">
            Bed and After come from the audio director&apos;s plan
          </span>
        </CardHeader>
        <CardContent className="p-0">
          {beats.length === 0 ? (
            <p className="px-4 py-6 text-sm text-muted-foreground">The blueprint has no beats.</p>
          ) : (
            <Table className="text-xs">
              <THead>
                <TR className="hover:bg-transparent">
                  <TH>Beat</TH>
                  <TH>Purpose</TH>
                  <TH className="min-w-64">Summary</TH>
                  <TH>Emotion</TH>
                  <TH>Density</TH>
                  <TH>Attention</TH>
                  <TH>Visual</TH>
                  <TH>Audio</TH>
                  <TH>Bed</TH>
                  <TH>After</TH>
                  <TH>Reveals</TH>
                  <TH>Open questions</TH>
                </TR>
              </THead>
              <TBody>
                {beats.map((b) => {
                  const audio = audioByBeat.get(b.id);
                  return (
                    <TR key={b.id}>
                      <TD className="whitespace-nowrap align-top">
                        <span className="font-mono font-medium">{b.id}</span>
                        <span className="block text-[10px] text-muted-foreground">
                          {b.act_id} ¶{b.paragraphs[0]}
                          {b.paragraphs[1] !== b.paragraphs[0] && `–${b.paragraphs[1]}`} · {b.words}w
                        </span>
                      </TD>
                      <TD className="whitespace-nowrap align-top">{humanize(b.purpose)}</TD>
                      <TD className="align-top leading-5" dir="auto">
                        {b.summary || "—"}
                        {b.human_focus && (
                          <span className="block text-[10px] text-muted-foreground">focus: {b.human_focus}</span>
                        )}
                      </TD>
                      <TD className="align-top">
                        <LevelBadge level={b.emotional_load} />
                      </TD>
                      <TD className="align-top">
                        <LevelBadge level={b.information_density} />
                      </TD>
                      <TD className="whitespace-nowrap align-top">{humanize(b.attention)}</TD>
                      <TD className="whitespace-nowrap align-top">{humanize(b.visual_intent)}</TD>
                      <TD className="whitespace-nowrap align-top">{humanize(b.audio_intent)}</TD>
                      <TD className="whitespace-nowrap align-top">
                        {audio ? (
                          audio.bed === "none" ? (
                            <span className="text-muted-foreground">none</span>
                          ) : (
                            <>
                              {audio.bed}
                              <span className="text-muted-foreground"> · {humanize(audio.bed_level)}</span>
                            </>
                          )
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </TD>
                      <TD className="align-top">
                        <AfterCell beat={audio} />
                      </TD>
                      <TD className="align-top">
                        <Ids ids={b.reveals} />
                      </TD>
                      <TD className="align-top">
                        <Ids ids={b.open_questions} />
                      </TD>
                    </TR>
                  );
                })}
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Listener questions</CardTitle>
          <span className="text-xs text-muted-foreground">
            {questions.filter((q) => q.status === "answered").length} answered ·{" "}
            {questions.filter((q) => q.status === "unresolved").length} honestly unresolved
          </span>
        </CardHeader>
        <CardContent className="p-0">
          {questions.length === 0 ? (
            <p className="px-4 py-6 text-sm text-muted-foreground">No listener questions recorded.</p>
          ) : (
            <Table className="text-xs">
              <THead>
                <TR className="hover:bg-transparent">
                  <TH>Id</TH>
                  <TH className="min-w-64">Question</TH>
                  <TH>Kind</TH>
                  <TH>Opened</TH>
                  <TH>Resolved</TH>
                  <TH>Status</TH>
                </TR>
              </THead>
              <TBody>
                {questions.map((q) => (
                  <TR key={q.id}>
                    <TD className="font-mono">{q.id}</TD>
                    <TD dir="auto" className="leading-5">
                      {q.question}
                    </TD>
                    <TD>{q.kind}</TD>
                    <TD className="font-mono">{q.opened_in ?? "—"}</TD>
                    <TD className="font-mono">{q.resolved_in ?? "—"}</TD>
                    <TD>
                      <Badge variant={QUESTION_STATUS[q.status] ?? "outline"}>{humanize(q.status)}</Badge>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Field({ label, value, emphasis }: { label: string; value: string | undefined; emphasis?: boolean }) {
  return (
    <div>
      <p className="mb-0.5 text-xs font-medium text-muted-foreground">{label}</p>
      {value ? (
        <p className={emphasis ? "font-medium leading-6" : "leading-6 text-foreground/90"} dir="auto">
          {value}
        </p>
      ) : (
        <p className="text-muted-foreground">—</p>
      )}
    </div>
  );
}
