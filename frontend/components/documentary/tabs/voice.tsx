"use client";

import { useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { api, apiFileUrl, nullIfNotFound } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatTimecode, humanize, isRtl, langLabel } from "@/lib/format";
import type { BlockPronunciation, PronunciationFixKind, VoiceManifest, VoiceManifestBlock } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Skeleton, TableSkeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { EmptyState, ErrorState } from "@/components/state";
import { InlineAlert, LanguagePicker, type LanguageTabProps, Metric, TtsText } from "../shared";

const FIX_LABELS: Record<PronunciationFixKind, string> = {
  vowelled: "harakat",
  full: "full harakat",
  respell: "spelling",
  synonym: "synonym",
};

/** A word in the narration's own script and direction. */
function Word({ text, lang }: { text: string; lang: string }) {
  return (
    <span lang={lang} dir={isRtl(lang) ? "rtl" : "ltr"} className="font-medium">
      {text}
    </span>
  );
}

function pronunciationCounts(blocks: VoiceManifestBlock[]) {
  const checked = blocks.filter((b) => b.pronunciation);
  return {
    blocks: checked.length,
    words: checked.reduce((n, b) => n + (b.pronunciation?.words.length ?? 0), 0),
    fixed: checked.reduce(
      (n, b) => n + (b.pronunciation?.fixes ?? []).reduce((m, r) => m + r.fixes.length, 0),
      0,
    ),
    unresolved: checked.reduce((n, b) => n + (b.pronunciation?.unresolved.length ?? 0), 0),
  };
}

export function VoiceTab({ caseId, settings, overview, refreshKey, language, onLanguageChange }: LanguageTabProps) {
  const versionId = overview.languages[language]?.version_id ?? null;
  const pron = settings.pronunciation_check;
  const pronChecked = (lang: string) => pron.enabled && pron.languages.includes(lang);

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
                <TH>Pronunciation check</TH>
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
                      {pronChecked(l)
                        ? `listening · up to ${pron.max_rounds} corrected take${pron.max_rounds === 1 ? "" : "s"}`
                        : "—"}
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
        <Narration
          caseId={caseId}
          versionId={versionId}
          refreshKey={refreshKey}
          pronunciationChecked={pronChecked(language)}
        />
      )}
    </div>
  );
}

function Narration({
  caseId,
  versionId,
  refreshKey,
  pronunciationChecked,
}: {
  caseId: number;
  versionId: number;
  refreshKey: number;
  pronunciationChecked: boolean;
}) {
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
  const pron = pronunciationCounts(manifest.blocks);
  const showPronunciation = pronunciationChecked || pron.blocks > 0;

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
          {manifest.pronunciation_error ? (
            <InlineAlert tone="warning">
              Pronunciation check unavailable: {manifest.pronunciation_error}
            </InlineAlert>
          ) : manifest.pronunciation_listener ? (
            <p className={pron.unresolved ? "text-xs text-rose-600 dark:text-rose-400" : "text-xs text-muted-foreground"}>
              Pronunciation check: listened with{" "}
              <span className="font-mono">{manifest.pronunciation_listener}</span> · {pron.words} word
              {pron.words === 1 ? "" : "s"} in {pron.blocks} block{pron.blocks === 1 ? "" : "s"} · {pron.fixed}{" "}
              fixed · {pron.unresolved} unresolved
            </p>
          ) : (
            pronunciationChecked && (
              <p className="text-xs text-muted-foreground">
                Pronunciation check: no word was listened to in this narration (the pronunciation key marked
                none in these blocks).
              </p>
            )
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
                  {showPronunciation && <TH>Pronunciation</TH>}
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
                    {showPronunciation && (
                      <TD>
                        <PronunciationCell pronunciation={b.pronunciation ?? null} />
                      </TD>
                    )}
                    <TD>
                      {b.flags.length ? (
                        <span className="flex flex-wrap gap-1">
                          {b.flags.map((f) => (
                            <Badge key={f} variant={f === "pronunciation_unresolved" ? "danger" : "warning"}>
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

      {pron.blocks > 0 && <PronunciationSection blocks={manifest.blocks} language={manifest.language} />}
    </div>
  );
}

function PronunciationCell({ pronunciation }: { pronunciation: BlockPronunciation | null }) {
  if (!pronunciation) return <span className="text-muted-foreground">—</span>;
  const fixed = pronunciation.fixes.reduce((n, r) => n + r.fixes.length, 0);
  return (
    <span className="flex flex-wrap items-center gap-1">
      <span className="tabular-nums text-muted-foreground">{pronunciation.words.length} checked</span>
      {fixed > 0 && <Badge variant="info">{fixed} fixed</Badge>}
      {pronunciation.unresolved.length > 0 ? (
        <Badge variant="danger">{pronunciation.unresolved.length} unresolved</Badge>
      ) : (
        <Badge variant="success">ok</Badge>
      )}
    </span>
  );
}

/** Per block: which risky words were fixed after which take and how, and what is still wrong. */
function PronunciationSection({ blocks, language }: { blocks: VoiceManifestBlock[]; language: string }) {
  const [problemsOnly, setProblemsOnly] = useState(true);
  const checked = blocks.filter((b) => b.pronunciation);
  const shown = problemsOnly
    ? checked.filter((b) => b.pronunciation && (b.pronunciation.fixes.length > 0 || b.pronunciation.unresolved.length > 0))
    : checked;

  return (
    <Card>
      <CardHeader className="flex-wrap gap-2">
        <CardTitle>Pronunciation</CardTitle>
        <Checkbox
          label="Only blocks with fixes or problems"
          checked={problemsOnly}
          onChange={(e) => setProblemsOnly(e.target.checked)}
        />
      </CardHeader>
      <CardContent className="p-0">
        <p className="border-b border-border px-4 py-2 text-[11px] leading-4 text-muted-foreground">
          Each risky word of the pronunciation key is found in the audio and its vowels compared with the
          reading its meaning needs. A word said wrong gets harakat, then full harakat, then an
          unambiguous spelling (last: a synonym) and the block is spoken again.
        </p>
        {shown.length === 0 ? (
          <p className="px-4 py-6 text-sm text-emerald-600 dark:text-emerald-400">
            Every checked word was said as its meaning needs — no fixes were necessary.
          </p>
        ) : (
          <ul className="divide-y divide-border">
            {shown.map((b) => (
              <PronunciationBlock key={b.block_id} block={b} language={language} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function PronunciationBlock({ block, language }: { block: VoiceManifestBlock; language: string }) {
  const [showWords, setShowWords] = useState(false);
  const p = block.pronunciation;
  if (!p) return null;
  const byWord = new Map(p.words.map((w) => [w.word, w]));

  return (
    <li className="space-y-2 px-4 py-3 text-xs">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono font-medium">{block.block_id}</span>
        <span className="text-muted-foreground">
          {p.words.length} word{p.words.length === 1 ? "" : "s"} checked · {p.rounds} take{p.rounds === 1 ? "" : "s"}
        </span>
        {p.unresolved.length > 0 ? (
          <Badge variant="danger">{p.unresolved.length} unresolved</Badge>
        ) : (
          <Badge variant="success">all said right</Badge>
        )}
      </div>

      {p.fixes.map((round) => (
        <div key={round.after_round} className="flex flex-wrap items-center gap-1.5">
          <span className="text-muted-foreground">After take {round.after_round + 1}:</span>
          {round.fixes.map((f, i) => (
            <span
              key={`${f.word}-${i}`}
              dir="ltr"
              className="inline-flex items-center gap-1 rounded border border-sky-500/25 bg-sky-500/10 px-1.5 leading-5"
            >
              <Word text={f.word} lang={language} />
              <span aria-hidden className="text-muted-foreground">
                →
              </span>
              <span className="sr-only">changed to</span>
              <Word text={f.form} lang={language} />
              <span className="text-muted-foreground">· {FIX_LABELS[f.fix] ?? humanize(f.fix)}</span>
              <span className="font-mono text-muted-foreground">{f.read}</span>
            </span>
          ))}
        </div>
      ))}

      {p.unresolved.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Unresolved words">
          <span className="font-medium text-rose-600 dark:text-rose-400">Still wrong — check by ear:</span>
          {p.unresolved.map((word, i) => {
            const w = byWord.get(word);
            return (
              <span
                key={`${word}-${i}`}
                dir="ltr"
                className="inline-flex items-center gap-1 rounded border border-rose-500/30 bg-rose-500/10 px-1.5 leading-5 text-rose-700 dark:text-rose-300"
              >
                <Word text={word} lang={language} />
                {w && (
                  <>
                    <span>should be</span>
                    <span className="font-mono">{w.read}</span>
                    {w.heard_ipa && <span className="font-mono opacity-80">heard {w.heard_ipa}</span>}
                  </>
                )}
              </span>
            );
          })}
        </div>
      )}

      {p.per_round.length > 1 && (
        <p className="text-muted-foreground">
          Wrong per take:{" "}
          {p.per_round.map((r, i) => (
            <span key={i} className="mr-2">
              {(r.round ?? i) + 1}:{" "}
              {r.wrong.length
                ? r.wrong.map((w, k) => (
                    <span key={k}>
                      {k > 0 && ", "}
                      <Word text={w} lang={language} />
                    </span>
                  ))
                : "none"}
            </span>
          ))}
        </p>
      )}

      {block.tts_text && (
        <div>
          <p className="mb-0.5 text-muted-foreground">Sent to the voice:</p>
          <p lang={language} dir={isRtl(language) ? "rtl" : "ltr"} className="break-words text-sm leading-6">
            <TtsText text={block.tts_text} />
          </p>
        </div>
      )}

      <button
        type="button"
        onClick={() => setShowWords((v) => !v)}
        aria-expanded={showWords}
        className="flex cursor-pointer items-center gap-1 font-medium text-muted-foreground hover:text-foreground"
      >
        {showWords ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
        Checked words
      </button>
      {showWords && (
        <Table className="text-xs">
          <THead>
            <TR className="hover:bg-transparent">
              <TH>Word</TH>
              <TH>Reading</TH>
              <TH>Said as</TH>
              <TH>Verdict</TH>
              <TH>Heard (IPA)</TH>
              <TH className="text-right">Distance</TH>
              <TH className="text-right">At (in block)</TH>
            </TR>
          </THead>
          <TBody>
            {p.words.map((w, i) => (
              <TR key={`${w.word}-${i}`}>
                <TD>
                  <Word text={w.word} lang={language} />
                </TD>
                <TD className="font-mono">{w.read}</TD>
                <TD>
                  <Word text={w.form} lang={language} />
                </TD>
                <TD>
                  {w.ok === true ? (
                    <Badge variant="success">right</Badge>
                  ) : w.ok === false ? (
                    <Badge variant="danger">wrong</Badge>
                  ) : (
                    <Badge variant="outline">{w.reason ? humanize(w.reason) : "not judged"}</Badge>
                  )}
                </TD>
                <TD className="font-mono text-muted-foreground">{w.heard_ipa || "—"}</TD>
                <TD className="text-right tabular-nums">{w.distance ?? "—"}</TD>
                <TD className="whitespace-nowrap text-right tabular-nums text-muted-foreground">
                  {w.at ? `${w.at[0].toFixed(2)} s` : "—"}
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}
    </li>
  );
}
