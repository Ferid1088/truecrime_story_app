"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { isRtl } from "@/lib/format";
import type { BlockReviewOverview, BlockReviewReplaceMatch } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

/** Words to find and the words for their place, in every block at once: first a preview of the places, then a correction of each block that has them
 *  (the blocks turn yellow, the new words red; they are voiced with "Send corrected blocks"). */
export function ReplaceInBlocks({ versionId, language, onApplied }: { versionId: number; language: string; onApplied: (review: BlockReviewOverview) => void }) {
  const [find, setFind] = useState("");
  const [replace, setReplace] = useState("");
  const [seen, setSeen] = useState<{ blocks_found: number; places_found: number; matches: BlockReviewReplaceMatch[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run(apply: boolean) {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      const r = await api.replaceInBlocks(versionId, { find, replace, apply });
      if (r.applied) {
        onApplied(r);
        setSeen(null);
        setNote(`${r.places_found} place(s) in ${r.blocks_found} block(s) corrected. Send them to ElevenLabs when you are ready.`);
      } else {
        setSeen(r);
        if (r.blocks_found === 0) setNote("These words stand in no block.");
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="rounded-md border p-3" data-testid="replace-in-blocks">
      <summary className="cursor-pointer text-sm font-medium">Replace in all blocks</summary>
      <div className="mt-3 space-y-3">
        <div className="grid gap-2 sm:grid-cols-2">
          <label className="text-xs text-muted-foreground">Find (words, one after the other)
            <Input lang={language} dir={isRtl(language) ? "rtl" : "ltr"} value={find} onChange={(e) => { setFind(e.target.value); setSeen(null); }} className="mt-1" aria-label="Words to find" />
          </label>
          <label className="text-xs text-muted-foreground">Replace with
            <Input lang={language} dir={isRtl(language) ? "rtl" : "ltr"} value={replace} onChange={(e) => { setReplace(e.target.value); setSeen(null); }} className="mt-1" aria-label="Words to put in their place" />
          </label>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="secondary" disabled={busy || !find.trim() || !replace.trim()} onClick={() => run(false)}>Find the places</Button>
          {seen && seen.blocks_found > 0 && (
            <Button size="sm" disabled={busy} onClick={() => run(true)}>Replace in {seen.blocks_found} block{seen.blocks_found === 1 ? "" : "s"} ({seen.places_found} place{seen.places_found === 1 ? "" : "s"})</Button>
          )}
        </div>
        {note && <p role="status" className="text-sm text-emerald-700">{note}</p>}
        {error && <p role="alert" className="text-sm text-rose-700">{error}</p>}
        {seen && seen.blocks_found > 0 && (
          <ul className="space-y-1 text-sm" aria-label="Places found">
            {seen.matches.map((m) => (
              <li key={m.block_id} className="rounded bg-muted/40 px-2 py-1">
                <span className="text-xs text-muted-foreground" dir="ltr">{m.position + 1} · {m.block_id}{m.count > 1 ? ` · ${m.count} places` : ""}</span>
                <span lang={language} dir={isRtl(language) ? "rtl" : "ltr"} className="block"><del className="text-red-600">{m.before}</del></span>
                <span lang={language} dir={isRtl(language) ? "rtl" : "ltr"} className="block">{m.after}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </details>
  );
}
