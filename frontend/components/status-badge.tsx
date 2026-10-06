import { Badge } from "@/components/ui/badge";
import type { CaseStatus, RunStatus } from "@/lib/types";

const CASE_STATUS: Record<CaseStatus, { label: string; variant: "default" | "success" | "warning" | "danger" | "info" | "accent" | "outline" }> = {
  new: { label: "New", variant: "info" },
  researching: { label: "Researching", variant: "warning" },
  researched: { label: "Research Complete", variant: "accent" },
  writing: { label: "Writing", variant: "warning" },
  story_ready: { label: "Story Ready", variant: "success" },
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
