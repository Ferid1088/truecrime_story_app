"use client";

import type { StoryMeta } from "@/lib/types";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";

export function VersionList({
  versions,
  selectedId,
  onSelect,
}: {
  versions: StoryMeta[];
  selectedId: number | null;
  onSelect: (v: StoryMeta) => void;
}) {
  return (
    <div className="divide-y divide-border rounded-lg border border-border">
      {versions.map((v) => (
        <button
          key={v.id}
          onClick={() => onSelect(v)}
          className={cn(
            "flex w-full cursor-pointer items-center justify-between px-3 py-2.5 text-left transition-colors",
            selectedId === v.id ? "bg-accent/50" : "hover:bg-muted/60",
          )}
        >
          <div>
            <p className="flex items-center gap-1.5 text-sm font-medium">
              Version {v.version}
              <span className="rounded bg-sky-500/15 px-1 text-[10px] font-medium uppercase text-sky-600 dark:text-sky-400">
                {v.kind ?? "direct"}
              </span>
              {v.is_best && <span className="rounded bg-emerald-500/15 px-1 text-[10px] font-medium text-emerald-600 dark:text-emerald-400">best</span>}
              {v.status === "needs_revision" && (
                <span className="rounded bg-amber-500/15 px-1 text-[10px] font-medium text-amber-600 dark:text-amber-400">
                  needs revision
                </span>
              )}
              {v.status === "outdated" && (
                <span className="rounded bg-zinc-500/15 px-1 text-[10px] font-medium text-zinc-500">
                  outdated
                </span>
              )}
            </p>
            <p className="text-xs text-muted-foreground">{formatDateTime(v.created_at)}</p>
            {v.generation_model && (
              <p className="max-w-44 truncate font-mono text-[10px] text-muted-foreground/70">
                {v.generation_model}
              </p>
            )}
          </div>
          <div className="text-right text-xs text-muted-foreground">
            <p className="tabular-nums">
              Eng <span className="font-medium text-foreground">{Math.round(v.engagement_score)}</span>
            </p>
            <p className="tabular-nums">
              Sim{" "}
              <span className="font-medium text-foreground">
                {v.similarity_score == null ? "n/e" : `${(v.similarity_score * 100).toFixed(0)}%`}
              </span>
            </p>
          </div>
        </button>
      ))}
    </div>
  );
}
