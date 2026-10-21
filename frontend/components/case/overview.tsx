"use client";

import { useState } from "react";
import Link from "next/link";
import { Archive, ArchiveRestore, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { TabBar } from "@/components/ui/tabs";
import { EmptyState } from "@/components/state";
import { formatDate } from "@/lib/format";
import { cn } from "@/lib/utils";

export type OverviewItem = {
  id: number;
  title: string;
  /** ISO date shown under the name (creation or production date). */
  date: string | null;
  archived: boolean;
  /** "manual" can be restored by the owner; "all_published" ends only when a short is unpublished. */
  archiveReason?: string | null;
  /** Short line next to the date, e.g. "3 of 5 shorts published". */
  note?: string;
};

const TABS = ["Active", "Archive"] as const;
type Tab = (typeof TABS)[number];

/**
 * The overview of all cases: name, date, and a click for the details. Two tabs, Active and
 * Archive. `selectedId` marks the case that is selected right now.
 */
export function CaseOverview({
  items,
  idPrefix,
  dateLabel,
  href,
  onOpen,
  selectedId,
  onArchive,
  onRestore,
}: {
  items: OverviewItem[];
  idPrefix: string;
  dateLabel: string;
  href?: (id: number) => string;
  onOpen?: (id: number) => void;
  selectedId?: number | null;
  onArchive?: (id: number) => Promise<void>;
  onRestore?: (id: number) => Promise<void>;
}) {
  const [tab, setTab] = useState<Tab>("Active");
  const [busy, setBusy] = useState<number | null>(null);
  const shown = items.filter((item) => item.archived === (tab === "Archive"));
  const count = (archived: boolean) => items.filter((item) => item.archived === archived).length;

  async function run(id: number, action: (id: number) => Promise<void>) {
    setBusy(id);
    try {
      await action(id);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div data-testid={`${idPrefix}-overview`}>
      <TabBar
        tabs={TABS}
        value={tab}
        onChange={setTab}
        idPrefix={idPrefix}
        label="Active or archived cases"
      />
      <div id={`${idPrefix}-panel`} role="tabpanel" aria-label={`${tab} cases (${count(tab === "Archive")})`}>
        {shown.length === 0 ? (
          <EmptyState
            title={tab === "Active" ? "No active cases" : "Nothing archived yet"}
            description={
              tab === "Active"
                ? "Every case is archived."
                : "A case moves here when you archive it or when all its shorts are published."
            }
          />
        ) : (
          <ul className="divide-y divide-border rounded-lg border border-border bg-card">
            {shown.map((item) => {
              const selected = item.id === selectedId;
              const body = (
                <>
                  <span className="min-w-0 flex-1">
                    <span className="flex min-w-0 items-center gap-2">
                      <span className="truncate text-sm font-medium" title={item.title}>
                        {item.title}
                      </span>
                      {selected && (
                        <span className="shrink-0 rounded bg-accent px-1.5 py-0.5 text-[11px] text-accent-foreground">
                          Selected
                        </span>
                      )}
                    </span>
                    <span className="mt-0.5 block text-xs text-muted-foreground">
                      {dateLabel} {formatDate(item.date)}
                      {item.note ? ` · ${item.note}` : ""}
                    </span>
                  </span>
                  <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
                </>
              );
              const rowClass = cn(
                "flex min-w-0 flex-1 cursor-pointer items-center gap-3 px-4 py-3 text-left outline-none hover:bg-muted/40 focus-visible:ring-2 focus-visible:ring-ring/50",
                selected && "bg-muted/30",
              );
              return (
                <li key={item.id} className="flex items-center" data-testid={`${idPrefix}-row-${item.id}`}>
                  {href ? (
                    <Link href={href(item.id)} className={rowClass}>
                      {body}
                    </Link>
                  ) : (
                    <button type="button" onClick={() => onOpen?.(item.id)} className={rowClass}>
                      {body}
                    </button>
                  )}
                  {!item.archived && onArchive && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="mr-3"
                      loading={busy === item.id}
                      onClick={() => void run(item.id, onArchive)}
                    >
                      <Archive className="size-4" /> Archive
                    </Button>
                  )}
                  {item.archived && item.archiveReason === "manual" && onRestore && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="mr-3"
                      loading={busy === item.id}
                      onClick={() => void run(item.id, onRestore)}
                    >
                      <ArchiveRestore className="size-4" /> Restore
                    </Button>
                  )}
                  {item.archived && item.archiveReason === "all_published" && (
                    <span className="mr-4 shrink-0 text-xs text-muted-foreground">All shorts published</span>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
