"use client";

import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { api } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { confidenceLabel, langLabel } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState, ErrorState } from "@/components/state";
import { Select } from "@/components/ui/select";
import { TableSkeleton } from "@/components/ui/skeleton";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { cn } from "@/lib/utils";
import type { Fact } from "@/lib/types";

const CONFIDENCE_VARIANT = {
  high: "success",
  medium: "warning",
  low: "danger",
} as const;

export function FactsTab({ caseId, refreshKey }: { caseId: number; refreshKey: number }) {
  const { data: facts, error, loading, refetch } = useApi(
    () => api.listFacts(caseId),
    [caseId, refreshKey],
  );
  const { data: sources } = useApi(() => api.listSources(caseId), [caseId]);
  const [open, setOpen] = useState<Set<number>>(new Set());
  const [verFilter, setVerFilter] = useState("");
  const [langFilter, setLangFilter] = useState("");
  const [catFilter, setCatFilter] = useState("");

  const languages = useMemo(
    () => [...new Set((facts ?? []).map((f) => f.original_language))].sort(),
    [facts],
  );
  const categories = useMemo(
    () => [...new Set((facts ?? []).map((f) => f.category))].sort(),
    [facts],
  );

  const filtered = useMemo(
    () =>
      (facts ?? []).filter(
        (f) =>
          (verFilter === "" || (verFilter === "disputed" ? f.disputed : !f.disputed)) &&
          (langFilter === "" || f.original_language === langFilter) &&
          (catFilter === "" || f.category === catFilter),
      ),
    [facts, verFilter, langFilter, catFilter],
  );

  const sourceTitle = (id: number) => sources?.find((s) => s.id === id)?.title ?? `Source #${id}`;

  const toggle = (id: number) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  if (loading) return <TableSkeleton rows={6} cols={5} />;
  if (error) return <ErrorState message={error} onRetry={refetch} />;
  if (!facts || facts.length === 0)
    return (
      <EmptyState
        title="No facts extracted"
        description="Add sources, then run research to extract factual claims."
      />
    );

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <Select value={verFilter} onChange={(e) => setVerFilter(e.target.value)} className="w-40">
          <option value="">All facts</option>
          <option value="verified">Verified only</option>
          <option value="disputed">Disputed only</option>
        </Select>
        <Select value={langFilter} onChange={(e) => setLangFilter(e.target.value)} className="w-40">
          <option value="">All languages</option>
          {languages.map((l) => (
            <option key={l} value={l}>
              {langLabel(l)}
            </option>
          ))}
        </Select>
        <Select value={catFilter} onChange={(e) => setCatFilter(e.target.value)} className="w-44">
          <option value="">All categories</option>
          {categories.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </Select>
        <span className="text-xs text-muted-foreground tabular-nums">
          {filtered.length} of {facts.length}
        </span>
      </div>

      {filtered.length === 0 ? (
        <EmptyState title="No facts match" description="Adjust or clear the filters above." />
      ) : (
        <Card>
          <CardContent className="p-0">
            <Table>
              <THead>
                <TR className="hover:bg-transparent">
                  <TH className="w-6" />
                  <TH>Claim</TH>
                  <TH>Category</TH>
                  <TH>Confidence</TH>
                  <TH className="text-right">Sources</TH>
                  <TH>Disputed</TH>
                </TR>
              </THead>
              <TBody>
                {filtered.map((f) => (
                  <FactsRow
                    key={f.id}
                    fact={f}
                    expanded={open.has(f.id)}
                    onToggle={() => toggle(f.id)}
                    sourceTitle={sourceTitle}
                  />
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function FactsRow({
  fact: f,
  expanded,
  onToggle,
  sourceTitle,
}: {
  fact: Fact;
  expanded: boolean;
  onToggle: () => void;
  sourceTitle: (id: number) => string;
}) {
  const conf = confidenceLabel(f.confidence);
  const showOriginal = f.original_claim && f.original_claim !== f.claim;
  return (
    <>
      <TR className="cursor-pointer" onClick={onToggle}>
        <TD className="w-6 pr-0">
          {expanded ? (
            <ChevronDown className="size-3.5 text-muted-foreground" />
          ) : (
            <ChevronRight className="size-3.5 text-muted-foreground" />
          )}
        </TD>
        <TD className="max-w-xl">
          <span dir="auto" className={cn("block", !expanded && "truncate")}>
            {f.claim}
          </span>
        </TD>
        <TD>
          <Badge variant="outline">{f.category}</Badge>
        </TD>
        <TD>
          <Badge variant={CONFIDENCE_VARIANT[conf]}>{conf}</Badge>
        </TD>
        <TD className="text-right tabular-nums">{f.source_ids.length}</TD>
        <TD>{f.disputed ? <Badge variant="danger">disputed</Badge> : "—"}</TD>
      </TR>
      {expanded && (
        <TR className="hover:bg-transparent">
          <TD />
          <TD colSpan={5} className="bg-subtle/50">
            <div className="space-y-3">
              {showOriginal && (
                <div>
                  <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                    Original claim ({langLabel(f.original_language)})
                  </p>
                  <p
                    className="text-xs leading-5 text-foreground/80"
                    dir={["fa", "ar"].includes(f.original_language) ? "rtl" : "auto"}
                  >
                    {f.original_claim}
                  </p>
                </div>
              )}
              {f.narrative_value && (
                <div>
                  <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                    Narrative value
                  </p>
                  <Badge variant="info">{f.narrative_value.replace(/_/g, " ")}</Badge>
                </div>
              )}
              {f.event_date && (
                <div>
                  <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                    Event date
                  </p>
                  <p className="font-mono text-xs text-foreground/80">{f.event_date}</p>
                </div>
              )}
              <div>
                <p className="mb-1 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                  Supporting sources
                </p>
                {f.source_ids.length === 0 ? (
                  <p className="text-xs text-muted-foreground">No linked sources.</p>
                ) : (
                  <ul className="space-y-0.5">
                    {f.source_ids.map((id) => (
                      <li key={id} className="text-xs text-muted-foreground">
                        #{id} — {sourceTitle(id)}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>
          </TD>
        </TR>
      )}
    </>
  );
}
