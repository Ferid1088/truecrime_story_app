"use client";

import { Suspense, useEffect, useId, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Plus, Search } from "lucide-react";
import { api, duplicateConflict } from "@/lib/api";
import type { DuplicateConflict, ResolutionStatus } from "@/lib/types";
import { useApi } from "@/lib/hooks";
import { langLabel } from "@/lib/format";
import { Button } from "@/components/ui/button";
import { CaseOverview } from "@/components/case/overview";
import { CaseControls } from "@/components/case/controls";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState } from "@/components/state";
import { resolutionLabel } from "@/components/status-badge";
import { DuplicateNotice } from "@/components/lifecycle/duplicate-notice";
import { splitList } from "@/components/lifecycle/shared";
import { TableSkeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

const FILTERS: { value: string; label: string }[] = [
  { value: "", label: "All" },
  { value: "new", label: "New" },
  { value: "researching", label: "Researching" },
  { value: "researched", label: "Research Complete" },
  { value: "writing", label: "Writing" },
  { value: "producing", label: "Producing" },
  { value: "rendered", label: "Film Rendered" },
  { value: "published", label: "Published" },
  { value: "completed", label: "Completed" },
  { value: "rejected", label: "Rejected" },
];

/** Resolution filter (?resolution=): solved / unsolved / under review / all. */
const RESOLUTION_FILTERS: { value: ResolutionStatus | ""; label: string }[] = [
  { value: "", label: "All" },
  { value: "SOLVED", label: "Solved" },
  { value: "UNSOLVED", label: "Unsolved" },
  { value: "STATUS_UNDER_REVIEW", label: "Under review" },
  { value: "UNKNOWN", label: "Unknown" },
];

function parseResolution(value: string | null): ResolutionStatus | "" {
  const v = (value ?? "").toUpperCase();
  return RESOLUTION_FILTERS.some((f) => f.value === v) ? (v as ResolutionStatus | "") : "";
}

export default function CasesPage() {
  return (
    <Suspense fallback={<TableSkeleton rows={8} cols={7} />}>
      <CasesView />
    </Suspense>
  );
}

function FilterChip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "cursor-pointer rounded-full border px-2.5 py-1 text-xs transition-colors",
        active
          ? "border-primary bg-accent text-accent-foreground"
          : "border-border text-muted-foreground hover:bg-muted hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

function CasesView() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const resolution = parseResolution(searchParams.get("resolution"));
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [showCreate, setShowCreate] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q), 250);
    return () => clearTimeout(t);
  }, [q]);

  const { data, error, loading, refetch } = useApi(
    () => api.listCases({ status: status || undefined, q: debouncedQ || undefined, resolution }),
    [status, debouncedQ, resolution],
  );

  function setResolution(value: ResolutionStatus | "") {
    const params = new URLSearchParams(searchParams.toString());
    if (value) params.set("resolution", value);
    else params.delete("resolution");
    const qs = params.toString();
    window.history.replaceState(null, "", qs ? `?${qs}` : window.location.pathname);
  }

  return (
    <div>
      <PageHeader
        title="Cases"
        description="Your case library — everything the system has investigated."
        actions={
          <Button size="sm" onClick={() => setShowCreate(true)}>
            <Plus className="size-4" /> New Case
          </Button>
        }
      />

      <div className="mb-4 flex flex-wrap items-center gap-3">
        <div className="relative w-64">
          <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search cases…"
            className="pl-8"
          />
        </div>
        <div className="flex flex-wrap gap-1" role="group" aria-label="Workflow status">
          {FILTERS.map((f) => (
            <FilterChip key={f.value} active={status === f.value} onClick={() => setStatus(f.value)}>
              {f.label}
            </FilterChip>
          ))}
        </div>
      </div>
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <span className="text-xs font-medium text-muted-foreground">Case status</span>
        <div className="flex flex-wrap gap-1" role="group" aria-label="Case status (solved or unsolved)">
          {RESOLUTION_FILTERS.map((f) => (
            <FilterChip key={f.value || "all"} active={resolution === f.value} onClick={() => setResolution(f.value)}>
              {f.label}
            </FilterChip>
          ))}
        </div>
      </div>

      {loading && <TableSkeleton rows={8} cols={7} />}
      {error && <ErrorState message={error} onRetry={refetch} />}

      {data && data.length === 0 && (
        <EmptyState
          title="No cases match"
          description={
            debouncedQ || status || resolution
              ? "Try clearing the search or changing the status filters."
              : "Discover new cases or create one manually."
          }
        />
      )}

      {data && data.length > 0 && (
        <CaseOverview
          idPrefix="cases"
          dateLabel="Created"
          href={(id) => `/cases/${id}`}
          items={data.map((c) => ({
            id: c.id,
            title: c.title,
            date: c.created_at,
            archived: c.archived,
            archiveReason: c.archive_reason,
            note: `${c.status} · ${langLabel(c.language)}`,
          }))}
          onArchive={async (id) => {
            await api.archiveCase(id);
            refetch();
          }}
          onRestore={async (id) => {
            await api.unarchiveCase(id);
            refetch();
          }}
          rowActions={(item) => (
            <CaseControls compact caseId={item.id} caseTitle={item.title} onChanged={refetch} />
          )}
        />
      )}

      <CreateCaseDialog
        open={showCreate}
        onClose={() => setShowCreate(false)}
        onCreated={(id) => router.push(`/cases/${id}`)}
      />
    </div>
  );
}

function CreateCaseDialog({
  open,
  onClose,
  onCreated,
}: {
  open: boolean;
  onClose: () => void;
  onCreated: (id: number) => void;
}) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="New Case"
      description="Register a case manually. People, place and date let the duplicate checker recognise the same case under another title."
      className="max-h-[calc(100vh-2rem)] overflow-y-auto"
    >
      {open && <CreateCaseForm onCancel={onClose} onCreated={onCreated} />}
    </Dialog>
  );
}

function CreateCaseForm({ onCancel, onCreated }: { onCancel: () => void; onCreated: (id: number) => void }) {
  const [title, setTitle] = useState("");
  const [language, setLanguage] = useState("fa");
  const [resolution, setResolution] = useState<ResolutionStatus>("UNKNOWN");
  const [people, setPeople] = useState("");
  const [aliases, setAliases] = useState("");
  const [location, setLocation] = useState("");
  const [incidentDate, setIncidentDate] = useState("");
  const [summary, setSummary] = useState("");
  const [busy, setBusy] = useState<"create" | "force" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState<DuplicateConflict | null>(null);
  const ids = useId();

  async function submit(force = false) {
    if (!title.trim()) return;
    setBusy(force ? "force" : "create");
    setError(null);
    try {
      const res = await api.createCase({
        canonical_title: title.trim(),
        language,
        summary: summary.trim() || undefined,
        resolution_status: resolution,
        people: splitList(people),
        aliases: splitList(aliases),
        location: location.trim() || null,
        incident_date: incidentDate.trim() || null,
        force,
      });
      onCreated(res.id);
    } catch (e) {
      const dup = duplicateConflict(e);
      if (dup) setConflict(dup);
      else setError(e instanceof Error ? e.message : String(e));
      setBusy(null);
    }
  }

  // Any edit invalidates the duplicate verdict: the next create checks again.
  function edit<T>(set: (v: T) => void) {
    return (v: T) => {
      set(v);
      setConflict(null);
    };
  }

  const label = "mb-1 block text-xs font-medium text-muted-foreground";
  return (
    <div className="space-y-3">
      <div>
        <label htmlFor={`${ids}-title`} className={label}>
          Case title
        </label>
        <Input
          id={`${ids}-title`}
          value={title}
          onChange={(e) => edit(setTitle)(e.target.value)}
          placeholder="e.g. The Disappearance of…"
        />
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor={`${ids}-status`} className={label}>
            Case status
          </label>
          <Select
            id={`${ids}-status`}
            value={resolution}
            onChange={(e) => setResolution(e.target.value as ResolutionStatus)}
          >
            {(["UNKNOWN", "SOLVED", "UNSOLVED", "STATUS_UNDER_REVIEW"] as const).map((s) => (
              <option key={s} value={s}>
                {resolutionLabel(s)}
              </option>
            ))}
          </Select>
        </div>
        <div>
          <label htmlFor={`${ids}-language`} className={label}>
            Primary language
          </label>
          <Select id={`${ids}-language`} value={language} onChange={(e) => setLanguage(e.target.value)}>
            <option value="en">English</option>
            <option value="de">German</option>
            <option value="fa">Persian</option>
            <option value="ar">Arabic</option>
          </Select>
        </div>
      </div>
      <div>
        <label htmlFor={`${ids}-people`} className={label}>
          People (victims, suspects — comma-separated)
        </label>
        <Input
          id={`${ids}-people`}
          value={people}
          onChange={(e) => edit(setPeople)(e.target.value)}
          placeholder="e.g. Inga Gehricke, …"
        />
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor={`${ids}-location`} className={label}>
            Location
          </label>
          <Input
            id={`${ids}-location`}
            value={location}
            onChange={(e) => edit(setLocation)(e.target.value)}
            placeholder="City, region, country"
          />
        </div>
        <div>
          <label htmlFor={`${ids}-date`} className={label}>
            Incident date
          </label>
          <Input
            id={`${ids}-date`}
            value={incidentDate}
            onChange={(e) => edit(setIncidentDate)(e.target.value)}
            placeholder="YYYY-MM-DD, YYYY-MM or YYYY"
          />
        </div>
      </div>
      <div>
        <label htmlFor={`${ids}-aliases`} className={label}>
          Also known as (optional, comma-separated)
        </label>
        <Input
          id={`${ids}-aliases`}
          value={aliases}
          onChange={(e) => edit(setAliases)(e.target.value)}
          placeholder="Other titles the case is known by"
        />
      </div>
      <div>
        <label htmlFor={`${ids}-summary`} className={label}>
          Summary (optional)
        </label>
        <Textarea id={`${ids}-summary`} value={summary} onChange={(e) => setSummary(e.target.value)} rows={3} />
      </div>
      {conflict && <DuplicateNotice conflict={conflict} onForce={() => submit(true)} forcing={busy === "force"} />}
      {error && <p className="text-xs text-rose-500">{error}</p>}
      <div className="flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={onCancel}>
          Cancel
        </Button>
        <Button
          size="sm"
          onClick={() => submit(false)}
          loading={busy === "create"}
          disabled={!title.trim() || busy !== null || conflict != null}
        >
          Create Case
        </Button>
      </div>
    </div>
  );
}
