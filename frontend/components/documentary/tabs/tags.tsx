"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, Plus, X } from "lucide-react";
import { api } from "@/lib/api";
import type { TagItem, TagReport, TagSetView, TagsOverview } from "@/lib/types";
import { formatDate, isRtl, langLabel } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { LanguagePicker, type LanguageTabProps } from "../shared";

const APPROVAL_VARIANT = { pending: "info", approved: "success", rejected: "default" } as const;
const STATUS_VARIANT = { none: "default", running: "info", interrupted: "warning", done: "success", failed: "danger" } as const;
const SEPARATOR = /[,،]/;

/** The tags of a comma-separated field, keeping the category and the evidence of a tag whose text did not change. */
function parseField(field: string, known: TagItem[]): TagItem[] {
  return field.split(SEPARATOR).map((part) => part.trim()).filter(Boolean).map((text) => known.find((k) => k.text === text) ?? { text, category: "manual", evidence: [] });
}

const asField = (tags: TagItem[]) => tags.map((t) => t.text).join(", ");

/** One language: the editable, comma-separated field YouTube Studio takes, its counters and checks, the one-by-one editor and the breakdown by category. Regeneration never replaces your list: a new suggestion waits for your decision. */
function LanguageTags({ caseId, view, categories, onChanged }: { caseId: number; view: TagSetView; categories: { key: string; label: string }[]; onChanged: (view: TagSetView) => void }) {
  const rtl = isRtl(view.language);
  const dir = rtl ? "rtl" : "ltr";
  const [tags, setTags] = useState<TagItem[]>(view.tags);
  const [field, setField] = useState(asField(view.tags));
  const [checked, setChecked] = useState<TagReport | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [newTag, setNewTag] = useState("");
  const dirty = asField(tags) !== asField(view.tags);
  const working = busy != null;

  const edit = useCallback((next: TagItem[], text?: string) => {
    setTags(next);
    setField(text ?? asField(next));
  }, []);

  useEffect(() => {
    if (!dirty) return;
    const timer = setTimeout(() => {
      api.checkTags(caseId, view.language, tags).then(setChecked).catch((e) => setError(e instanceof Error ? e.message : "The check failed"));
    }, 300);
    return () => clearTimeout(timer);
  }, [caseId, view.language, tags, dirty]);
  const report = dirty && checked ? checked : view.report;

  const run = async (what: string, call: () => Promise<TagSetView>) => {
    setBusy(what);
    setError(null);
    try {
      onChanged(await call());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
    } finally {
      setBusy(null);
    }
  };

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(field);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setError("Copying is not allowed here; select the field and copy it by hand.");
    }
  };

  const move = (index: number, by: number) => {
    const next = [...tags];
    const [item] = next.splice(index, 1);
    next.splice(index + by, 0, item);
    edit(next);
  };

  const groups = useMemo(
    () => [...categories, { key: "manual", label: "Added by you" }].map((c) => ({ ...c, tags: tags.filter((t) => t.category === c.key).map((t) => t.text) })).filter((g) => g.tags.length > 0),
    [categories, tags],
  );
  const over = report.char_count > view.max_chars;
  const errors = report.issues.filter((i) => i.severity === "error");
  const warnings = report.issues.filter((i) => i.severity === "warning");
  const test = `tags-${view.language}`;

  return (
    <div className="space-y-4" data-testid={test}>
      <Card>
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-2 text-sm">
            YouTube tags — {langLabel(view.language)} <span className="font-normal text-muted-foreground">({view.locale})</span>
            <Badge variant={STATUS_VARIANT[view.status]} data-testid={`${test}-status`}>{view.status === "none" ? "not generated" : view.status}</Badge>
            {view.origin && <Badge variant="outline" data-testid={`${test}-origin`}>{view.origin === "edited" ? "edited by you" : "AI-generated"}</Badge>}
            <Badge variant={APPROVAL_VARIANT[view.approval]} data-testid={`${test}-approval`}>{view.approval}</Badge>
            {view.stale && <Badge variant="warning">facts changed since</Badge>}
            <Badge variant={errors.length > 0 ? "danger" : "success"} data-testid={`${test}-validation`}>{errors.length > 0 ? `${errors.length} error${errors.length > 1 ? "s" : ""}` : warnings.length > 0 ? "valid, needs a look" : "valid"}</Badge>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {view.stale && <p className="text-xs text-amber-700 dark:text-amber-400">The case facts, the title or the script changed after these tags were written. They are kept as they are; regenerate to get a suggestion.</p>}
          {view.status === "interrupted" && <p className="text-xs text-amber-700 dark:text-amber-400">The last generation was interrupted. Generate again.</p>}
          {view.status === "failed" && view.error && <p role="alert" className="text-xs text-rose-600" data-testid={`${test}-error`}>Generation failed: {view.error}</p>}

          <Textarea
            value={field}
            dir={dir}
            rows={4}
            lang={view.language}
            aria-label={`YouTube tags in ${langLabel(view.language)}`}
            data-testid={`${test}-field`}
            onChange={(e) => edit(parseField(e.target.value, tags), e.target.value)}
            placeholder="Generate the tags, or type them separated by commas"
          />
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
            <span className={over ? "font-medium text-rose-600" : undefined} data-testid={`${test}-chars`}>{report.char_count} / {view.max_chars} characters</span>
            <span data-testid={`${test}-count`}>{report.tag_count} tags</span>
            <span>Last generated: {view.generated_at ? formatDate(view.generated_at) : "never"}{view.model ? ` · ${view.model}` : ""}</span>
          </div>

          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="secondary" onClick={copy} disabled={field.trim() === ""} data-testid={`${test}-copy`}>{copied ? "Copied" : "Copy all"}</Button>
            <Button size="sm" variant="secondary" disabled={working} onClick={() => run("generate", () => api.generateTagsFor(caseId, view.language))} data-testid={`${test}-generate`}>
              {busy === "generate" ? "Generating…" : view.tags.length > 0 ? "Regenerate" : "Generate"}
            </Button>
            <Button size="sm" disabled={working || !dirty} onClick={() => run("save", () => api.saveTags(caseId, view.language, tags))} data-testid={`${test}-save`}>Save</Button>
            <Button size="sm" variant="ghost" disabled={working || !dirty} onClick={() => edit(view.tags)}>Discard changes</Button>
            <Button size="sm" disabled={working || dirty || view.tags.length === 0 || view.approval === "approved" || errors.length > 0} onClick={() => run("approve", () => api.approveTags(caseId, view.language))} data-testid={`${test}-approve`}>Approve</Button>
            <Button size="sm" variant="ghost" disabled={working || dirty || view.tags.length === 0 || view.approval === "rejected"} onClick={() => run("reject", () => api.rejectTags(caseId, view.language))}>Reject</Button>
          </div>
          {dirty && <p className="text-xs text-muted-foreground">You have unsaved changes. Saving withdraws an earlier approval.</p>}
          {error && <p role="alert" className="text-xs text-rose-600">{error}</p>}
          {(report.fixes ?? []).length > 0 && <p className="text-xs text-muted-foreground">Saving will fix the formatting: {(report.fixes ?? []).join("; ")}.</p>}
          {view.fixes.length > 0 && !dirty && <p className="text-xs text-muted-foreground">Fixed when written: {view.fixes.join("; ")}.</p>}
          {view.warnings.length > 0 && <p className="text-xs text-muted-foreground">The generator left out: {view.warnings.join("; ")}</p>}
          {report.issues.length > 0 && (
            <ul className="space-y-0.5 text-xs" data-testid={`${test}-issues`}>
              {report.issues.map((issue, i) => (
                <li key={`${issue.type}-${i}`} className={issue.severity === "error" ? "text-rose-600" : "text-amber-700 dark:text-amber-400"}>
                  {issue.severity === "error" ? "Error" : "Check"}{issue.tag ? ` — ${issue.tag}` : ""}: {issue.message}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {view.candidate && (
        <Card data-testid={`${test}-candidate`}>
          <CardHeader><CardTitle className="text-sm">A new suggestion is waiting</CardTitle></CardHeader>
          <CardContent className="space-y-2 text-sm">
            <p className="text-xs text-muted-foreground">Your tags were not changed. {view.candidate.report.char_count} / {view.max_chars} characters, {view.candidate.report.tag_count} tags, {view.candidate.report.ok ? "valid" : "has errors"}.</p>
            <p dir={dir} lang={view.language} className="rounded border p-2">{view.candidate.text}</p>
            <div className="flex gap-2">
              <Button size="sm" disabled={working} onClick={() => run("use", () => api.useTagSuggestion(caseId, view.language))}>Use the suggestion</Button>
              <Button size="sm" variant="ghost" disabled={working} onClick={() => run("drop", () => api.dropTagSuggestion(caseId, view.language))}>Keep mine</Button>
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader><CardTitle className="text-sm">Edit the tags one by one</CardTitle></CardHeader>
        <CardContent className="space-y-2">
          <ol className="space-y-1">
            {tags.map((tag, index) => (
              <li key={`${index}-${tag.text}`} className="flex items-center gap-1">
                <Input value={tag.text} dir={dir} lang={view.language} className="h-8 max-w-sm" aria-label={`Tag ${index + 1}`} onChange={(e) => edit(tags.map((t, i) => (i === index ? { ...t, text: e.target.value } : t)))} />
                <Button size="sm" variant="ghost" aria-label={`Move tag ${index + 1} up`} disabled={index === 0} onClick={() => move(index, -1)}><ArrowUp /></Button>
                <Button size="sm" variant="ghost" aria-label={`Move tag ${index + 1} down`} disabled={index === tags.length - 1} onClick={() => move(index, 1)}><ArrowDown /></Button>
                <Button size="sm" variant="ghost" aria-label={`Delete tag ${index + 1}`} onClick={() => edit(tags.filter((_, i) => i !== index))}><X /></Button>
              </li>
            ))}
          </ol>
          <form
            className="flex items-center gap-1"
            onSubmit={(e) => {
              e.preventDefault();
              const added = parseField(newTag, []);
              if (added.length > 0) edit([...tags, ...added]);
              setNewTag("");
            }}
          >
            <Input value={newTag} dir={dir} lang={view.language} className="h-8 max-w-sm" placeholder="Add your own tag" aria-label="New tag" onChange={(e) => setNewTag(e.target.value)} />
            <Button size="sm" variant="secondary" type="submit" disabled={newTag.trim() === ""}><Plus /> Add</Button>
          </form>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-sm">Breakdown</CardTitle></CardHeader>
        <CardContent className="space-y-3">
          {groups.length === 0 && <p className="text-xs text-muted-foreground">Nothing to show yet.</p>}
          {groups.map((group) => (
            <div key={group.key}>
              <p className="text-xs font-medium">{group.label}</p>
              <div className="mt-1 flex flex-wrap gap-1.5">
                {group.tags.map((text) => <Badge key={text} variant="outline" dir={dir} lang={view.language}>{text}</Badge>)}
              </div>
            </div>
          ))}
          <p className="text-xs text-muted-foreground">The breakdown is for inspection; the field above holds the list you copy.</p>
        </CardContent>
      </Card>
    </div>
  );
}

/** The YouTube tags of the case, one tab per configured language. Generating is paid; approval is yours: only approved tags go to the upload, in your order. */
export function TagsTab({ caseId, language, onLanguageChange, refreshKey }: LanguageTabProps) {
  const [data, setData] = useState<TagsOverview | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api.tags(caseId).then((overview) => {
      if (live) {
        setData(overview);
        setError(null);
      }
    }).catch((e) => live && setError(e instanceof Error ? e.message : "Failed to load the tags"));
    return () => { live = false; };
  }, [caseId, refreshKey]);

  const generate = async (force: boolean) => {
    setBusy(force ? "force" : "all");
    setError(null);
    try {
      setData(await api.generateTags(caseId, null, force));
    } catch (e) {
      setError(e instanceof Error ? e.message : "The generator failed");
    } finally {
      setBusy(null);
    }
  };

  const replace = (view: TagSetView) => setData((old) => (old ? { ...old, languages: old.languages.map((l) => (l.language === view.language ? view : l)) } : old));
  const languages = data?.languages.map((l) => l.language) ?? [];
  const current = data?.languages.find((l) => l.language === language) ?? data?.languages[0];
  const approved = data?.languages.filter((l) => l.approval === "approved").length ?? 0;

  return (
    <div className="space-y-4">
      {languages.length > 0 && <LanguagePicker languages={languages} value={current?.language ?? language} onChange={onLanguageChange} />}
      <Card>
        <CardHeader><CardTitle>YouTube tags</CardTitle></CardHeader>
        <CardContent className="space-y-2 text-sm">
          <p className="text-muted-foreground">
            Case-specific tags in the language of each film, written from the verified case facts and checked in code (length, duplicates, evidence, accusations). Up to {data?.max_chars ?? 500} characters per language. Your edits are never overwritten by a regeneration, and only approved tags go to the upload.
          </p>
          {data && <p data-testid="tags-counts">{approved} of {data.languages.length} languages approved.</p>}
          <div className="flex flex-wrap gap-2">
            <Button size="sm" onClick={() => generate(false)} disabled={busy != null} data-testid="tags-generate-all">{busy === "all" ? "Generating…" : "Generate missing or outdated"}</Button>
            <Button size="sm" variant="secondary" onClick={() => generate(true)} disabled={busy != null}>{busy === "force" ? "Generating…" : "Regenerate all"}</Button>
          </div>
          <p className="text-xs text-muted-foreground">A language that fails does not stop the others; each result is saved on its own.</p>
          {error && <p role="alert" className="text-xs text-rose-600">{error}</p>}
        </CardContent>
      </Card>
      {current && data && <LanguageTags key={`${current.language}-${current.generated_at}-${current.origin}-${current.tags.length}-${current.text}`} caseId={caseId} view={current} categories={data.categories} onChanged={replace} />}
    </div>
  );
}
