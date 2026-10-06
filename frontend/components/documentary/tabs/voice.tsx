"use client";

import { useState } from "react";
import { api, apiFileUrl, nullIfNotFound } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatTimecode, humanize, langLabel } from "@/lib/format";
import type { VoiceManifest } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton, TableSkeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { EmptyState, ErrorState } from "@/components/state";
import { InlineAlert, LanguagePicker, type LanguageTabProps, Metric } from "../shared";

export function VoiceTab({ caseId, settings, overview, refreshKey, language, onLanguageChange }: LanguageTabProps) {
  const versionId = overview.languages[language]?.version_id ?? null;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Narrator voices</CardTitle>
          <span className="text-xs text-muted-foreground">from configuration</span>
        </CardHeader>
        <CardContent className="p-0">
          <Table className="text-xs">
            <THead>
              <TR className="hover:bg-transparent">
                <TH>Language</TH>
                <TH>Voice id</TH>
                <TH>Model</TH>
                <TH>Language code</TH>
                <TH>Audio tags</TH>
                <TH>Reads</TH>
              </TR>
            </THead>
            <TBody>
              {settings.languages.map((l) => {
                const v = settings.voices[l];
                return (
                  <TR key={l}>
                    <TD>{langLabel(l)}</TD>
                    <TD className="font-mono">
                      {v?.voice_id ?? <Badge variant="warning">not configured</Badge>}
                    </TD>
                    <TD className="font-mono text-muted-foreground">{v?.model_id ?? "—"}</TD>
                    <TD className="font-mono text-muted-foreground">{v?.language_code ?? "—"}</TD>
                    <TD>
                      {v?.audio_tags ? (
                        <Badge variant="success">performed</Badge>
                      ) : (
                        <span className="text-muted-foreground">no</span>
                      )}
                    </TD>
                    <TD className="text-muted-foreground">
                      {settings.speech_script[l] === "finglish" ? "Finglish" : "native script"}
                    </TD>
                  </TR>
                );
              })}
            </TBody>
          </Table>
        </CardContent>
      </Card>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <LanguagePicker
          languages={settings.languages}
          value={language}
          onChange={onLanguageChange}
          isAvailable={(l) => overview.languages[l] != null}
        />
        <p className="text-xs text-muted-foreground">Narration of the latest spoken version</p>
      </div>

      {versionId == null ? (
        <EmptyState
          title={`No spoken version in ${langLabel(language)} yet`}
          description="Run a pilot in the Run tab — the voice stage narrates every selected language."
        />
      ) : (
        <Narration caseId={caseId} versionId={versionId} refreshKey={refreshKey} />
      )}
    </div>
  );
}

function Narration({ caseId, versionId, refreshKey }: { caseId: number; versionId: number; refreshKey: number }) {
  const [flaggedOnly, setFlaggedOnly] = useState(false);
  const { data, error, loading, refetch } = useApi(
    () => nullIfNotFound(api.storyVoice(caseId, versionId)),
    [caseId, versionId, refreshKey],
    { keepPrevious: true },
  );
  const manifest: VoiceManifest | null = data && data.story_version_id === versionId ? data : null;

  if (!manifest && loading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-24" />
        <TableSkeleton rows={6} cols={6} />
      </div>
    );
  }
  if (!manifest && error) return <ErrorState message={error} onRetry={refetch} />;
  if (!manifest) {
    return (
      <EmptyState
        title="No narration rendered yet"
        description="Run a pilot in the Run tab — the voice stage narrates the opening and checks every block with speech-to-text."
      />
    );
  }

  const asrFailed = manifest.blocks.filter((b) => b.asr && !b.asr.passed).length;
  const blocks = flaggedOnly ? manifest.blocks.filter((b) => b.flags.length > 0) : manifest.blocks;

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Narration · {langLabel(manifest.language)}</CardTitle>
          <span className="font-mono text-[11px] text-muted-foreground">
            {manifest.provider} · {manifest.model_id}
          </span>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
            <Metric label="Duration" value={formatTimecode(manifest.duration_seconds)} />
            <Metric label="Blocks rendered" value={`${manifest.blocks_rendered}/${manifest.blocks_in_plan}`} />
            <Metric
              label="Speech-to-text failures"
              value={manifest.asr ? asrFailed : "n/a"}
              tone={manifest.asr ? (asrFailed ? "danger" : "success") : undefined}
            />
            <Metric
              label="Loudness (LUFS)"
              value={manifest.loudness.after_lufs != null ? manifest.loudness.after_lufs.toFixed(1) : "—"}
            />
            <Metric label="Characters paid" value={manifest.characters_paid.toLocaleString()} />
            <Metric label="Flags" value={manifest.flags.length} tone={manifest.flags.length ? "danger" : undefined} />
          </div>
          {manifest.asr_error && (
            <InlineAlert tone="warning">
              Speech-to-text check unavailable: {manifest.asr_error}
            </InlineAlert>
          )}
          {manifest.styles_used.length > 0 && (
            <p className="text-xs text-muted-foreground">
              Styles used:{" "}
              {manifest.styles_used.map((s) => (
                <Badge key={s} variant="outline" className="mr-1">
                  {humanize(s)}
                </Badge>
              ))}
            </p>
          )}
          <div>
            <p id={`narration-${versionId}`} className="mb-1 text-xs font-medium text-muted-foreground">
              Narration only (without music)
            </p>
            <audio
              controls
              preload="none"
              aria-labelledby={`narration-${versionId}`}
              src={apiFileUrl(`/api/cases/${caseId}/stories/${versionId}/voice/narration.mp3`)}
              className="w-full"
            />
          </div>
          {manifest.mix && (
            <p className="text-xs text-muted-foreground">
              Music mix: {manifest.mix.beds} bed{manifest.mix.beds === 1 ? "" : "s"} ·{" "}
              {manifest.mix.music_moments} music moment{manifest.mix.music_moments === 1 ? "" : "s"} ·{" "}
              {manifest.mix.music_only_seconds.toFixed(0)} s music only
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Voice blocks</CardTitle>
          <Checkbox
            label="Only flagged blocks"
            checked={flaggedOnly}
            onChange={(e) => setFlaggedOnly(e.target.checked)}
          />
        </CardHeader>
        <CardContent className="p-0">
          {blocks.length === 0 ? (
            <p className="px-4 py-6 text-sm text-muted-foreground">
              {flaggedOnly ? "No block was flagged." : "No blocks were rendered."}
            </p>
          ) : (
            <Table className="text-xs">
              <THead>
                <TR className="hover:bg-transparent">
                  <TH>Block</TH>
                  <TH>Style</TH>
                  <TH className="text-right">Duration</TH>
                  <TH className="text-right">Words/min</TH>
                  <TH className="text-right">ASR WER</TH>
                  <TH>Check</TH>
                  <TH>Flags</TH>
                </TR>
              </THead>
              <TBody>
                {blocks.map((b) => (
                  <TR key={b.block_id}>
                    <TD className="font-mono">
                      {b.block_id}
                      {b.beat_ids.length > 0 && (
                        <span className="block text-[10px] text-muted-foreground">{b.beat_ids.join(" ")}</span>
                      )}
                    </TD>
                    <TD>{humanize(b.style)}</TD>
                    <TD className="text-right tabular-nums">{b.actual_seconds.toFixed(1)} s</TD>
                    <TD className="text-right tabular-nums">{b.words_per_minute ?? "—"}</TD>
                    <TD className="text-right tabular-nums">
                      {b.asr ? `${(b.asr.word_error_rate * 100).toFixed(1)}%` : "—"}
                    </TD>
                    <TD>
                      {b.asr ? (
                        <Badge variant={b.asr.passed ? "success" : "danger"} title={b.asr.failures.join(", ") || undefined}>
                          {b.asr.passed ? "pass" : "fail"}
                        </Badge>
                      ) : (
                        <span className="text-muted-foreground">not checked</span>
                      )}
                      {b.attempts > 1 && (
                        <span className="ml-1 text-[10px] text-muted-foreground">{b.attempts} takes</span>
                      )}
                    </TD>
                    <TD>
                      {b.flags.length ? (
                        <span className="flex flex-wrap gap-1">
                          {b.flags.map((f) => (
                            <Badge key={f} variant="warning">
                              {humanize(f)}
                            </Badge>
                          ))}
                        </span>
                      ) : (
                        <span className="text-muted-foreground">—</span>
                      )}
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
