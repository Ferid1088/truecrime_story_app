"use client";

import { useMemo, useState } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import type { PunctuationBlock } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/state";

const STATUS_TONE = { EZAFE_REQUIRED: "accent", EZAFE_FORBIDDEN: "info", EZAFE_CONTEXT_DEPENDENT: "warning", NO_EZAFE_ANALYSIS_NEEDED: "default" } as const;
const FILTERS = [
  { key: "changed", label: "Changed for the voice", keep: (b: { changed: boolean }) => b.changed },
  { key: "flagged", label: "Flagged", keep: (b: { flags: number; review: string }) => b.flags > 0 || ["UNCERTAIN", "REJECT", "UNRESOLVED"].includes(b.review) },
  { key: "all", label: "All", keep: () => true },
] as const;

/** The voice text with each changed gap highlighted (the words are the approved text's). */
function Prepared({ block }: { block: PunctuationBlock }) {
  const marks = new Set(block.edits.map((e) => e.after_word));
  const pieces = block.tts_text.split(/(\s+)/);
  const isWord = (p: string) => !/^\s*$/.test(p) && !p.startsWith("[");
  const numbers = pieces.map((p, i) => (isWord(p) ? pieces.slice(0, i).filter(isWord).length : -1));
  return (
    <p lang="fa" dir="rtl" className="rounded-md bg-muted/40 p-2 text-base leading-8" aria-label="Prepared for ElevenLabs">
      {pieces.map((piece, i) => (
        <span key={i} className={numbers[i] >= 0 && marks.has(numbers[i]) ? "rounded bg-primary/20 px-0.5 font-semibold" : undefined}>{piece}</span>
      ))}
    </p>
  );
}

function Detail({ versionId, blockId }: { versionId: number; blockId: string }) {
  const block = useApi(() => api.semanticPunctuationBlock(versionId, blockId), [versionId, blockId]);
  const [all, setAll] = useState(false);
  if (block.error) return <ErrorState message={block.error} onRetry={block.refetch} />;
  if (!block.data) return <Skeleton className="h-48 w-full" />;
  const b = block.data;
  const shown = b.boundaries.filter((x) => all || x.status !== "NO_EZAFE_ANALYSIS_NEEDED");
  return (
    <div className="space-y-3" aria-label="Punctuation of the block">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium" dir="ltr">{b.block_id}</span>
        <Badge variant={b.review === "PASS" ? "success" : b.review === "NOT_REVIEWED" ? "outline" : "warning"}>{b.review}</Badge>
        {b.rounds > 0 && <span className="text-xs text-muted-foreground">{b.rounds} round(s)</span>}
      </div>
      <div>
        <p className="text-xs text-muted-foreground">Original Persian (approved, unchanged)</p>
        <p lang="fa" dir="rtl" className="rounded-md border border-border p-2 text-base leading-8" aria-label="Original Persian">{b.canonical}</p>
      </div>
      <div>
        <p className="text-xs text-muted-foreground">Prepared Persian for ElevenLabs</p>
        <Prepared block={b} />
      </div>
      <div aria-label="Punctuation changes">
        <p className="text-sm font-medium">Punctuation changes</p>
        {b.edits.length === 0 ? <p className="text-xs text-muted-foreground">None: the block goes to the voice with its own punctuation.</p> : (
          <ul className="list-disc ps-4 text-xs">
            {b.edits.map((e, i) => <li key={i}>after word {e.after_word}: <span lang="fa">{e.old || "∅"} → {e.new || "∅"}</span> ({e.source}) · {e.reason}</li>)}
          </ul>
        )}
        {b.refused.length > 0 && (
          <ul className="list-disc ps-4 text-xs text-muted-foreground">{b.refused.map((e, i) => <li key={i}>refused after word {e.after_word}: {e.why}</li>)}</ul>
        )}
      </div>
      {b.checks.length > 0 && (
        <ul className="list-disc ps-4 text-xs" aria-label="Checker">{b.checks.map((c) => <li key={c.round}>round {c.round}: {c.decision} · {c.issues.join("; ")}</li>)}</ul>
      )}
      {b.flags.length > 0 && <ul className="list-disc ps-4 text-xs text-rose-600 dark:text-rose-400">{b.flags.map((f, i) => <li key={i}>{f}</li>)}</ul>}
      <div aria-label="Ezafe decisions">
        <div className="flex items-center gap-2">
          <p className="text-sm font-medium">Ezafe decisions</p>
          <Button size="sm" variant="secondary" onClick={() => setAll(!all)}>{all ? "Hide boundaries that need no analysis" : "Show every boundary"}</Button>
        </div>
        <ul className="mt-1 space-y-1 text-xs">
          {shown.map((x) => (
            <li key={x.id} className="flex flex-wrap items-center gap-2">
              <span lang="fa" className="text-sm">{x.words[0]} · {x.words[1] ?? "—"}</span>
              <Badge variant={STATUS_TONE[x.status]}>{x.status}</Badge>
              {x.pronunciation && <span dir="ltr">{x.pronunciation}</span>}
              <span className="text-muted-foreground">{x.reason} ({x.source})</span>
            </li>
          ))}
        </ul>
      </div>
      <p className="text-xs text-muted-foreground">Acoustic result: none. No audio of this layer was listened to; the Audio review shows a narration once it exists.</p>
    </div>
  );
}

/** The semantic punctuation of the Persian narration: what the voice is given, why, and which word boundaries need or forbid an ezafe. */
export function PunctuationView({ versionId }: { versionId: number | null }) {
  const summary = useApi(() => (versionId == null ? Promise.resolve(null) : api.semanticPunctuation(versionId)), [versionId]);
  const [filter, setFilter] = useState<(typeof FILTERS)[number]["key"]>("all");
  const [picked, setPicked] = useState<string | null>(null);
  const blocks = useMemo(() => (summary.data?.blocks ?? []).filter(FILTERS.find((f) => f.key === filter)!.keep), [summary.data, filter]);
  if (versionId == null) return <EmptyState title="No version" description="This language has no story version yet." />;
  if (summary.error) return <ErrorState message={summary.error} onRetry={summary.refetch} />;
  if (!summary.data) return <Skeleton className="h-64 w-full" />;
  const s = summary.data.statistics as Record<string, number | boolean>;
  const current = blocks.find((b) => b.block_id === picked) ?? blocks[0];
  return (
    <div className="space-y-3">
      <p className="text-xs leading-5 text-muted-foreground" aria-label="Punctuation summary">
        Source: {summary.data.source}. {String(s.blocks)} blocks · {String(s.blocks_unchanged)} unchanged · {String(s.commas_inserted)} commas inserted ·
        {" "}{String(s.marks_replaced_or_removed)} marks replaced or removed · ezafe required {String(s.ezafe_required)} · forbidden {String(s.ezafe_forbidden)} ·
        {" "}context-dependent {String(s.context_dependent)} · reviewer rejections {String(s.reviewer_rejections)} · integrity violations {String(s.source_integrity_violations)}
      </p>
      <div className="flex flex-wrap gap-2" role="tablist" aria-label="Punctuation blocks">
        {FILTERS.map((f) => (
          <Button key={f.key} size="sm" variant={filter === f.key ? "default" : "secondary"} onClick={() => { setFilter(f.key); setPicked(null); }}>
            {f.label} <span className="opacity-70">({(summary.data?.blocks ?? []).filter(f.keep).length})</span>
          </Button>
        ))}
      </div>
      {!current ? <EmptyState title="No block here" description="No block matches this filter." /> : (
        <div className="grid gap-4 lg:grid-cols-[14rem_1fr]">
          <ul className="max-h-[36rem] space-y-1 overflow-y-auto" aria-label="Punctuated blocks">
            {blocks.map((b) => (
              <li key={b.block_id}>
                <button type="button" onClick={() => setPicked(b.block_id)}
                  className={`flex w-full items-center justify-between rounded-md border px-3 py-1.5 text-start text-xs ${b.block_id === current.block_id ? "border-primary bg-primary/5" : "border-border"}`}>
                  <span dir="ltr">{b.block_id.replace(/^[A-Z]+_/, "")}</span>
                  <span>{b.edits} · {b.boundaries.EZAFE_CONTEXT_DEPENDENT ?? 0} open</span>
                </button>
              </li>
            ))}
          </ul>
          <Detail key={current.block_id} versionId={versionId} blockId={current.block_id} />
        </div>
      )}
    </div>
  );
}
