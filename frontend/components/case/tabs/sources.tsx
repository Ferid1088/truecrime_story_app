"use client";

import { useState } from "react";
import { ExternalLink, Plus, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatDateTime, langLabel } from "@/lib/format";
import type { Fact, Source, SourceDetail as SourceDetailData } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Sheet } from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { EmptyState, ErrorState } from "@/components/state";
import { TableSkeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";

const SOURCE_TYPES = ["youtube", "news", "court", "police", "book", "documentary", "article", "other"];
const LANGS = ["en", "de", "fa", "ar", "unknown"];

const CONTENT_STATUS_LABEL: Record<string, string> = {
  full_text: "full text",
  partial_text: "partial",
  partial: "partial",
  summary_only: "summary only",
  metadata_only: "metadata only",
  unavailable: "unavailable",
};

function contentStatusVariant(s: string): "success" | "warning" | "default" {
  if (s === "full_text") return "success";
  if (s === "summary_only") return "default";
  return "warning";
}

export function SourcesTab({ caseId, refreshKey }: { caseId: number; refreshKey: number }) {
  const { data: sources, error, loading, refetch } = useApi(
    () => api.listSources(caseId),
    [caseId, refreshKey],
  );
  const { data: facts } = useApi(() => api.listFacts(caseId), [caseId, refreshKey]);
  const [selected, setSelected] = useState<Source | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);

  async function removeSelected() {
    if (!selected) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await api.deleteSource(caseId, selected.id);
      setConfirmDelete(false);
      setSelected(null);
      refetch();
    } catch (e) {
      setDeleteError(e instanceof Error ? e.message : String(e));
    } finally {
      setDeleting(false);
    }
  }

  const factsFor = (id: number) => (facts ?? []).filter((f) => f.source_ids.includes(id));

  return (
    <div>
      <div className="mb-3 flex justify-end">
        <Button size="sm" variant="secondary" onClick={() => setShowAdd(true)}>
          <Plus className="size-3.5" /> Add Source
        </Button>
      </div>

      {loading && <TableSkeleton rows={5} cols={6} />}
      {error && <ErrorState message={error} onRetry={refetch} />}

      {sources && sources.length === 0 && (
        <EmptyState
          title="No sources yet"
          description="Add articles, videos, court documents or other material. Sources stay separate from the story."
          action={
            <Button size="sm" variant="secondary" onClick={() => setShowAdd(true)}>
              <Plus className="size-3.5" /> Add the first source
            </Button>
          }
        />
      )}

      {sources && sources.length > 0 && (
        <Card>
          <CardContent className="p-0">
            <Table>
              <THead>
                <TR className="hover:bg-transparent">
                  <TH>Source</TH>
                  <TH>Publisher</TH>
                  <TH>Type</TH>
                  <TH>Language</TH>
                  <TH>Content</TH>
                  <TH>Provider</TH>
                  <TH className="text-right">Reliability</TH>
                  <TH>URL</TH>
                  <TH>Facts</TH>
                </TR>
              </THead>
              <TBody>
                {sources.map((s) => (
                  <TR key={s.id} className="cursor-pointer" onClick={() => setSelected(s)}>
                    <TD className="max-w-72">
                      <span className="block truncate font-medium">{s.title}</span>
                    </TD>
                    <TD className="text-muted-foreground">{s.publisher ?? "—"}</TD>
                    <TD>
                      <Badge variant="outline">{s.source_type}</Badge>
                    </TD>
                    <TD className="text-muted-foreground">
                      {langLabel(s.language)}
                      {s.detected_language &&
                        s.declared_language &&
                        s.detected_language !== s.declared_language && (
                          <span
                            className="ml-1 inline-block size-1.5 rounded-full bg-amber-500 align-middle"
                            title={`declared ${s.declared_language} · detected ${s.detected_language}`}
                          />
                        )}
                    </TD>
                    <TD>
                      <Badge variant={contentStatusVariant(s.content_status)}>
                        {CONTENT_STATUS_LABEL[s.content_status] ?? s.content_status}
                      </Badge>
                    </TD>
                    <TD>
                      {s.research_provider ? (
                        <Badge variant="accent">{s.research_provider}</Badge>
                      ) : (
                        <span className="text-xs text-muted-foreground">manual</span>
                      )}
                    </TD>
                    <TD className="text-right tabular-nums">{Math.round(s.reliability_score * 100)}%</TD>
                    <TD>
                      <a
                        href={s.url}
                        target="_blank"
                        rel="noopener noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                      >
                        open <ExternalLink className="size-3" />
                      </a>
                    </TD>
                    <TD className="tabular-nums">{factsFor(s.id).length}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      )}

      <Sheet open={!!selected} onClose={() => setSelected(null)} title={selected?.title ?? ""}>
        {selected && (
          <SourceDetail
            source={selected}
            facts={factsFor(selected.id)}
            onDelete={() => setConfirmDelete(true)}
          />
        )}
      </Sheet>

      <Dialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        title="Delete source?"
        description="The source is removed permanently. Facts that cite it keep their claims but lose this source link."
      >
        {deleteError && <p className="mb-3 text-xs text-rose-500">{deleteError}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => setConfirmDelete(false)}>
            Cancel
          </Button>
          <Button variant="destructive" size="sm" onClick={removeSelected} loading={deleting}>
            <Trash2 className="size-3.5" /> Delete source
          </Button>
        </div>
      </Dialog>

      <AddSourceDialog
        open={showAdd}
        onClose={() => setShowAdd(false)}
        caseId={caseId}
        onAdded={() => {
          setShowAdd(false);
          refetch();
        }}
      />
    </div>
  );
}

function SourceDetail({
  source: s,
  facts,
  onDelete,
}: {
  source: Source;
  facts: Fact[];
  onDelete: () => void;
}) {
  return (
    <div className="space-y-4 text-sm">
      <div className="grid grid-cols-2 gap-3">
        <Field label="Type" value={s.source_type} />
        <Field
          label="Language"
          value={
            langLabel(s.language) +
            (s.detected_language && s.detected_language !== s.declared_language
              ? ` (declared ${s.declared_language ?? "?"} → detected ${s.detected_language})`
              : s.detected_language
                ? " (detected)"
                : "")
          }
        />
        {s.language_detection_method && (
          <Field
            label="Lang detection"
            value={
              s.language_detection_method +
              (s.language_confidence != null
                ? ` · ${Math.round(s.language_confidence * 100)}%`
                : "")
            }
          />
        )}
        <Field label="Publisher" value={s.publisher ?? "—"} />
        <Field label="Reliability" value={`${Math.round(s.reliability_score * 100)}%`} />
        <Field label="Added" value={formatDateTime(s.created_at)} />
        <Field label="Authorized text" value={s.is_authorized_text ? "Yes" : "No"} />
        <Field label="Provider" value={s.research_provider ?? "manual"} />
        <Field label="Published" value={s.published_at ?? "—"} />
        {s.retrieved_at && <Field label="Retrieved" value={formatDateTime(s.retrieved_at)} />}
        <Field label="Status" value={s.status} />
        <Field
          label="Content"
          value={
            (CONTENT_STATUS_LABEL[s.content_status] ?? s.content_status) +
            (s.raw_text_length > 0 ? ` · ${s.raw_text_length.toLocaleString()} chars` : "")
          }
        />
        {s.source_family && <Field label="Source family" value={s.source_family} />}
        {s.retrieval_method && <Field label="Retrieved via" value={s.retrieval_method} />}
      </div>
      {s.retrieval_notes && (
        <p className="text-xs text-muted-foreground">{s.retrieval_notes}</p>
      )}

      <SourceProvenance caseId={s.case_id} sourceId={s.id} />

      <SourceChunks caseId={s.case_id} sourceId={s.id} count={s.chunk_count} />

      {s.value_flags.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {s.value_flags.map((f) => (
            <Badge key={f} variant="info">
              {f.replace(/_/g, " ")}
            </Badge>
          ))}
        </div>
      )}

      {s.summary_en && (
        <div>
          <p className="mb-1 text-xs font-medium text-muted-foreground">Summary (English, normalized)</p>
          <p className="whitespace-pre-wrap text-foreground/90">{s.summary_en}</p>
        </div>
      )}

      {s.summary && (
        <div>
          <p className="mb-1 text-xs font-medium text-muted-foreground">
            Summary {s.summary_en ? `(original ${langLabel(s.language)})` : ""}
          </p>
          <p className="whitespace-pre-wrap text-foreground/90" dir="auto">{s.summary}</p>
        </div>
      )}

      <div>
        <p className="mb-1 text-xs font-medium text-muted-foreground">URL</p>
        <a
          href={s.url}
          target="_blank"
          rel="noopener noreferrer"
          className="break-all text-xs text-primary hover:underline"
        >
          {s.url}
        </a>
      </div>

      {s.notes && (
        <div>
          <p className="mb-1 text-xs font-medium text-muted-foreground">Notes</p>
          <p className="whitespace-pre-wrap text-foreground/90" dir="auto">{s.notes}</p>
        </div>
      )}

      {s.raw_text && (
        <div>
          <p className="mb-1 text-xs font-medium text-muted-foreground">Available text</p>
          <div className="max-h-64 overflow-y-auto rounded-md border border-border bg-subtle p-3">
            <p className="whitespace-pre-wrap text-xs leading-5 text-foreground/80" dir="auto">
              {s.raw_text}
            </p>
          </div>
        </div>
      )}

      <div>
        <p className="mb-1 text-xs font-medium text-muted-foreground">
          Facts extracted from this source ({facts.length})
        </p>
        {facts.length === 0 ? (
          <p className="text-xs text-muted-foreground">None — run research after adding sources.</p>
        ) : (
          <ul className="space-y-1.5">
            {facts.map((f) => (
              <li key={f.id} className="rounded-md border border-border px-2.5 py-1.5 text-xs" dir="auto">
                {f.claim}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="border-t border-border pt-3">
        <Button variant="ghost" size="sm" className="text-rose-500 hover:text-rose-400" onClick={onDelete}>
          <Trash2 className="size-3.5" /> Delete this source
        </Button>
      </div>
    </div>
  );
}

function SourceProvenance({ caseId, sourceId }: { caseId: number; sourceId: number }) {
  const { data } = useApi<SourceDetailData>(
    () => api.caseSource(caseId, sourceId),
    [caseId, sourceId],
  );
  const paths = data?.discovered_by ?? [];
  if (paths.length === 0) return null;
  return (
    <div>
      <p className="mb-1 text-xs font-medium text-muted-foreground">Discovered by</p>
      <ul className="space-y-1.5">
        {paths.map((p, i) => (
          <li key={i} className="rounded-md border border-border px-2.5 py-1.5 text-xs">
            <span className="font-medium" dir="auto">{p.query}</span>
            <span className="mt-0.5 block text-[10px] text-muted-foreground">
              language: {p.language}
              {p.purpose ? ` · purpose: ${p.purpose}` : ""}
              {` · round ${p.round}`}
              {p.link_type !== "canonical" ? ` · ${p.link_type}` : ""}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="mt-0.5">{value}</p>
    </div>
  );
}

function SourceChunks({
  caseId,
  sourceId,
  count,
}: {
  caseId: number;
  sourceId: number;
  count: number;
}) {
  const [open, setOpen] = useState(false);
  const { data: chunks } = useApi(
    () => (open ? api.sourceChunks(caseId, sourceId) : Promise.resolve([])),
    [caseId, sourceId, open],
  );
  if (!count) return null;
  return (
    <div>
      <button
        onClick={() => setOpen((o) => !o)}
        className="cursor-pointer text-xs font-medium text-muted-foreground hover:text-foreground"
      >
        {open ? "Hide" : "Show"} {count} chunk{count === 1 ? "" : "s"}
      </button>
      {open && chunks && chunks.length > 0 && (
        <div className="mt-1.5 max-h-64 space-y-2 overflow-y-auto rounded-md border border-border bg-subtle p-3">
          {chunks.map((c) => (
            <div key={c.id}>
              <p className="text-[10px] text-muted-foreground">
                chunk {c.chunk_index} · ~{c.token_count} tok
                {c.page_or_location ? ` · ${c.page_or_location}` : ""}
              </p>
              <p className="whitespace-pre-wrap text-xs leading-5 text-foreground/80" dir="auto">
                {c.text}
              </p>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function AddSourceDialog({
  open,
  onClose,
  caseId,
  onAdded,
}: {
  open: boolean;
  onClose: () => void;
  caseId: number;
  onAdded: () => void;
}) {
  const [form, setForm] = useState({
    title: "",
    url: "",
    source_type: "article",
    language: "unknown",
    publisher: "",
    notes: "",
    raw_text: "",
    reliability_score: 0.5,
    is_authorized_text: false,
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const set = (k: string, v: string | number | boolean) => setForm((f) => ({ ...f, [k]: v }));

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await api.addSource(caseId, {
        title: form.title.trim(),
        url: form.url.trim(),
        source_type: form.source_type,
        language: form.language,
        publisher: form.publisher || null,
        notes: form.notes || null,
        raw_text: form.raw_text || null,
        reliability_score: form.reliability_score,
        is_authorized_text: form.is_authorized_text,
      });
      onAdded();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Add Source"
      description="Register a source for this case. Raw text is only used for analysis when you are authorized to use it."
      className="max-w-xl"
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="sm:col-span-2">
          <label className="mb-1 block text-xs font-medium text-muted-foreground">Title</label>
          <Input value={form.title} onChange={(e) => set("title", e.target.value)} />
        </div>
        <div className="sm:col-span-2">
          <label className="mb-1 block text-xs font-medium text-muted-foreground">URL</label>
          <Input value={form.url} onChange={(e) => set("url", e.target.value)} placeholder="https://…" />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-muted-foreground">Type</label>
          <Select value={form.source_type} onChange={(e) => set("source_type", e.target.value)}>
            {SOURCE_TYPES.map((t) => (
              <option key={t} value={t}>
                {t[0].toUpperCase() + t.slice(1)}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-muted-foreground">Language</label>
          <Select value={form.language} onChange={(e) => set("language", e.target.value)}>
            {LANGS.map((l) => (
              <option key={l} value={l}>
                {langLabel(l)}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-muted-foreground">Publisher</label>
          <Input value={form.publisher} onChange={(e) => set("publisher", e.target.value)} />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-muted-foreground">
            Reliability — {Math.round(form.reliability_score * 100)}%
          </label>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={form.reliability_score}
            onChange={(e) => set("reliability_score", Number(e.target.value))}
            className="mt-2 w-full accent-primary"
            aria-label="Reliability score"
          />
        </div>
        <div className="sm:col-span-2">
          <label className="mb-1 block text-xs font-medium text-muted-foreground">Notes</label>
          <Textarea rows={2} value={form.notes} onChange={(e) => set("notes", e.target.value)} />
        </div>
        <div className="sm:col-span-2">
          <label className="mb-1 block text-xs font-medium text-muted-foreground">
            Raw text (only if authorized)
          </label>
          <Textarea rows={4} value={form.raw_text} onChange={(e) => set("raw_text", e.target.value)} />
        </div>
        <div className="sm:col-span-2">
          <Checkbox
            checked={form.is_authorized_text}
            onChange={(e) => set("is_authorized_text", e.target.checked)}
            label="I am authorized to use this text"
          />
        </div>
      </div>
      {error && <p className="mt-3 text-xs text-rose-500">{error}</p>}
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={onClose}>
          Cancel
        </Button>
        <Button size="sm" onClick={submit} loading={busy} disabled={!form.title.trim() || !form.url.trim()}>
          Add Source
        </Button>
      </div>
    </Dialog>
  );
}
