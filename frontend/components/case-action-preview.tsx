"use client";

import { useState } from "react";
import { Check } from "lucide-react";
import { api } from "@/lib/api";
import type { CaseControlAction, CaseControlApplyResult, CaseControlPreview } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";

const ACTION_LABEL: Record<CaseControlAction, string> = {
  naming_approve: "Approve title",
  thumbnail_approve: "Approve",
  thumbnail_reject: "Reject",
};

/** One reusable preview/apply boundary for case-level actions outside a job. */
export function CaseActionPreview({
  caseId,
  action,
  targetId,
  onApplied,
}: {
  caseId: number;
  action: CaseControlAction;
  targetId: number;
  onApplied?: (result: CaseControlApplyResult) => void;
}) {
  const [preview, setPreview] = useState<CaseControlPreview | null>(null);
  const [result, setResult] = useState<CaseControlApplyResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const openPreview = async () => {
    setBusy(true);
    setError(null);
    try { setPreview(await api.caseControlPreview(caseId, action, targetId)); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };

  const apply = async () => {
    if (!preview) return;
    setBusy(true);
    setError(null);
    try {
      const applied = await api.caseControlApply(caseId, preview.preview_id);
      setResult(applied);
      setPreview(null);
      onApplied?.(applied);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  };

  return (
    <>
      <Button size="sm" variant={action === "thumbnail_reject" ? "outline" : "default"} onClick={() => void openPreview()} loading={busy && !preview}>
        {ACTION_LABEL[action]}
      </Button>
      {error && <p role="alert" className="mt-1 text-xs text-rose-600 dark:text-rose-400">{error}</p>}
      {result && <p role="status" className="mt-1 text-xs text-emerald-700">Applied; changed outside scope: {result.changed_outside_scope.length ? result.changed_outside_scope.join(", ") : "none"}</p>}
      <Dialog
        open={preview != null}
        onClose={() => { if (!busy) setPreview(null); }}
        title="Preview before applying"
        description={`${ACTION_LABEL[action]} · preview ${preview?.preview_id ?? ""}`}
      >
        {preview && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-2 text-xs">
              <div className="rounded border border-border px-2 py-2"><b>{preview.scope.length}</b><span className="ml-1 text-muted-foreground">scoped item(s)</span></div>
              <div className="rounded border border-border px-2 py-2"><b>{preview.estimated_provider_calls.total}</b><span className="ml-1 text-muted-foreground">provider calls</span></div>
            </div>
            <ul className="divide-y divide-border rounded border border-border text-xs">
              {preview.scope.map((item) => <li key={item.id} className="flex items-center justify-between gap-2 px-3 py-2"><span>{item.type}</span><span className="flex items-center gap-2"><Badge variant="outline">{item.language}</Badge>{item.may_change && <Badge variant="warning">may change</Badge>}</span></li>)}
            </ul>
            <div className="flex justify-end gap-2"><Button variant="ghost" onClick={() => setPreview(null)} disabled={busy}>Cancel</Button><Button onClick={() => void apply()} loading={busy}><Check /> Apply</Button></div>
          </div>
        )}
      </Dialog>
    </>
  );
}
