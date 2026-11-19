"use client";

import { useState } from "react";
import { facebookApi, metaPublishingApi, youtubeApi, type AccountConnection, type ConnectionState } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { LANGUAGES } from "./publishing-portals";

const PLATFORM_LABEL = { youtube: "YouTube", instagram: "Instagram", facebook: "Facebook" } as const;
const STATE: Record<ConnectionState, { text: string; variant: "success" | "warning" | "danger" | "default" | "info" }> = {
  verified: { text: "Verified live", variant: "success" },
  known_not_authenticated: { text: "Known, not authenticated", variant: "warning" },
  not_created: { text: "Not created", variant: "default" },
  awaiting_creation: { text: "Awaiting channel creation", variant: "default" },
  auth_failed: { text: "Authentication failed", variant: "danger" },
  needs_owner_authorization: { text: "Needs owner authorization", variant: "info" },
};
const DISABLED = "disabled:opacity-100 disabled:bg-muted disabled:text-muted-foreground disabled:border disabled:border-dashed disabled:border-border disabled:cursor-not-allowed";

async function runTest(row: AccountConnection): Promise<string> {
  if (row.platform === "youtube") { const r = await youtubeApi.test(row.language); return `${r.channel_title} (${r.channel_id}) answered with a working token.`; }
  if (row.platform === "facebook") { const r = await facebookApi.testConnection(row.language); return `${r.page.name} (${r.page.id}) answered; linked to @${r.linked_instagram.username}.`; }
  const r = await metaPublishingApi.testConnection(row.language);
  return `@${r.account.username} (${r.account.id}) answered with a working token.`;
}

/** Every language and platform in one table: the account, whether a real authenticated check confirmed it, and what the owner must still do. A test only reads; nothing is published. */
export function AccountMatrix({ rows, onChanged, returnTo }: { rows: AccountConnection[]; onChanged: () => void; returnTo: string }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, { ok: boolean; text: string }>>({});
  const found = typeof window === "undefined" ? null : new URLSearchParams(window.location.search);
  const discovered = found?.get("youtube") === "discovered" ? { language: found.get("language") ?? "", id: found.get("channel_id") ?? "", title: found.get("channel_title") ?? "" } : null;
  const key = (r: AccountConnection) => `${r.platform}-${r.language}`;
  const act = async (r: AccountConnection, work: () => Promise<string>) => {
    setBusy(key(r));
    try { const text = await work(); setNotes((n) => ({ ...n, [key(r)]: { ok: true, text } })); } catch (e) { setNotes((n) => ({ ...n, [key(r)]: { ok: false, text: e instanceof Error ? e.message : String(e) } })); } finally { setBusy(null); onChanged(); }
  };
  const discover = (r: AccountConnection) => act(r, async () => { const { authorization_url } = await youtubeApi.connect(r.language, returnTo, true); window.location.assign(authorization_url); return "Opening Google…"; });
  return (
    <Card data-testid="account-matrix">
      <CardHeader><CardTitle>Accounts, all languages</CardTitle></CardHeader>
      <CardContent className="space-y-2 text-xs">
        <p className="text-muted-foreground">Verified means a real authenticated read-only request confirmed the account. Nothing here uploads or publishes.</p>
        {discovered && (
          <p role="status" data-testid="discovered-channel" className="rounded border border-dashed border-border p-2">
            Google returned channel <strong>{discovered.title}</strong> ({discovered.id}) for {discovered.language.toUpperCase()}. Nothing was stored. If this is the intended channel, register this id for the language.
          </p>
        )}
        <div className="overflow-x-auto">
          <table className="w-full min-w-[56rem] text-left">
            <thead><tr className="text-muted-foreground">{["Language", "Platform", "Brand", "Account ID", "Status", "Last verified", "Credentials", "Owner action", ""].map((h) => <th key={h} className="px-2 py-1 font-medium">{h}</th>)}</tr></thead>
            <tbody>
              {LANGUAGES.flatMap((l) => rows.filter((r) => r.language === l)).map((r) => {
                const note = notes[key(r)];
                return (
                  <tr key={key(r)} data-testid={`account-${r.platform}-${r.language}`} className="border-t border-border align-top">
                    <td className="px-2 py-1 font-medium">{r.language.toUpperCase()}</td>
                    <td className="px-2 py-1">{PLATFORM_LABEL[r.platform]}</td>
                    <td className="px-2 py-1">{r.brand}</td>
                    <td className="px-2 py-1 font-mono">{r.account_id ?? "—"}</td>
                    <td className="px-2 py-1"><Badge variant={STATE[r.state].variant} data-testid={`account-state-${r.platform}-${r.language}`}>{STATE[r.state].text}</Badge></td>
                    <td className="px-2 py-1">{r.last_verified ? new Date(r.last_verified).toLocaleString() : "never"}</td>
                    <td className="px-2 py-1">{r.credentials_valid === null ? "unknown" : r.credentials_valid ? "valid" : "invalid"}{r.missing_permissions.length > 0 && <span className="block text-amber-700 dark:text-amber-400">missing: {r.missing_permissions.join(", ")}</span>}</td>
                    <td className="px-2 py-1">
                      {r.last_error && <span className="block text-rose-700 dark:text-rose-400">{r.last_error}</span>}
                      {r.owner_action ?? "none"}
                      {note && <span role="status" className={`block ${note.ok ? "text-emerald-700 dark:text-emerald-400" : "text-rose-700 dark:text-rose-400"}`}>{note.text}</span>}
                    </td>
                    <td className="px-2 py-1 whitespace-nowrap">
                      {r.state === "needs_owner_authorization" && r.platform === "youtube"
                        ? <Button type="button" size="sm" variant="outline" className={DISABLED} disabled={!!busy} onClick={() => void discover(r)}>Discover channel</Button>
                        : <Button type="button" size="sm" variant="outline" className={DISABLED} disabled={!!busy || !r.testable} onClick={() => void act(r, () => runTest(r))}>{busy === key(r) ? "Testing…" : "Test connection"}</Button>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  );
}
