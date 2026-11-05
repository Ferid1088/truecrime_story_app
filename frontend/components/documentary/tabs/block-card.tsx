"use client";

import { useState } from "react";
import { Pause, Play } from "lucide-react";
import { api } from "@/lib/api";
import type { BlockReviewBlock, BlockReviewTake } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";

const clock = (s: number | null) => (s == null ? "–" : `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`);

/** The words of a block: a corrected word stays red for good. */
function Words({ block }: { block: BlockReviewBlock }) {
  return (
    <p lang="fa" dir="rtl" className="text-base leading-8" data-testid={`words-${block.block_id}`}>
      {block.words.map((w) => (
        <span key={w.n} data-corrected={w.corrected} title={w.corrected ? `was: ${w.was || "(new word)"}${w.finglish ? ` · finglish: ${w.finglish}` : ""}` : undefined}
          className={w.corrected ? "rounded px-0.5 font-medium text-red-600 dark:text-red-400" : undefined}>
          {w.surface}{" "}
        </span>
      ))}
    </p>
  );
}

/** The correction of a block: the whole text, and every word with its Persian spelling and a finglish that goes to the voice as an alias. */
function Editor({ versionId, block, onSaved, onCancel }: { versionId: number; block: BlockReviewBlock; onSaved: (b: BlockReviewBlock) => void; onCancel: () => void }) {
  const [text, setText] = useState(block.text);
  const [rows, setRows] = useState(block.words.map((w) => ({ n: w.n, surface: w.surface, finglish: w.finglish })));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const wholeTextChanged = text !== block.text;

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const words = wholeTextChanged ? [] : rows
        .filter((r, i) => r.surface !== block.words[i].surface || r.finglish !== block.words[i].finglish)
        .map((r) => ({ n: r.n, surface: r.surface, finglish: r.finglish }));
      onSaved(await api.editBlock(versionId, block.block_id, { text: wholeTextChanged ? text : undefined, words }));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3 rounded-md border border-dashed p-3" data-testid={`editor-${block.block_id}`}>
      <label className="block text-xs text-muted-foreground">
        Text of the block (the words below are available while the text is unchanged)
        <Textarea lang="fa" dir="rtl" rows={4} value={text} onChange={(e) => setText(e.target.value)} className="mt-1 text-base" aria-label="Text of the block" />
      </label>
      {!wholeTextChanged && (
        <div className="grid max-h-72 grid-cols-1 gap-1 overflow-y-auto sm:grid-cols-2" aria-label="Words of the block">
          {rows.map((r, i) => (
            <div key={r.n} className="flex items-center gap-2">
              <span className="w-7 shrink-0 text-xs text-muted-foreground" dir="ltr">{r.n}</span>
              <Input lang="fa" dir="rtl" value={r.surface} aria-label={`Persian word ${r.n}`}
                onChange={(e) => setRows((all) => all.map((x, j) => (j === i ? { ...x, surface: e.target.value } : x)))} className="h-8 flex-1" />
              <Input dir="ltr" placeholder="finglish" value={r.finglish} aria-label={`Finglish of word ${r.n}`}
                onChange={(e) => setRows((all) => all.map((x, j) => (j === i ? { ...x, finglish: e.target.value } : x)))} className="h-8 flex-1" />
            </div>
          ))}
        </div>
      )}
      {error && <p role="alert" className="text-sm text-rose-700">{error}</p>}
      <div className="flex gap-2">
        <Button size="sm" onClick={save} disabled={busy}>{busy ? "Saving…" : "Save correction"}</Button>
        <Button size="sm" variant="secondary" onClick={onCancel} disabled={busy}>Cancel</Button>
      </div>
    </div>
  );
}

/** The takes of a block: the ticked one counts; the others stay, greyed. */
function Takes({ block, playing, onPlay, onTick }: { block: BlockReviewBlock; playing: string | null; onPlay: (t: BlockReviewTake) => void; onTick: (t: BlockReviewTake) => void }) {
  return (
    <ul className="space-y-1" aria-label={`Takes of ${block.block_id}`}>
      {block.takes.map((t) => {
        const id = `${block.block_id}:${t.take_no}`;
        return (
          <li key={t.take_no} className={`flex items-center gap-2 rounded px-2 py-1 text-xs ${t.active ? "bg-emerald-50 dark:bg-emerald-950/30" : "bg-muted/40 text-muted-foreground"}`} data-active={t.active}>
            <Checkbox checked={t.active} onChange={() => !t.active && onTick(t)} aria-label={`Use take ${t.take_no} of ${block.block_id}`} />
            <button type="button" onClick={() => onPlay(t)} className="inline-flex items-center gap-1" aria-label={`${playing === id ? "Pause" : "Play"} take ${t.take_no}`}>
              {playing === id ? <Pause className="size-3.5" /> : <Play className="size-3.5" />}
            </button>
            <span>take {t.take_no} · {t.source === "original" ? "original" : "corrected"} · {clock(t.duration_s)}{t.source === "correction" ? ` · €${t.cost_eur.toFixed(4)}` : ""}{t.active ? "" : " · not used"}</span>
            <span dir="ltr" title="ElevenLabs speed · stability · speed-up of the finished file" data-testid={`voice-${block.block_id}-${t.take_no}`}>speed {t.voice.speed} · stability {t.voice.stability} · ×{t.voice.post_speed}</span>
            {t.aliases.length > 0 && <span dir="ltr" className="truncate" title={t.aliases.map((a) => `${a.string_to_replace} → ${a.alias}`).join(", ")}>alias: {t.aliases.map((a) => a.alias).join(", ")}</span>}
          </li>
        );
      })}
    </ul>
  );
}

export function BlockCard({ versionId, block, current, playingTake, onPlayBlock, onPlayTake, onChanged }: {
  versionId: number;
  block: BlockReviewBlock;
  current: boolean;
  playingTake: string | null;
  onPlayBlock: () => void;
  onPlayTake: (t: BlockReviewTake) => void;
  onChanged: (b: BlockReviewBlock) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run(job: () => Promise<BlockReviewBlock>) {
    setBusy(true);
    setError(null);
    try {
      onChanged(await job());
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const tone = block.edited ? "border-amber-300 bg-amber-50 dark:border-amber-700 dark:bg-amber-950/30" : "border-border";
  return (
    <li id={`block-${block.block_id}`} className={`rounded-md border p-3 ${tone} ${current ? "ring-2 ring-primary" : ""}`} data-edited={block.edited} data-testid={`block-${block.block_id}`}>
      <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <button type="button" onClick={onPlayBlock} className="inline-flex items-center gap-1 font-medium text-foreground" aria-label={`Play from block ${block.position + 1}`}>
          <Play className="size-3.5" /> {block.position + 1}
        </button>
        <span dir="ltr">{block.block_id}</span>
        {block.edited && <Badge variant="outline" className="border-amber-400 text-amber-700">corrected</Badge>}
        {block.needs_sending && <Badge variant="warning">not voiced yet</Badge>}
      </div>
      {block.draft_text !== null && block.needs_sending && <p className="mb-1 text-xs text-amber-700">Draft: this text is not voiced yet; the ticked take still has the earlier text.</p>}
      <Words block={block} />
      {editing ? (
        <Editor versionId={versionId} block={block} onCancel={() => setEditing(false)} onSaved={(b) => { setEditing(false); onChanged(b); }} />
      ) : (
        <div className="mt-2 flex flex-wrap gap-2">
          <Button size="sm" variant="secondary" onClick={() => setEditing(true)} disabled={busy}>Correct</Button>
          <Button size="sm" variant={block.needs_sending ? "default" : "secondary"} onClick={() => run(() => api.sendBlock(versionId, block.block_id, !block.needs_sending))} disabled={busy}>
            {busy ? "Voicing…" : block.needs_sending ? "Send to ElevenLabs" : "Voice again"}
          </Button>
        </div>
      )}
      {error && <p role="alert" className="mt-1 text-sm text-rose-700">{error}</p>}
      <div className="mt-2">
        <Takes block={block} playing={playingTake} onPlay={onPlayTake} onTick={(t) => run(() => api.activateTake(versionId, block.block_id, t.take_no))} />
      </div>
    </li>
  );
}
