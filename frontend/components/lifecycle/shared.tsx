"use client";

import { useId, useState } from "react";
import { ArrowRight, ChevronDown, ChevronRight, ExternalLink } from "lucide-react";
import type { Film, FilmState, ProductionType, StatusSource } from "@/lib/types";
import { humanize } from "@/lib/format";
import { Badge } from "@/components/ui/badge";
import { ResolutionBadge } from "@/components/status-badge";
import { cn } from "@/lib/utils";

/** Small uppercase caption above a value (same style as the discovery cards). */
export function FieldLabel({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <p className={cn("mb-0.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground", className)}>
      {children}
    </p>
  );
}

/** previous → new resolution status. */
export function ResolutionChange({
  from,
  to,
  size = "sm",
}: {
  from: string | null | undefined;
  to: string | null | undefined;
  size?: "sm" | "lg";
}) {
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5">
      {from ? (
        <ResolutionBadge status={from} size={size} />
      ) : (
        <span className="text-xs text-muted-foreground">first status</span>
      )}
      <ArrowRight className="size-3.5 shrink-0 text-muted-foreground" aria-label="changed to" />
      <ResolutionBadge status={to} size={size} />
    </span>
  );
}

export function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

/** Source links (title, else the site). Accepts `{url, title}` objects or plain URLs. */
export function SourceLinks({
  sources,
  max = 8,
  className,
}: {
  sources: (StatusSource | string)[] | null | undefined;
  max?: number;
  className?: string;
}) {
  const items = (sources ?? [])
    .map((s) => (typeof s === "string" ? { url: s, title: null } : s))
    .filter((s): s is StatusSource => !!s && typeof s.url === "string" && s.url.length > 0);
  if (!items.length) return null;
  const shown = items.slice(0, max);
  return (
    <ul className={cn("space-y-0.5 text-xs", className)}>
      {shown.map((s, i) => (
        <li key={`${s.url}-${i}`} className="flex min-w-0 items-start gap-1">
          <ExternalLink className="mt-0.5 size-3 shrink-0 text-muted-foreground" aria-hidden />
          <a
            href={s.url}
            target="_blank"
            rel="noopener noreferrer"
            className="min-w-0 break-words text-primary hover:underline"
            title={s.url}
          >
            {s.title?.trim() || hostOf(s.url)}
          </a>
          {s.title?.trim() && <span className="shrink-0 text-muted-foreground">· {hostOf(s.url)}</span>}
        </li>
      ))}
      {items.length > shown.length && (
        <li className="text-muted-foreground">+{items.length - shown.length} more</li>
      )}
    </ul>
  );
}

/** A section that opens and closes (button with aria-expanded). */
export function Collapsible({
  title,
  count,
  defaultOpen = false,
  children,
  className,
}: {
  title: React.ReactNode;
  count?: number;
  defaultOpen?: boolean;
  children: React.ReactNode;
  className?: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  return (
    <div className={className}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-controls={id}
        className="flex cursor-pointer items-center gap-1.5 rounded text-xs font-medium text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50"
      >
        {open ? <ChevronDown className="size-3.5" aria-hidden /> : <ChevronRight className="size-3.5" aria-hidden />}
        {title}
        {count != null && <Badge variant="outline">{count}</Badge>}
      </button>
      {open && (
        <div id={id} className="mt-2">
          {children}
        </div>
      )}
    </div>
  );
}

const FILM_STATE: Record<FilmState, { label: string; variant: "info" | "success" | "outline" }> = {
  rendered: { label: "Rendered", variant: "info" },
  published: { label: "Published", variant: "success" },
  archived: { label: "Archived", variant: "outline" },
};

export function FilmStateBadge({ state }: { state: string }) {
  const meta = FILM_STATE[state as FilmState] ?? { label: humanize(state), variant: "outline" as const };
  return <Badge variant={meta.variant}>{meta.label}</Badge>;
}

/** "Follow-up" for an update video; nothing (or "Original" when asked) otherwise. */
export function ProductionTypeBadge({
  type,
  showOriginal = false,
}: {
  type: ProductionType | string | null | undefined;
  showOriginal?: boolean;
}) {
  if (type === "follow_up")
    return (
      <Badge variant="accent" title="An update video about a case covered before while it was unsolved">
        Follow-up
      </Badge>
    );
  return showOriginal ? <Badge variant="outline">Original</Badge> : null;
}

/** A film's title as the channel shows it. */
export function filmTitle(film: Pick<Film, "id" | "youtube_title" | "title">): string {
  return film.youtube_title?.trim() || film.title?.trim() || `Film #${film.id}`;
}

/** "critical_moment" → "critical moment"; null → "—". */
export function openingLabel(strategy: string | null | undefined): string {
  return strategy ? humanize(strategy) : "—";
}

/** 0.82 → "82%"; null → "—". */
export function pct(value: number | null | undefined): string {
  return value == null ? "—" : `${Math.round(value * 100)}%`;
}

/** "a, b ,, c" → ["a", "b", "c"]. */
export function splitList(text: string): string[] {
  return text
    .split(/[,;\n]/)
    .map((s) => s.trim())
    .filter(Boolean);
}
