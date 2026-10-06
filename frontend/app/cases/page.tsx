"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Plus, Search } from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatDate, formatDateTime, langLabel } from "@/lib/format";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { PageHeader } from "@/components/page-header";
import { EmptyState, ErrorState } from "@/components/state";
import { CaseStatusBadge } from "@/components/status-badge";
import { TableSkeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { cn } from "@/lib/utils";

const FILTERS: { value: string; label: string }[] = [
  { value: "", label: "All" },
  { value: "new", label: "New" },
  { value: "researching", label: "Researching" },
  { value: "researched", label: "Research Complete" },
  { value: "writing", label: "Writing" },
  { value: "completed", label: "Completed" },
  { value: "rejected", label: "Rejected" },
];

export default function CasesPage() {
  const router = useRouter();
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [showCreate, setShowCreate] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q), 250);
    return () => clearTimeout(t);
  }, [q]);

  const { data, error, loading, refetch } = useApi(
    () => api.listCases({ status: status || undefined, q: debouncedQ || undefined }),
    [status, debouncedQ],
  );

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
        <div className="flex flex-wrap gap-1">
          {FILTERS.map((f) => (
            <button
              key={f.value}
              onClick={() => setStatus(f.value)}
              className={cn(
                "cursor-pointer rounded-full border px-2.5 py-1 text-xs transition-colors",
                status === f.value
                  ? "border-primary bg-accent text-accent-foreground"
                  : "border-border text-muted-foreground hover:bg-muted hover:text-foreground",
              )}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {loading && <TableSkeleton rows={8} cols={7} />}
      {error && <ErrorState message={error} onRetry={refetch} />}

      {data && data.length === 0 && (
        <EmptyState
          title="No cases match"
          description={
            debouncedQ || status
              ? "Try clearing the search or changing the status filter."
              : "Discover new cases or create one manually."
          }
        />
      )}

      {data && data.length > 0 && (
        <Card>
          <CardContent className="p-0">
            <Table>
              <THead>
                <TR className="hover:bg-transparent">
                  <TH>Case</TH>
                  <TH>Status</TH>
                  <TH>Lang</TH>
                  <TH className="text-right">Sources</TH>
                  <TH className="text-right">Facts</TH>
                  <TH className="text-right">Contradictions</TH>
                  <TH className="text-right">Versions</TH>
                  <TH>Created</TH>
                  <TH>Last activity</TH>
                </TR>
              </THead>
              <TBody>
                {data.map((c) => (
                  <TR key={c.id} className="cursor-pointer" onClick={() => router.push(`/cases/${c.id}`)}>
                    <TD className="max-w-72">
                      <span className="block truncate font-medium">{c.title}</span>
                    </TD>
                    <TD>
                      <CaseStatusBadge status={c.status} />
                    </TD>
                    <TD className="text-muted-foreground">{langLabel(c.language)}</TD>
                    <TD className="text-right tabular-nums">{c.sources}</TD>
                    <TD className="text-right tabular-nums">{c.facts}</TD>
                    <TD className="text-right tabular-nums">{c.contradictions}</TD>
                    <TD className="text-right tabular-nums">{c.story_versions || "—"}</TD>
                    <TD className="text-muted-foreground">{formatDate(c.created_at)}</TD>
                    <TD className="text-muted-foreground">{formatDateTime(c.last_activity)}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>
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
  const [title, setTitle] = useState("");
  const [language, setLanguage] = useState("fa");
  const [summary, setSummary] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    if (!title.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.createCase({
        canonical_title: title.trim(),
        language,
        summary: summary.trim() || undefined,
      });
      onCreated(res.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onClose={onClose} title="New Case" description="Register a case manually.">
      <div className="space-y-3">
        <div>
          <label className="mb-1 block text-xs font-medium text-muted-foreground">Case title</label>
          <Input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="e.g. The Disappearance of…" />
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-muted-foreground">Primary language</label>
          <Select value={language} onChange={(e) => setLanguage(e.target.value)}>
            <option value="en">English</option>
            <option value="de">German</option>
            <option value="fa">Persian</option>
            <option value="ar">Arabic</option>
          </Select>
        </div>
        <div>
          <label className="mb-1 block text-xs font-medium text-muted-foreground">Summary (optional)</label>
          <Textarea value={summary} onChange={(e) => setSummary(e.target.value)} rows={3} />
        </div>
        {error && <p className="text-xs text-rose-500">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button size="sm" onClick={submit} loading={busy} disabled={!title.trim()}>
            Create Case
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
