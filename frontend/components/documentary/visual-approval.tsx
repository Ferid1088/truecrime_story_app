"use client";

import { useState } from "react";
import { mvpApi } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";

const LABEL = { none: "No picture plan yet", awaiting_owner: "Not approved", changed: "Changed since approval", approved: "Approved" } as const;

/** The owner's one approval of the case's shared pictures and videos. No language's film is directed or rendered before it; the films go on by themselves afterwards. */
export function VisualApprovalBar({ caseId }: { caseId: number }) {
  const { data, refetch } = useApi(() => mvpApi.visualApproval(caseId), [caseId], { keepPrevious: true });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!data) return null;
  const act = async (work: () => Promise<unknown>) => {
    setBusy(true); setError(null);
    try { await work(); } catch (e) { setError(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); refetch(); }
  };
  return (
    <div className="mb-3 flex flex-wrap items-center gap-3 rounded border border-border p-2 text-sm" data-testid="visual-approval">
      <span className="font-medium">Case pictures and videos</span>
      <Badge variant={data.open ? "success" : data.state === "none" ? "default" : "warning"} data-testid="visual-approval-state">{LABEL[data.state]}</Badge>
      <span className="text-muted-foreground">
        {data.open ? `Approved ${data.approved_at ? new Date(data.approved_at).toLocaleString() : ""}. Every language may be directed and rendered; a change to the pictures asks for a new approval.`
          : `${data.reason ?? ""} No film is directed or rendered before you approve.`}
      </span>
      {data.open
        ? <Button type="button" size="sm" variant="outline" disabled={busy} onClick={() => void act(() => mvpApi.withdrawVisualApproval(caseId))}>Withdraw approval</Button>
        : <Button type="button" size="sm" disabled={busy || data.state === "none"} onClick={() => void act(() => mvpApi.approveVisualPackage(caseId))}>Approve pictures and videos</Button>}
      {error && <span role="alert" className="text-rose-700 dark:text-rose-400">{error}</span>}
    </div>
  );
}
