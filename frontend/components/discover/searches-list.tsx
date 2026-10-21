"use client";

import { useState } from "react";
import { Archive, ChevronDown, ChevronRight, Pencil, RotateCcw, Rocket, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import type { DiscoveredCaseStatus, Search, SuggestionRecord } from "@/lib/types";
import { formatDateTime } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { EmptyState } from "@/components/state";
import { Skeleton } from "@/components/ui/skeleton";
import { StartDialog } from "@/components/autopilot/start-dialog";

const STATUS_LABEL: Record<DiscoveredCaseStatus, string> = {
  free: "Free",
  created_not_published: "Created — not published",
  created_published: "Created and published",
  archived: "Archived",
};

const STATUS_BADGE_VARIANT: Record<DiscoveredCaseStatus, "default" | "info" | "success" | "warning"> = {
  free: "default",
  created_not_published: "info",
  created_published: "success",
  archived: "warning",
};

/** D2: criteria chips, derived from the (deterministic, server-computed)
 * generated name — avoids duplicating the country/year/resolution display
 * logic in the frontend. The trailing "#n" is redundant with the job id
 * already shown and is dropped. */
function criteriaChips(generatedName: string): string[] {
  return generatedName.split(" · ").filter((part) => !part.startsWith("#"));
}

export function SearchesList() {
  const searches = useApi(() => api.searches(), []);
  const [undo, setUndo] = useState<{ candidateId: number; title: string } | null>(null);

  if (searches.loading && !searches.data) {
    return (
      <Card>
        <CardContent className="space-y-2 py-4">
          <Skeleton className="h-10 w-full" />
          <Skeleton className="h-10 w-full" />
        </CardContent>
      </Card>
    );
  }

  const rows = searches.data ?? [];
  if (!rows.length) {
    return <EmptyState title="No Searches yet" description="Run a discovery search above to see it here." />;
  }

  return (
    <div className="space-y-2">
      {undo && (
        <div className="flex items-center justify-between rounded-md border border-border bg-muted px-3 py-2 text-sm">
          <span>Deleted &ldquo;{undo.title}&rdquo;.</span>
          <Button
            size="sm"
            variant="outline"
            onClick={async () => {
              await api.restoreCandidate(undo.candidateId);
              setUndo(null);
              searches.refetch();
            }}
          >
            Undo
          </Button>
        </div>
      )}
      {rows.map((search) => (
        <SearchRow key={search.id} search={search} onChanged={searches.refetch}
                  onDeleted={(candidateId, title) => setUndo({ candidateId, title })} />
      ))}
    </div>
  );
}

function SearchRow({
  search,
  onChanged,
  onDeleted,
}: {
  search: Search;
  onChanged: () => void;
  onDeleted: (candidateId: number, title: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [nameDraft, setNameDraft] = useState(search.name);

  async function saveRename() {
    setRenaming(false);
    const trimmed = nameDraft.trim();
    if (trimmed === search.display_name || (!trimmed && !search.display_name)) return;
    await api.renameSearch(search.id, trimmed || null);
    onChanged();
  }

  const chips = criteriaChips(search.generated_name);
  const { counts } = search;

  return (
    <Card>
      <CardContent className="py-3">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            {renaming ? (
              <div className="flex items-center gap-1.5">
                <ChevronRight className="size-3.5 shrink-0 text-transparent" aria-hidden />
                <Input
                  autoFocus
                  value={nameDraft}
                  onChange={(e) => setNameDraft(e.target.value)}
                  onBlur={saveRename}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") saveRename();
                    if (e.key === "Escape") { setNameDraft(search.name); setRenaming(false); }
                  }}
                  className="h-7 text-sm"
                />
              </div>
            ) : (
              <div className="flex items-center gap-1.5">
                <button
                  type="button"
                  onClick={() => setOpen((v) => !v)}
                  aria-expanded={open}
                  aria-label={open ? "Collapse" : "Expand"}
                  className="flex min-w-0 cursor-pointer items-center gap-1.5 text-left text-sm font-medium outline-none hover:text-foreground"
                >
                  {open ? <ChevronDown className="size-3.5 shrink-0" /> : <ChevronRight className="size-3.5 shrink-0" />}
                  <span className="truncate">{search.name}</span>
                </button>
                <button
                  type="button"
                  onClick={() => { setRenaming(true); setNameDraft(search.name); }}
                  aria-label="Rename this Search"
                  className="shrink-0 cursor-pointer text-muted-foreground outline-none hover:text-foreground"
                >
                  <Pencil className="size-3" />
                </button>
              </div>
            )}
            <div className="mt-1 flex flex-wrap items-center gap-1 pl-5">
              {chips.map((chip, i) => (
                <Badge key={i} variant="outline">{chip}</Badge>
              ))}
              <span className="text-xs text-muted-foreground">{formatDateTime(search.created_at)}</span>
            </div>
          </div>
          <div className="flex shrink-0 flex-wrap items-center gap-2 text-xs">
            <Badge variant="outline">{counts.total} total</Badge>
            <Badge variant="default">{counts.free} free</Badge>
            <Badge variant="info">{counts.created_not_published} not published</Badge>
            <Badge variant="success">{counts.created_published} published</Badge>
            <Badge variant="warning">{counts.archived} archived</Badge>
            <span className="text-muted-foreground">
              {search.status}{search.total_cost_eur != null ? ` · €${search.total_cost_eur.toFixed(3)}` : ""}
            </span>
          </div>
        </div>
        {open && <SearchExpandedView jobId={search.id} onChanged={onChanged} onDeleted={onDeleted} />}
      </CardContent>
    </Card>
  );
}

/** Start: the autopilot for one free suggestion, after the languages and the estimate are confirmed. */
function StartButton({ candidate }: { candidate: SuggestionRecord }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button size="sm" onClick={() => setOpen(true)} data-testid={`start-autopilot-${candidate.candidate_id}`}>
        <Rocket className="size-3.5" /> Start
      </Button>
      <StartDialog candidateId={candidate.candidate_id} title={candidate.title} open={open}
                   onClose={() => setOpen(false)} />
    </>
  );
}

function SearchExpandedView({
  jobId,
  onChanged,
  onDeleted,
}: {
  jobId: number;
  onChanged: () => void;
  onDeleted: (candidateId: number, title: string) => void;
}) {
  const history = useApi(() => api.discoveryHistory(undefined, jobId), [jobId]);
  const rows = (history.data ?? []).filter((r) => r.state !== "duplicate" && r.state !== "filtered");

  async function archive(c: SuggestionRecord) {
    await api.archiveCandidate(c.candidate_id);
    history.refetch();
    onChanged();
  }

  async function del(c: SuggestionRecord) {
    await api.deleteCandidate(c.candidate_id);
    history.refetch();
    onChanged();
    onDeleted(c.candidate_id, c.title);
  }

  async function restore(c: SuggestionRecord) {
    await api.restoreCandidate(c.candidate_id);
    history.refetch();
    onChanged();
  }

  if (history.loading && !history.data) {
    return <Skeleton className="mt-3 h-16 w-full" />;
  }
  if (!rows.length) {
    return <p className="mt-3 pl-5 text-xs text-muted-foreground">No discovered cases in this Search.</p>;
  }

  return (
    <div className="mt-3 space-y-2 border-t border-border pt-3 pl-5">
      {rows.map((c) => (
        <div key={c.candidate_id} className="flex flex-wrap items-center justify-between gap-2 text-sm">
          <div className="flex min-w-0 flex-wrap items-center gap-1.5">
            <span className="font-medium">{c.title}</span>
            {c.state === "deleted" ? (
              <Badge variant="danger">Deleted</Badge>
            ) : (
              <Badge variant={STATUS_BADGE_VARIANT[c.case_status]}>{STATUS_LABEL[c.case_status]}</Badge>
            )}
            {Object.entries(c.language_badges).map(([lang, state]) => (
              <Badge key={lang} variant={state === "not_created" ? "outline" : "default"}>
                {lang}: {state === "not_created" ? "—" : state}
              </Badge>
            ))}
          </div>
          <div className="flex shrink-0 gap-1">
            {c.case_status === "free" && c.state === "suggested" && <StartButton candidate={c} />}
            {c.state === "archived" || c.state === "deleted" ? (
              <Button size="sm" variant="outline" onClick={() => restore(c)}>
                <RotateCcw className="size-3.5" /> Restore
              </Button>
            ) : (
              <Button size="sm" variant="outline" onClick={() => archive(c)}>
                <Archive className="size-3.5" /> Archive
              </Button>
            )}
            {c.case_status === "free" && c.state !== "archived" && c.state !== "deleted" && (
              <Button size="sm" variant="ghost" onClick={() => del(c)}
                     title="Removes it from this Search only — it may be suggested again later. Archive instead to exclude it forever.">
                <Trash2 className="size-3.5" /> Delete
              </Button>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
