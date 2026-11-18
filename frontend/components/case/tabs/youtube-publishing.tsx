"use client";

import { useState } from "react";
import { youtubeApi, type PublicationView, type YouTubeChannel, type YouTubeSession, type YouTubeUploads } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const DISABLED = "disabled:opacity-100 disabled:bg-muted disabled:text-muted-foreground disabled:border disabled:border-dashed disabled:border-border disabled:cursor-not-allowed";
export const ACTIVE_UPLOAD = ["created", "uploading", "uploaded", "processing"];
const ACTION_TEXT: Record<string, string> = {
  reconnect: "Disconnect and connect YouTube again.", set_up_google: "Set up the Google OAuth client (steps above).", wait_for_quota: "YouTube's quota is used up: try again after it resets.",
  check_permissions: "YouTube refused the request: check the channel's permissions and that uploads are allowed.", restart_upload: "The upload session expired: upload again (it starts from the beginning).",
  retry: "The connection failed: resume the upload.", resume: "The application stopped during the upload: resume it.",
};
const SETUP_STEPS = [
  "In Google Cloud Console create (or pick) a project and enable the YouTube Data API v3.",
  "Configure the OAuth consent screen and add the owner's Google account as a test user.",
  "Create an OAuth client ID of type \"Web application\" and add the redirect URI shown below as an authorized redirect URI.",
  "Download the client JSON and save it as ~/.truecrime/youtube/client_secret.json (owner-only, outside the repository).",
  "Set \"live_enabled\" to true in config/youtube_publishing.json and restart the API.",
];

type Note = { tone: "ok" | "bad"; text: string };

function noteFromUrl(): Record<string, Note> {
  const params = typeof window === "undefined" ? new URLSearchParams() : new URLSearchParams(window.location.search);
  const language = params.get("language") ?? "";
  if (params.get("youtube") === "connected") return { [language]: { tone: "ok", text: "YouTube connected: the authorized channel is the registered one." } };
  if (params.get("youtube") === "error") return { [language]: { tone: "bad", text: params.get("detail") ?? `Connecting failed (${params.get("reason") ?? "unknown"}).` } };
  return {};
}

export function YouTubeChannelsPanel({ channels, onChanged, returnTo, language }: { channels: YouTubeChannel[] | null; onChanged: () => void; returnTo: string; language: string }) {
  const [notes, setNotes] = useState<Record<string, Note | undefined>>(noteFromUrl);
  const [busy, setBusy] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [ids, setIds] = useState<Record<string, string>>({});
  if (!channels) return null;
  const first = channels[0];
  const act = async (lang: string, work: () => Promise<string>) => {
    setBusy(lang); setNotes((n) => ({ ...n, [lang]: undefined }));
    try { const text = await work(); setNotes((n) => ({ ...n, [lang]: { tone: "ok", text } })); } catch (e) { setNotes((n) => ({ ...n, [lang]: { tone: "bad", text: e instanceof Error ? e.message : String(e) } })); } finally { setBusy(null); onChanged(); }
  };
  const connect = (c: YouTubeChannel) => act(c.language, async () => { const { authorization_url } = await youtubeApi.connect(c.language, returnTo); window.location.assign(authorization_url); return "Opening Google…"; });
  const test = (c: YouTubeChannel) => act(c.language, async () => { const r = await youtubeApi.test(c.language); return `Connection test passed at ${new Date(r.checked_at).toLocaleTimeString()}: the Google token works, the permissions (${r.scopes.join(", ")}) are granted, and the channel is ${r.channel_title} (${r.channel_id}), the registered ${c.label} channel.`; });
  const disconnect = (c: YouTubeChannel) => act(c.language, async () => { setConfirming(null); const r = await youtubeApi.disconnect(c.language); return `${c.label} channel disconnected: its stored tokens were removed. ${r.revoked ? "Google's access was revoked too." : `Google's access was kept, because ${r.kept_connected.join(", ")} still uses it.`}`; });
  const register = (c: YouTubeChannel) => act(c.language, async () => { await youtubeApi.register(c.language, ids[c.language] ?? ""); setIds((i) => ({ ...i, [c.language]: "" })); return `Channel id registered for ${c.label}. You can connect it now.`; });
  return (
    <Card data-testid="youtube-connection">
      <CardHeader><CardTitle>YouTube channels</CardTitle>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={first.live_enabled ? "info" : "default"} data-testid="youtube-live">{first.live_enabled ? "live calls on" : "live calls off"}</Badge>
          <Badge variant="info">uploads are private</Badge>
        </div>
      </CardHeader>
      <CardContent className="space-y-3 text-xs">
        <p className="text-muted-foreground">One Google OAuth client serves every channel; each language has its own credentials and only ever publishes to its own registered channel. Nothing is published or made public by this application in this phase.</p>
        {!first.client_configured && (
          <div className="space-y-1 rounded border border-dashed border-border p-2" data-testid="youtube-setup">
            <p className="font-medium">One-time setup by the owner (Google Cloud):</p>
            <ol className="list-decimal space-y-0.5 pl-5">{SETUP_STEPS.map((s) => <li key={s}>{s}</li>)}</ol>
            <p>Authorized redirect URI: <code>{first.redirect_uri}</code></p>
          </div>
        )}
        {channels.map((c) => {
          const state = !c.registered ? ["Channel id needed", "warning"] as const : !c.connected ? ["Not connected", "warning"] as const
            : c.channel_matches && c.scopes_ok ? [`Connected: ${c.channel_title} (${c.channel_id})`, "success"] as const : ["Connected to a different channel or with missing permissions", "danger"] as const;
          const note = notes[c.language];
          const working = busy === c.language;
          return (
            <div key={c.language} data-testid={`youtube-channel-${c.language}`} className={`space-y-2 rounded border p-2 ${c.language === language ? "border-primary/60" : "border-border"}`}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{c.name ? `${c.label} · ${c.name}` : c.label}</span>
                <Badge variant={state[1]} data-testid={`youtube-state-${c.language}`}>{state[0]}</Badge>
                {c.language === language && <Badge variant="default">this language</Badge>}
              </div>
              {c.registered
                ? <p className="text-muted-foreground">Registered channel: {c.expected_channel_id}. Connecting grants upload and read access to this channel only.</p>
                : (
                  <div className="flex flex-wrap items-center gap-2">
                    <input aria-label={`Channel ID ${c.label}`} data-testid={`youtube-channel-id-${c.language}`} placeholder="UC… (YouTube Studio > Settings > Channel > Advanced settings)" value={ids[c.language] ?? ""}
                      onChange={(e) => setIds((i) => ({ ...i, [c.language]: e.target.value }))} className="w-80 rounded border border-border bg-background px-2 py-1" />
                    <Button type="button" size="sm" className={DISABLED} disabled={working || !(ids[c.language] ?? "").trim()} onClick={() => void register(c)}>Register channel</Button>
                  </div>
                )}
              <div className="flex flex-wrap items-center gap-2">
                <Button type="button" size="sm" className={DISABLED} disabled={!!busy || !c.registered || !c.client_configured || !c.live_enabled} onClick={() => void connect(c)}>Connect {c.label} channel</Button>
                <Button type="button" size="sm" variant="outline" className={DISABLED} disabled={!!busy || !c.connected || !c.live_enabled} onClick={() => void test(c)}>{working ? "Working…" : "Test connection"}</Button>
                {confirming === c.language ? (
                  <><span>Removes the {c.label} channel&apos;s stored tokens. Google&apos;s access is revoked only if no other channel is connected.</span>
                    <Button type="button" size="sm" onClick={() => void disconnect(c)}>Yes, disconnect</Button><Button type="button" size="sm" variant="ghost" onClick={() => setConfirming(null)}>Cancel</Button></>
                ) : (
                  <Button type="button" size="sm" variant="outline" className={DISABLED} disabled={!!busy || !c.connected} onClick={() => setConfirming(c.language)}>Disconnect</Button>
                )}
              </div>
              {note && <p role="status" data-testid={`youtube-note-${c.language}`} className={`rounded border px-3 py-2 font-medium ${note.tone === "ok" ? "border-emerald-500/50 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" : "border-rose-500/50 bg-rose-500/10 text-rose-700 dark:text-rose-400"}`}>{note.tone === "ok" ? "✓ " : "✗ "}{note.text}</p>}
            </div>
          );
        })}
      </CardContent>
    </Card>
  );
}

export function YouTubeUpload({ pub, uploads, status, onChanged, refetchUploads, filmUrl, filmPrivate }:
  { pub: PublicationView; uploads: YouTubeUploads | null; status: YouTubeChannel | null; onChanged: () => void; refetchUploads: () => void; filmUrl: string | null; filmPrivate: boolean }) {
  const entry = uploads?.uploads[String(pub.id)];
  const session: YouTubeSession | null = entry?.session ?? null;
  const [typed, setTyped] = useState("");
  const [asking, setAsking] = useState(false);
  const [message, setMessage] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  if (!entry) return null;
  const approved = ["publication_approved", "scheduled"].includes(pub.status);
  const resumable = session !== null && ["interrupted", "expired"].includes(session.status);
  const running = session !== null && ACTIVE_UPLOAD.includes(session.status) && session.status !== "interrupted";
  const canStart = (approved || pub.status === "uploading") && !running && !!status?.connected && !!status.live_enabled && status.channel_matches;
  const why = !status?.live_enabled ? "Live calls are off." : !status?.connected ? "Connect this language's YouTube channel first." : !status.channel_matches ? "The connected channel is not the registered one."
    : !approved && pub.status !== "uploading" ? "Approve the content and the publication first." : null;
  const start = async () => {
    setBusy(true); setMessage(null);
    try { await youtubeApi.upload(pub.id, typed); setAsking(false); setTyped(""); setMessage({ tone: "ok", text: "Upload started: private. Progress is shown below." }); }
    catch (e) { setMessage({ tone: "bad", text: e instanceof Error ? e.message : String(e) }); }
    finally { setBusy(false); refetchUploads(); onChanged(); }
  };
  const check = async () => {
    setBusy(true); setMessage(null);
    try { await youtubeApi.checkProcessing(pub.id); setMessage({ tone: "ok", text: "Asked YouTube about the processing." }); }
    catch (e) { setMessage({ tone: "bad", text: e instanceof Error ? e.message : String(e) }); }
    finally { setBusy(false); refetchUploads(); onChanged(); }
  };
  const related = pub.kind === "short" ? pub.links.find((l) => l.method === "related_video") : undefined;
  return (
    <div className="space-y-2 border-t border-border pt-2 text-xs" data-testid="youtube-upload">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">YouTube upload</span>
        <Badge variant="info">private</Badge>
        {session && <Badge variant={session.status === "processed" ? "success" : session.status === "failed" || session.status === "expired" ? "danger" : session.status === "interrupted" ? "warning" : "info"} data-testid="youtube-session-status">{session.status}</Badge>}
        {entry.remote_url && <a href={entry.remote_url} target="_blank" rel="noreferrer" className="underline" data-testid="youtube-video-link">View uploaded video (private: visible to the channel owner only)</a>}
      </div>
      {session && session.status !== "processed" && (
        <div className="space-y-1" data-testid="youtube-progress">
          <progress className="h-2 w-64" max={100} value={session.percent} aria-label={`Upload progress ${pub.asset_key}`} />
          <p>{session.percent}% sent ({(session.bytes_sent / 1024 / 1024).toFixed(1)} of {(session.total_bytes / 1024 / 1024).toFixed(1)} MB)
            {session.processing_status ? ` · YouTube processing: ${session.processing_status}${session.processing_percent != null ? ` ${session.processing_percent}%` : ""}` : ""}</p>
        </div>
      )}
      {session?.status === "processed" && <p data-testid="youtube-processed">Uploaded and processed by YouTube (private). Thumbnail: {session.thumbnail}. Subtitles: {session.captions}.</p>}
      {session?.error && <p className="text-rose-700 dark:text-rose-400" data-testid="youtube-error">{session.error}{session.required_action ? ` Required: ${ACTION_TEXT[session.required_action] ?? session.required_action}` : ""}</p>}
      {asking ? (
        <div className="space-y-1 rounded border border-border p-2" data-testid="youtube-confirm">
          <p>This sends the video to YouTube as <b>private</b>. It does not publish it. To confirm, type exactly: <code>{entry.confirmation}</code></p>
          <input aria-label={`Confirmation ${pub.asset_key}`} data-testid="youtube-confirm-input" value={typed} onChange={(e) => setTyped(e.target.value)} className="w-full rounded border border-border bg-background px-2 py-1" />
          <div className="flex gap-2">
            <Button type="button" size="sm" className={DISABLED} disabled={busy || typed !== entry.confirmation} onClick={() => void start()}>Upload privately</Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => { setAsking(false); setTyped(""); }}>Cancel</Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          {session?.status !== "processed" && entry.remote_url === null && (
            <Button type="button" size="sm" className={DISABLED} disabled={busy || !canStart} onClick={() => setAsking(true)}>{resumable ? "Resume upload" : "Upload privately"}</Button>
          )}
          {session && ["uploaded", "processing"].includes(session.status) && <Button type="button" size="sm" variant="outline" className={DISABLED} disabled={busy} onClick={() => void check()}>Check processing</Button>}
          {!canStart && !running && session?.status !== "processed" && why && <span className="text-muted-foreground" data-testid="youtube-why">{why}</span>}
        </div>
      )}
      {message && <p role="status" className={message.tone === "ok" ? "text-emerald-700 dark:text-emerald-400" : "text-rose-700 dark:text-rose-400"}>{message.text}</p>}
      {related && (
        <div className="space-y-1 rounded border border-dashed border-border p-2" data-testid="youtube-related-manual">
          <p className="font-medium">Related video: set by hand in YouTube Studio (YouTube has no API for it)</p>
          <p className="text-muted-foreground">Open this Short in Studio, click the pencil next to <em>Related video</em> in the right column and choose the full film{filmUrl ? <> ({filmUrl})</> : ""}. The channel needs advanced features (Settings → Channel → Feature eligibility), and the film must be public or unlisted. Then record below how you checked it; it is never marked verified without that note.</p>
          {filmPrivate && <p className="text-amber-700 dark:text-amber-400" data-testid="youtube-related-film-private">The film is still private: Studio will not offer it until it is public or unlisted.</p>}
          {entry.studio_url && pub.remote_url && (
            <a href={entry.studio_url} target="_blank" rel="noreferrer" className="inline-block font-medium underline" data-testid="youtube-related-studio-link">Open this Short in YouTube Studio</a>
          )}
        </div>
      )}
    </div>
  );
}
