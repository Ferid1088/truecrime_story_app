"use client";

import { useEffect, useState } from "react";
import { textReviewApi, type TextReviewItem } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { isRtl } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { EmptyState, ErrorState } from "@/components/state";
import { Skeleton } from "@/components/ui/skeleton";
import { InlineAlert, LanguagePicker, type LanguageTabProps } from "../shared";

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** What the owner has typed and not saved yet is kept in this browser, per case and language, so a reload or a closed tab loses nothing. Storage can be missing or blocked: then it is only not kept. */
const draftKey = (caseId: number, language: string) => `texts-drafts:${caseId}:${language}`;
function readDrafts(key: string): Record<string, string> {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(key) ?? "{}");
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch { return {}; }
}
function writeDrafts(key: string, drafts: Record<string, string>) {
  try { if (Object.keys(drafts).length > 0) window.localStorage.setItem(key, JSON.stringify(drafts)); else window.localStorage.removeItem(key); } catch { /* not kept */ }
}

/** The titles and cover texts of a language the owner reviews before any cover is made: each can be corrected, and one press releases them exactly as they are. */
export function TextsTab({ caseId, settings, refreshKey, language, onLanguageChange }: LanguageTabProps) {
  return (
    <div className="space-y-4" data-testid="texts-tab">
      <LanguagePicker languages={settings.languages} value={language} onChange={onLanguageChange} />
      <TextsPanel key={`${caseId}:${language}`} caseId={caseId} language={language} refreshKey={refreshKey} />
    </div>
  );
}

function TextsPanel({ caseId, language, refreshKey }: { caseId: number; language: string; refreshKey: number }) {
  const [tick, setTick] = useState(0);
  const [busy, setBusy] = useState(false);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [loaded, setLoaded] = useState(false);
  const key = draftKey(caseId, language);
  useEffect(() => { setDrafts(readDrafts(key)); setLoaded(true); }, [key]);
  useEffect(() => { if (loaded) writeDrafts(key, drafts); }, [key, drafts, loaded]);
  const [note, setNote] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);
  const { data, error, refetch } = useApi(() => textReviewApi.status(caseId, language), [caseId, language, refreshKey, tick], { keepPrevious: true });
  const act = async (work: () => Promise<string>) => {
    setBusy(true); setNote(null);
    try { setNote({ tone: "ok", text: await work() }); } catch (e) { setNote({ tone: "bad", text: message(e) }); } finally { setBusy(false); setTick((t) => t + 1); }
  };
  const rtl = isRtl(language);
  /** A text is saved on the server when the owner leaves its box, and by the Save button: what he typed never depends on the browser alone. */
  const save = (item: TextReviewItem, value: string) => act(async () => {
    await textReviewApi.edit(caseId, language, item.kind, item.key, value);
    setDrafts((d) => { const { [`${item.kind}:${item.key}`]: _gone, ...rest } = d; return rest; });
    return `${item.label} saved.`;
  });
  // a text counts as unsaved only when it really differs from the saved one: typed back to the original, it is not a correction
  const unsaved = (data?.items ?? []).filter((item) => drafts[`${item.kind}:${item.key}`] !== undefined && drafts[`${item.kind}:${item.key}`] !== item.text);
  return (
    <>
      {error && !data && <ErrorState message={error} onRetry={refetch} />}
      {!data && !error && <Skeleton className="h-40 w-full" />}
      {data && !data.required && <EmptyState title="No text review for this language" description="Only the languages you read yourself are held for your review before their covers are made." />}
      {data && data.required && (
        <Card>
          <CardHeader>
            <CardTitle>
              Titles and cover texts{" "}
              {data.released ? <Badge variant="success">released</Badge> : data.changed_since_release ? <Badge variant="warning">changed since release: release again</Badge> : <Badge variant="warning">waiting for your review</Badge>}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-sm text-muted-foreground">No cover of this language is made before you release these texts. Correct any text, save it, then release them all.</p>
            {note && (note.tone === "ok" ? <p className="text-sm text-emerald-600 dark:text-emerald-400">{note.text}</p> : <InlineAlert tone="error">{note.text}</InlineAlert>)}
            {data.items.map((item) => {
              const id = `${item.kind}:${item.key}`;
              const value = drafts[id] ?? item.text;
              return (
                <div key={id} className="space-y-1" data-testid={`text-${id}`}>
                  <label className="text-xs font-medium text-muted-foreground" htmlFor={`text-input-${id}`}>{item.label}</label>
                  <div className="flex gap-2">
                    <textarea id={`text-input-${id}`} dir={rtl ? "rtl" : "ltr"} rows={2} value={value} onChange={(e) => setDrafts({ ...drafts, [id]: e.target.value })} onBlur={() => { if (!busy && value !== item.text && value.trim() !== "") void save(item, value); }}
                      className="min-h-10 w-full rounded border border-border bg-background p-2 text-sm" />
                    <Button type="button" size="sm" variant="outline" disabled={busy || value === item.text}
                      onClick={() => void save(item, value)}>Save</Button>
                  </div>
                </div>
              );
            })}
            <div className="flex flex-wrap items-center gap-3 pt-2">
              <Button type="button" disabled={busy || unsaved.length > 0 || data.released} onClick={() => void act(async () => { await textReviewApi.release(caseId, language); return "Released: the covers of this language may be made."; })}>
                I have reviewed all texts: release them
              </Button>
              {data.released && <Button type="button" variant="outline" disabled={busy} onClick={() => void act(async () => { await textReviewApi.withdraw(caseId, language); return "The release is taken back."; })}>Take the release back</Button>}
              {unsaved.length > 0 && <span className="text-xs text-muted-foreground">{unsaved.length} correction{unsaved.length > 1 ? "s are" : " is"} not saved yet (kept in this browser). Click out of the box, or press its Save button: it is then saved on the server.</span>}
            </div>
          </CardContent>
        </Card>
      )}
    </>
  );
}
