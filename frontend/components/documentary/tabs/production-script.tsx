"use client";

import { FileJson } from "lucide-react";
import { formatDateTime, formatTimecode, humanize, langLabel } from "@/lib/format";
import type { Production } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { TableSkeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { EmptyState, ErrorState } from "@/components/state";
import { ResolutionBadge } from "@/components/status-badge";
import { ProductionTypeBadge, openingLabel } from "@/components/lifecycle/shared";
import { useProduction } from "../hooks";
import { LangText, LanguagePicker, type LanguageTabProps, Metric } from "../shared";

function downloadJson(production: Production) {
  const blob = new Blob([JSON.stringify(production.script, null, 2)], { type: "application/json" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `production_${production.language}_v${production.version}.json`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function ProductionScriptTab({
  caseId,
  settings,
  overview,
  refreshKey,
  language,
  onLanguageChange,
  masterId,
}: LanguageTabProps) {
  const expected = overview.languages[language]?.production ?? null;
  const { data, error, loading, refetch } = useProduction(caseId, language, masterId, expected != null, refreshKey);
  const production = data && expected && data.id === expected.id ? data : null;

  return (
    <div className="space-y-4">
      <LanguagePicker
        languages={settings.languages}
        value={language}
        onChange={onLanguageChange}
        isAvailable={(l) => overview.languages[l]?.production != null}
      />
      {!expected ? (
        <EmptyState
          title={`No production script in ${langLabel(language)} yet — run a pilot in the Run tab`}
          description="The production script is the render-ready timeline: every shot, on-screen text, music cue and subtitle with exact times from the real narration."
        />
      ) : production ? (
        <ScriptView production={production} />
      ) : error && !loading ? (
        <ErrorState message={error} onRetry={refetch} />
      ) : (
        <TableSkeleton rows={8} cols={6} />
      )}
    </div>
  );
}

function ScriptView({ production }: { production: Production }) {
  const script = production.script;
  const shots = script.shots ?? [];
  const overlays = script.overlays ?? [];
  const credits = script.credits ?? [];
  const lang = script.language ?? production.language;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader className="flex-wrap gap-2">
          <CardTitle className="flex items-center gap-2">
            Production v{production.version} · {langLabel(production.language)}
            <Badge variant="outline">{production.mode}</Badge>
            <Badge variant={production.status === "rendered" ? "success" : "info"}>{humanize(production.status)}</Badge>
            <ProductionTypeBadge type={script.production_type} />
          </CardTitle>
          <Button variant="secondary" size="sm" onClick={() => downloadJson(production)}>
            <FileJson className="size-3.5" /> Download JSON
          </Button>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
            <Metric label="Duration" value={formatTimecode(script.duration ?? production.duration_seconds)} />
            <Metric label="Resolution" value={script.width && script.height ? `${script.width}×${script.height}` : "—"} />
            <Metric label="Frame rate" value={script.fps ? `${script.fps} fps` : "—"} />
            <Metric label="Shots" value={shots.length} />
            <Metric label="Overlays" value={overlays.length} />
            <Metric label="Subtitles" value={(script.subtitles ?? []).length} />
          </div>
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-md border border-border px-3 py-2 text-xs">
            <span className="inline-flex items-center gap-1.5">
              <span className="text-muted-foreground">Case status in this film</span>
              {script.case_status ? (
                <ResolutionBadge status={script.case_status} />
              ) : (
                <span className="text-muted-foreground">not recorded (older script)</span>
              )}
            </span>
            <span>
              <span className="text-muted-foreground">Opening strategy </span>
              <span className="font-medium">{openingLabel(script.opening_strategy)}</span>
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="text-muted-foreground">Production</span>
              <ProductionTypeBadge type={script.production_type ?? "original"} showOriginal />
            </span>
          </div>
          {script.case_status === "UNSOLVED" && script.production_type !== "follow_up" && (
            <p className="text-xs leading-5 text-muted-foreground">
              An unsolved case: the film shows the localized &ldquo;UNSOLVED CASE&rdquo; status card in the opening and
              near the end, and its YouTube title starts with &ldquo;UNSOLVED:&rdquo;.
            </p>
          )}
          {script.production_type === "follow_up" && (
            <p className="text-xs leading-5 text-muted-foreground">
              A follow-up: the film opens with the earlier episode and a &ldquo;case now solved&rdquo; status card.
            </p>
          )}
          <p className="text-xs text-muted-foreground">Composed {formatDateTime(production.created_at)}</p>
          <div>
            <p className="mb-1 text-xs font-medium text-muted-foreground">Credits</p>
            {credits.length ? (
              <ul className="space-y-0.5 text-xs">
                {credits.map((c) => (
                  <li key={c}>{c}</li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-muted-foreground">No attribution required.</p>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Shots</CardTitle>
          <span className="text-xs text-muted-foreground">{shots.length}</span>
        </CardHeader>
        <CardContent className="p-0">
          {shots.length === 0 ? (
            <p className="px-4 py-6 text-sm text-muted-foreground">No shots in this script.</p>
          ) : (
            <Table className="text-xs">
              <THead>
                <TR className="hover:bg-transparent">
                  <TH className="text-right">#</TH>
                  <TH>Beat</TH>
                  <TH>Start–end</TH>
                  <TH>Command</TH>
                  <TH>Kind</TH>
                  <TH>Asset</TH>
                  <TH>Motion</TH>
                  <TH>Transition</TH>
                </TR>
              </THead>
              <TBody>
                {shots.map((s) => (
                  <TR key={s.index}>
                    <TD className="text-right tabular-nums text-muted-foreground">{s.index}</TD>
                    <TD className="font-mono">{s.beat_id}</TD>
                    <TD className="whitespace-nowrap tabular-nums">
                      {formatTimecode(s.start, { tenths: true })}–{formatTimecode(s.end, { tenths: true })}
                    </TD>
                    <TD className="whitespace-nowrap">{humanize(s.command)}</TD>
                    <TD>{s.kind ?? "—"}</TD>
                    <TD className="font-mono">{s.asset_id ?? "—"}</TD>
                    <TD className="whitespace-nowrap">{humanize(s.motion)}</TD>
                    <TD className="whitespace-nowrap">{humanize(s.transition_in)}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>On-screen text</CardTitle>
          <span className="text-xs text-muted-foreground">{overlays.length}</span>
        </CardHeader>
        <CardContent className="p-0">
          {overlays.length === 0 ? (
            <p className="px-4 py-6 text-sm text-muted-foreground">No on-screen text in this script.</p>
          ) : (
            <Table className="text-xs">
              <THead>
                <TR className="hover:bg-transparent">
                  <TH>Kind</TH>
                  <TH className="min-w-64">Text</TH>
                  <TH>Start</TH>
                  <TH>End</TH>
                </TR>
              </THead>
              <TBody>
                {overlays.map((o, i) => (
                  <TR key={`${o.start}-${i}`}>
                    <TD>
                      <Badge variant="outline">{o.kind}</Badge>
                    </TD>
                    <TD>
                      {o.kind === "credit" ? (
                        <span dir="auto">{o.text}</span>
                      ) : (
                        <LangText lang={lang}>{o.text}</LangText>
                      )}
                    </TD>
                    <TD className="whitespace-nowrap tabular-nums">{formatTimecode(o.start, { tenths: true })}</TD>
                    <TD className="whitespace-nowrap tabular-nums">{formatTimecode(o.end, { tenths: true })}</TD>
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
