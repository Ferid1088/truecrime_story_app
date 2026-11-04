"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { isRtl } from "@/lib/format";
import type { ReadingFamily } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/state";
import { InlineAlert } from "../shared";

/** One word family only the owner can decide: its candidates with their evidence and where the film reads them, one decision for every occurrence. */
function Family({ f, lang, reviewer, onDecide }: { f: ReadingFamily; lang: string; reviewer: string; onDecide: (reading: string) => Promise<void> }) {
  const [busy, setBusy] = useState<string | null>(null);
  const rtl = isRtl(lang);
  return (
    <li className="space-y-2 rounded-md border border-border p-3" aria-label={`Family ${f.word}`}>
      <div className="flex flex-wrap items-center gap-2">
        <span lang={lang} dir={rtl ? "rtl" : "ltr"} className="text-xl">{f.word}</span>
        <Badge variant="outline">{f.occurrences} occurrences</Badge>
        {f.kind === "name" && <Badge variant="info">name</Badge>}
        {f.forms.length > 1 && <span className="text-xs text-muted-foreground" lang={lang}>forms: {f.forms.join("، ")}</span>}
      </div>
      <p className="text-xs text-muted-foreground">{f.why_undecided}</p>
      {f.lexicon && <p className="text-xs text-muted-foreground">{f.lexicon}</p>}
      <ul className="space-y-1.5">
        {Object.entries(f.candidates).map(([reading, blocks]) => {
          const e = f.evidence_per_candidate?.[reading];
          return (
            <li key={reading} className="flex flex-wrap items-start gap-2 text-sm">
              <Button size="sm" variant="secondary" disabled={!reviewer.trim() || busy != null} loading={busy === reading}
                onClick={async () => { setBusy(reading); try { await onDecide(reading); } finally { setBusy(null); } }}>
                Read it {reading}
              </Button>
              <span className="text-xs text-muted-foreground" dir="ltr">
                {blocks} block{blocks === 1 ? "" : "s"} · {e?.written_on_the_letters ? "the letters can write it" : "the letters cannot write it"}
              </span>
              {e?.contexts[0] && <span lang={lang} dir={rtl ? "rtl" : "ltr"} className="w-full text-xs text-muted-foreground">{e.contexts[0].sentence}</span>}
            </li>
          );
        })}
      </ul>
    </li>
  );
}

/** The words the narration reads in more than one way: what the rules resolved (with the evidence), what is kept as a legitimate variant, and the
 * families only the owner can decide. A decision is kept for the case (not an approval: the word is not respelled for the voice). */
export function ReadingConflicts({ versionId, language, reviewer }: { versionId: number | null; language: string; reviewer: string }) {
  const review = useApi(() => (versionId == null ? Promise.resolve(null) : api.readingReview(versionId, language)), [versionId, language]);
  const [error, setError] = useState<string | null>(null);
  if (versionId == null) return <EmptyState title="No version" description="This language has no story version yet." />;
  if (review.error) return <ErrorState message={review.error} onRetry={review.refetch} />;
  if (!review.data) return <Skeleton className="h-64 w-full" />;
  const r = review.data;
  const decide = async (word: string, reading: string) => {
    setError(null);
    try {
      await api.decideReading(versionId, language, { word, reading, reviewer });
      review.refetch();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };
  return (
    <div className="space-y-3">
      <p className="text-xs leading-5 text-muted-foreground">
        {r.resolved.length} resolved by a rule · {r.retained.length} kept as legitimate variants · {r.owner_review.length} for you. No reading is chosen by
        how often it occurs. A decision holds for every occurrence in this case and does not respell the word for the voice.
      </p>
      {!reviewer.trim() && <InlineAlert tone="warning">Write your name above first: a decision is only valid with a named reviewer.</InlineAlert>}
      {error && <InlineAlert tone="error">{error}</InlineAlert>}
      <details className="rounded-md border border-border p-2 text-xs" aria-label="Resolved by a rule">
        <summary className="cursor-pointer font-medium">Resolved by a rule ({r.resolved.length})</summary>
        <ul className="mt-1 space-y-0.5">
          {r.resolved.map((f) => <li key={f.word}><span lang={language} className="text-sm">{f.word}</span> → <span dir="ltr">{f.reading}</span> ({f.rule}): {f.evidence}</li>)}
        </ul>
      </details>
      {r.owner_review.length === 0 ? <EmptyState title="Nothing to decide" description="Every word is read one way." /> : (
        <ul className="space-y-2" aria-label="Families for the owner">
          {r.owner_review.map((f) => <Family key={f.word} f={f} lang={language} reviewer={reviewer} onDecide={(reading) => decide(f.word, reading)} />)}
        </ul>
      )}
    </div>
  );
}
