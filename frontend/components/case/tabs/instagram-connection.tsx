"use client";

import { useState } from "react";
import { metaPublishingApi, type InstagramConnection } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const DISABLED = "disabled:opacity-100 disabled:bg-muted disabled:text-muted-foreground disabled:border disabled:border-dashed disabled:border-border disabled:cursor-not-allowed";
const SETUP_STEPS = [
  "Open developers.facebook.com, My Apps, and create (or open) a Business app with the Facebook Login for Business (or Facebook Login) product.",
  "Add the redirect URI shown below under Valid OAuth Redirect URIs. While the app is in development mode, the Facebook account that manages the Instagram account must be an admin, developer or tester of the app.",
  "The Instagram account must be a Professional (Business or Creator) account linked to a Facebook Page, and that Facebook account must manage the Page.",
  "Copy the App ID and the App Secret (App settings, Basic) into ~/.truecrime/meta/app.json as {\"app_id\": \"...\", \"app_secret\": \"...\"} (owner-only, outside the repository).",
];

type Note = { tone: "ok" | "bad"; text: string };

function noteFromUrl(): Note | null {
  const params = typeof window === "undefined" ? new URLSearchParams() : new URLSearchParams(window.location.search);
  if (params.get("instagram") === "connected") return { tone: "ok", text: "Instagram connected: the token reaches the configured account and holds every required permission." };
  if (params.get("instagram") === "error") return { tone: "bad", text: params.get("detail") ?? `Connecting failed (${params.get("reason") ?? "unknown"}).` };
  return null;
}

function stateOf(c: InstagramConnection): readonly [string, "warning" | "success" | "danger"] {
  if (!c.app_configured && !c.connected) return ["Meta app not set up", "warning"];
  if (!c.connected) return ["Not connected", "warning"];
  if (c.expired) return ["Token expired", "danger"];
  if (c.last_test && !c.last_test.ok) return ["Connected, last test failed", "danger"];
  if (!c.verified) return ["Connected, not yet tested", "warning"];
  return [`Connected: @${c.expected_account.username}${c.expiring_soon ? ` (token ends in ${c.days_left} days)` : ""}`, c.expiring_soon ? "warning" : "success"];
}

export function InstagramConnectionPanel({ returnTo, language }: { returnTo: string; language: string }) {
  const connection = useApi(() => metaPublishingApi.connection(), [], { keepPrevious: true });
  const [note, setNote] = useState<Note | null>(noteFromUrl);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const c = connection.data;
  if (!c) return null;
  const act = async (work: () => Promise<string>) => {
    setBusy(true); setNote(null);
    try { setNote({ tone: "ok", text: await work() }); } catch (e) { setNote({ tone: "bad", text: e instanceof Error ? e.message : String(e) }); } finally { setBusy(false); connection.refetch(); }
  };
  const connect = () => act(async () => { const { authorization_url } = await metaPublishingApi.connect(returnTo); window.location.assign(authorization_url); return "Opening Facebook…"; });
  const test = () => act(async () => {
    const r = await metaPublishingApi.testConnection();
    return `Connection test passed at ${new Date(r.checked_at).toLocaleTimeString()}: the token works, the permissions (${r.permissions.join(", ")}) are granted, the account is @${r.account.username} (${r.account.id}), the configured one. Publishing quota used: ${r.quota_usage ?? "n/a"}.`;
  });
  const disconnect = () => act(async () => { setConfirming(false); const r = await metaPublishingApi.disconnect(); return r.still_set_by_environment ? "The stored token was removed, but another token is still set by the environment." : "Instagram disconnected: the stored token was removed. Facebook's grant for the app is untouched (remove the app in your Facebook settings to end it)."; });
  const [label, tone] = stateOf(c);
  return (
    <Card data-testid="instagram-connection">
      <CardHeader><CardTitle>Instagram account</CardTitle>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={tone} data-testid="instagram-state">{label}</Badge>
          <Badge variant={c.live_enabled ? "info" : "default"} data-testid="instagram-live">{c.live_enabled ? "connection calls on" : "connection calls off"}</Badge>
          <Badge variant="info">nothing is published from here</Badge>
          {c.language === language && <Badge variant="default">this language</Badge>}
        </div>
      </CardHeader>
      <CardContent className="space-y-2 text-xs">
        <p className="text-muted-foreground">Expected account: @{c.expected_account.username} ({c.expected_account.id}), {c.language}. Required permissions: {c.required_permissions.join(", ")}. Connecting only reads; no Reel is made or published.</p>
        {c.connected_at && <p className="text-muted-foreground">Connected {new Date(c.connected_at).toLocaleString()}{c.expires_at ? `, the token ends ${new Date(c.expires_at).toLocaleDateString()}` : ""}.{c.last_test && ` Last test: ${c.last_test.ok ? "passed" : `failed (${c.last_test.error})`} at ${new Date(c.last_test.at).toLocaleTimeString()}.`}</p>}
        {!c.app_configured && !c.connected && (
          <div className="space-y-1 rounded border border-dashed border-border p-2" data-testid="instagram-setup">
            <p className="font-medium">One-time setup by the owner (Meta):</p>
            <ol className="list-decimal space-y-0.5 pl-5">{SETUP_STEPS.map((s) => <li key={s}>{s}</li>)}</ol>
            <p>Valid OAuth Redirect URI: <code>{c.redirect_uri}</code></p>
          </div>
        )}
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" size="sm" className={DISABLED} disabled={busy || !c.app_configured || !c.live_enabled} onClick={() => void connect()}>Connect Instagram</Button>
          <Button type="button" size="sm" variant="outline" className={DISABLED} disabled={busy || !c.connected || !c.live_enabled} onClick={() => void test()}>{busy ? "Working…" : "Test connection"}</Button>
          {confirming ? (
            <><span>Removes the stored token. Facebook&apos;s grant for the app is left as it is.</span>
              <Button type="button" size="sm" onClick={() => void disconnect()}>Yes, disconnect</Button><Button type="button" size="sm" variant="ghost" onClick={() => setConfirming(false)}>Cancel</Button></>
          ) : (
            <Button type="button" size="sm" variant="outline" className={DISABLED} disabled={busy || !c.connected} onClick={() => setConfirming(true)}>Disconnect</Button>
          )}
        </div>
        {note && <p role="status" data-testid="instagram-note" className={`rounded border px-3 py-2 font-medium ${note.tone === "ok" ? "border-emerald-500/50 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" : "border-rose-500/50 bg-rose-500/10 text-rose-700 dark:text-rose-400"}`}>{note.tone === "ok" ? "✓ " : "✗ "}{note.text}</p>}
      </CardContent>
    </Card>
  );
}
