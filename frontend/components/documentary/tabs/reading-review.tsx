"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { isRtl, langLabel } from "@/lib/format";
import type { ReadingAnswer, ReadingDoubt } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/state";
import { InlineAlert, LanguagePicker, type LanguageTabProps } from "../shared";

type Choice = "editor" | "checker" | "custom";

interface Pending {
  choice: Choice;
  latin: string;
  everywhere: boolean;
}

/** The sentence with the word in doubt marked. */
function Sentence({ doubt, lang }: { doubt: ReadingDoubt; lang: string }) {
  const at = doubt.sentence.indexOf(doubt.surface);
  const parts = at < 0 ? [doubt.sentence, "", ""] : [doubt.sentence.slice(0, at), doubt.surface, doubt.sentence.slice(at + doubt.surface.length)];
  return (
    <p lang={lang} dir={isRtl(lang) ? "rtl" : "ltr"} className="text-sm leading-7">
      {parts[0]}
      <mark className="rounded bg-amber-500/20 px-0.5 font-semibold">{parts[1]}</mark>
      {parts[2]}
    </p>
  );
}

function Doubt({
  doubt,
  lang,
  pending,
  onChange,
}: {
  doubt: ReadingDoubt;
  lang: string;
  pending: Pending | undefined;
  onChange: (next: Pending | undefined) => void;
}) {
  const answered = doubt.status === "answered";
  const pick = (choice: Choice) => onChange({ choice, latin: pending?.latin ?? "", everywhere: pending?.everywhere ?? false });
  const options: { choice: Choice; label: string; reading: string }[] = [
    { choice: "editor", label: "Editor", reading: doubt.editor_latin },
    ...(doubt.checker_latin ? [{ choice: "checker" as Choice, label: "Checker", reading: doubt.checker_latin }] : []),
  ];
  return (
    <li className="space-y-2 rounded-md border border-border p-3" data-testid="reading-doubt">
      <Sentence doubt={doubt} lang={lang} />
      <p className="text-xs text-muted-foreground">
        {doubt.block_id} · word {doubt.word_n + 1} · {doubt.question || "no question given"}
      </p>
      {answered ? (
        <p className="text-xs">
          <Badge variant="success">answered</Badge> <span className="font-mono">{doubt.answer_latin}</span>
        </p>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          {options.map((o) => (
            <Button
              key={o.choice}
              size="sm"
              variant={pending?.choice === o.choice ? "default" : "secondary"}
              onClick={() => pick(o.choice)}
            >
              {o.label}: <span className="font-mono">{o.reading || "—"}</span>
            </Button>
          ))}
          <Button size="sm" variant={pending?.choice === "custom" ? "default" : "secondary"} onClick={() => pick("custom")}>
            My reading
          </Button>
          {pending?.choice === "custom" && (
            <Input
              aria-label={`Your reading of ${doubt.surface}`}
              className="h-7 w-44 font-mono text-xs"
              placeholder="e.g. baham"
              value={pending.latin}
              onChange={(e) => onChange({ ...pending, latin: e.target.value })}
            />
          )}
          {pending && (
            <Checkbox
              label="keep for every film"
              checked={pending.everywhere}
              onChange={(e) => onChange({ ...pending, everywhere: e.target.checked })}
              className="text-xs text-muted-foreground"
            />
          )}
        </div>
      )}
    </li>
  );
}

/** The words of the narration's reading text that nobody could settle. One list, answered before the film is voiced. */
export function ReadingReviewTab({ settings, overview, refreshKey, language, onLanguageChange }: LanguageTabProps) {
  const versionId = overview.languages[language]?.version_id ?? null;
  const state = useApi(() => (versionId == null ? Promise.resolve(null) : api.readingDoubts(versionId)), [versionId, refreshKey]);
  const [chosen, setChosen] = useState<Record<number, Pending | undefined>>({});
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const picker = (
    <LanguagePicker
      languages={settings.languages}
      value={language}
      onChange={onLanguageChange}
      isAvailable={(l) => overview.languages[l] != null}
    />
  );
  if (versionId == null) {
    return (
      <div className="space-y-4">
        {picker}
        <EmptyState title={`No spoken version in ${langLabel(language)} yet`} description="The reading review belongs to a narration that is being prepared." />
      </div>
    );
  }
  if (state.error) return <ErrorState message={state.error} onRetry={state.refetch} />;
  if (!state.data) return <Skeleton className="h-48 w-full" />;
  const data = state.data;
  const open = data.doubts.filter((d) => d.status === "open");
  const ready = (d: ReadingDoubt) => {
    const p = chosen[d.id];
    return !!p && (p.choice !== "custom" || p.latin.trim() !== "");
  };
  const chosenCount = open.filter(ready).length;

  async function save() {
    const answers: ReadingAnswer[] = open
      .filter(ready)
      .map((d) => ({ id: d.id, choice: chosen[d.id]!.choice, latin: chosen[d.id]!.latin.trim() || undefined, everywhere: chosen[d.id]!.everywhere }));
    setSaving(true);
    setError(null);
    try {
      await api.answerReadingDoubts(data.version_id, answers);
      setChosen({});
      state.refetch();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="space-y-4">
      {picker}
      <Card>
        <CardHeader>
          <CardTitle>Reading review — {langLabel(language)}</CardTitle>
          <span className="text-xs text-muted-foreground">
            {data.open} open · {data.answered} answered
          </span>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-xs leading-5 text-muted-foreground">
            The editor and the checker disagreed, or neither was sure, how these words are read. Choose a reading for each, then save and resume the job
            in the Run tab. Nothing is voiced before every word is answered.
          </p>
          {data.doubts.length === 0 ? (
            <EmptyState title="Nothing is in doubt" description="Every word of this narration has a reading both agents agree on." />
          ) : (
            <ul className="space-y-3">
              {data.doubts.map((d) => (
                <Doubt key={d.id} doubt={d} lang={language} pending={chosen[d.id]} onChange={(next) => setChosen((c) => ({ ...c, [d.id]: next }))} />
              ))}
            </ul>
          )}
          {error && <InlineAlert tone="error">{error}</InlineAlert>}
          {open.length > 0 && (
            <div className="flex flex-wrap items-center gap-3">
              <Button onClick={save} loading={saving} disabled={chosenCount === 0}>
                Save {chosenCount} answer{chosenCount === 1 ? "" : "s"}
              </Button>
              <span className="text-xs text-muted-foreground">{open.length - chosenCount} still to choose</span>
            </div>
          )}
          {data.open === 0 && data.answered > 0 && (
            <InlineAlert tone="warning">Every word is answered. Resume the job in the Run tab to voice the film.</InlineAlert>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
