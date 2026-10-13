"use client";

import { useState } from "react";
import { api } from "@/lib/api";
import { langLabel } from "@/lib/format";
import type { ApprovalBundle } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export function ApprovalBundles({ bundles, onChanged }: { bundles: ApprovalBundle[]; onChanged: () => void }) {
  return (
    <div className="space-y-3 border-t border-border p-4">
      <div>
        <h3 className="text-sm font-medium">Approval bundles</h3>
        <p className="text-xs text-muted-foreground">Each language keeps one hash-bound film and Shorts bundle.</p>
      </div>
      {bundles.map((bundle) => <BundleCard key={bundle.id} bundle={bundle} onChanged={onChanged} />)}
    </div>
  );
}

function BundleCard({ bundle, onChanged }: { bundle: ApprovalBundle; onChanged: () => void }) {
  const [busy, setBusy] = useState<number | string | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function decide(itemId: number, itemType: string, decision: string) {
    setBusy(itemId); setError(null);
    try { await api.decideApprovalBundleItem(bundle.case_id, bundle.language, itemType, itemId, decision); onChanged(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); }
  }
  async function approve() {
    setBusy("bundle"); setError(null);
    try { await api.approveApprovalBundle(bundle.case_id, bundle.language); onChanged(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); }
  }
  async function upload() {
    setBusy("upload"); setError(null);
    try { await api.uploadApprovalBundle(bundle.case_id, bundle.language); onChanged(); }
    catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); }
  }
  return (
    <Card>
      <CardHeader className="flex-row items-center justify-between space-y-0">
        <CardTitle className="text-sm">{langLabel(bundle.language)} bundle</CardTitle>
        <Badge variant={bundle.status === "approved" ? "default" : "outline"}>{bundle.status}</Badge>
      </CardHeader>
      <CardContent className="space-y-2 text-xs">
        {bundle.items.map((item) => (
          <div key={`${item.item_type}-${item.item_id}`} className="flex flex-wrap items-center justify-between gap-2 rounded border border-border p-2">
            <div className="min-w-0"><span className="font-medium">{item.label}</span> <span className="text-muted-foreground">{item.item_type} · {item.content_hash.slice(0, 12)}</span></div>
            <div className="flex flex-wrap gap-1">
              <Badge variant="outline">{item.status}</Badge>
              {item.status !== "approved" && item.status !== "excluded" && <Button size="sm" onClick={() => decide(item.item_id, item.item_type, "approve")} loading={busy === item.item_id}>Approve</Button>}
              {item.status !== "excluded" && <Button size="sm" variant="outline" onClick={() => decide(item.item_id, item.item_type, "exclude")} loading={busy === item.item_id}>Exclude</Button>}
              {(item.status === "approved" || item.status === "excluded" || item.status === "needs_review") && <Button size="sm" variant="ghost" onClick={() => decide(item.item_id, item.item_type, "re-review")} loading={busy === item.item_id}>Re-review</Button>}
            </div>
          </div>
        ))}
        {error && <p className="text-rose-500">{error}</p>}
        <div className="flex flex-wrap items-center justify-between gap-2"><span className="text-muted-foreground">Visibility: {bundle.visibility} · Upload: {bundle.upload_status}</span><div className="flex gap-2"><Button size="sm" disabled={bundle.status !== "approved"} loading={busy === "bundle"} onClick={approve}>Approve bundle</Button><Button size="sm" disabled={bundle.status !== "approved"} loading={busy === "upload"} onClick={upload}>Approve and upload</Button></div></div>
      </CardContent>
    </Card>
  );
}
