import { CircleCheck, CircleDashed, CircleHelp, Hourglass } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { CaseStatus, ResolutionStatus, RunStatus } from "@/lib/types";
import { cn } from "@/lib/utils";

const CASE_STATUS: Record<CaseStatus, { label: string; variant: "default" | "success" | "warning" | "danger" | "info" | "accent" | "outline" }> = {
  new: { label: "New", variant: "info" },
  researching: { label: "Researching", variant: "warning" },
  researched: { label: "Research Complete", variant: "accent" },
  writing: { label: "Writing", variant: "warning" },
  story_ready: { label: "Story Ready", variant: "success" },
  producing: { label: "Producing", variant: "warning" },
  rendered: { label: "Film Rendered", variant: "accent" },
  published: { label: "Published", variant: "success" },
  completed: { label: "Completed", variant: "success" },
  rejected: { label: "Rejected", variant: "outline" },
  archived: { label: "Archived", variant: "outline" },
};

const RUN_STATUS: Record<RunStatus, { label: string; variant: "default" | "success" | "warning" | "danger" | "info" }> = {
  waiting: { label: "Waiting", variant: "default" },
  running: { label: "Running", variant: "warning" },
  completed: { label: "Done", variant: "success" },
  failed: { label: "Failed", variant: "danger" },
};

export function CaseStatusBadge({ status }: { status: string }) {
  const meta = CASE_STATUS[status as CaseStatus] ?? { label: status, variant: "default" as const };
  return <Badge variant={meta.variant}>{meta.label}</Badge>;
}

export function RunStatusBadge({ status }: { status: string }) {
  const meta = RUN_STATUS[status as RunStatus] ?? { label: status, variant: "default" as const };
  return <Badge variant={meta.variant}>{meta.label}</Badge>;
}

/** Resolution statuses in the order the UI lists them. */
export const RESOLUTION_STATUSES: readonly ResolutionStatus[] = [
  "SOLVED",
  "UNSOLVED",
  "STATUS_UNDER_REVIEW",
  "UNKNOWN",
];

const RESOLUTION: Record<
  ResolutionStatus,
  { label: string; short: string; variant: "success" | "danger" | "warning" | "outline"; hint: string; icon: typeof CircleCheck }
> = {
  SOLVED: {
    label: "SOLVED",
    short: "Solved",
    variant: "success",
    hint: "The perpetrator is legally or officially established.",
    icon: CircleCheck,
  },
  UNSOLVED: {
    label: "UNSOLVED",
    short: "Unsolved",
    variant: "danger",
    hint: "Nobody charged or convicted — the investigation is open or cold.",
    icon: CircleHelp,
  },
  STATUS_UNDER_REVIEW: {
    label: "UNDER REVIEW",
    short: "Under review",
    variant: "warning",
    hint: "A development without resolution (arrest, charges, no verdict yet) or conflicting reports.",
    icon: Hourglass,
  },
  UNKNOWN: {
    label: "UNKNOWN",
    short: "Unknown",
    variant: "outline",
    hint: "The case's resolution has not been verified yet.",
    icon: CircleDashed,
  },
};

/** Normalizes anything the API (or an older backend) sends to a known status. */
export function toResolution(status: string | null | undefined): ResolutionStatus {
  const s = (status ?? "").toUpperCase();
  return s in RESOLUTION ? (s as ResolutionStatus) : "UNKNOWN";
}

/** "Solved", "Unsolved", "Under review", "Unknown". */
export function resolutionLabel(status: string | null | undefined): string {
  return RESOLUTION[toResolution(status)].short;
}

/**
 * Whether the real-world case is solved — shown next to every case title.
 * `size="lg"` for headers and cards where the status is the headline.
 */
export function ResolutionBadge({
  status,
  confidence,
  size = "sm",
  className,
}: {
  status: string | null | undefined;
  /** 0–1; shown as a percentage when given. */
  confidence?: number | null;
  size?: "sm" | "lg";
  className?: string;
}) {
  const key = toResolution(status);
  const meta = RESOLUTION[key];
  const Icon = meta.icon;
  return (
    <Badge
      variant={meta.variant}
      title={`Case status: ${meta.short}. ${meta.hint}`}
      className={cn(
        "font-semibold tracking-wide",
        size === "lg" && "gap-1.5 px-2.5 py-1 text-xs",
        className,
      )}
    >
      <Icon className={size === "lg" ? "size-3.5" : "size-3"} aria-hidden />
      {meta.label}
      {confidence != null && (
        <span className="font-normal tabular-nums opacity-80">{Math.round(confidence * 100)}%</span>
      )}
    </Badge>
  );
}
