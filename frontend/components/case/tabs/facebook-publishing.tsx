"use client";

import { useState } from "react";
import { apiFileUrl, coverShapesApi, facebookApi, type FacebookConnection, type FacebookMode, type FacebookThumbnails, type FacebookUploads, type PublicationView } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const DISABLED = "disabled:opacity-100 disabled:bg-muted disabled:text-muted-foreground disabled:border disabled:border-dashed disabled:border-border disabled:cursor-not-allowed";
export const FACEBOOK_ACTIVE = ["created", "uploading", "processing"];
const ACTION_TEXT: Record<string, string> = {
  replace_token: "The Meta token was refused: generate a new system-user token and store it (the same token serves Instagram).", check_permissions: "Meta refused the request: check the system user's permissions and its access to the Page.",
  wait_and_retry: "Meta's rate limit was reached: try again later.", retry: "The connection failed: resume the upload.", resume: "The application stopped or was paused during the upload: resume it.",
  restart_upload: "Meta could not process this upload: upload again (it starts from the beginning).", check_facebook: "Meta gave no answer to the finish step: look at the Page in Meta Business Suite before anything else.",
  publish_in_business_suite: "Open Meta Business Suite, find this draft, check it, and publish it there by hand. This application never publishes a draft.",
  delete_on_facebook: "A video from this upload still exists on Facebook: delete it in Meta Business Suite if you do not want it.",
};
const MODE_TEXT: Record<FacebookMode, { button: string; what: string }> = {
  UPLOAD_ONLY: { button: "Upload only", what: "sends the file to Facebook and waits until Meta has processed it. Nothing is created on the Page." },
  SAVE_DRAFT: { button: "Save as draft", what: "uploads the file and saves it on the Page as a DRAFT. It is not public. What a draft looks like and where to find it is confirmed by the first test." },
  PUBLISH: { button: "Publish now", what: "uploads the file and makes it PUBLIC on the Page." },
};

type Note = { tone: "ok" | "bad"; text: string };
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));
const gigabytes = (n: number) => `${(n / 1024 ** 3).toFixed(1)} GB`;

function stateOf(c: FacebookConnection): readonly [string, "warning" | "success" | "danger"] {
  if (!c.connected) return ["No Meta token", "warning"];
  if (c.last_test && !c.last_test.ok) return ["Connected, last test failed", "danger"];
  if (!c.verified) return ["Token found, not yet tested", "warning"];
  return [`Connected: ${c.page.name}`, "success"];
}

export function FacebookConnectionPanel({ language, onChanged }: { language: string; onChanged: () => void }) {
  const connection = useApi(() => facebookApi.connection(), [], { keepPrevious: true });
  const [note, setNote] = useState<Note | null>(null);
  const [busy, setBusy] = useState(false);
  const c = connection.data;
  if (!c) return null;
  const test = async () => {
    setBusy(true); setNote(null);
    try {
      const r = await facebookApi.testConnection();
      setNote({ tone: "ok", text: `Connection test passed at ${new Date(r.checked_at).toLocaleTimeString()}: Page ${r.page.name} (${r.page.id}) is the configured one and is linked to Instagram @${r.linked_instagram.username} (${r.linked_instagram.id}). Permissions: ${r.permissions.join(", ")}. Tasks: ${r.tasks.join(", ")}. The Page access token was obtained and identifies as the Page. Meta's upload limit: ${gigabytes(r.upload_limits.size)} and ${Math.round(r.upload_limits.length / 60)} minutes.` });
    } catch (e) { setNote({ tone: "bad", text: message(e) }); } finally { setBusy(false); connection.refetch(); onChanged(); }
  };
  const [label, tone] = stateOf(c);
  return (
    <Card data-testid="facebook-connection">
      <CardHeader><CardTitle>Facebook Page</CardTitle>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={tone} data-testid="facebook-state">{label}</Badge>
          <Badge variant={c.live_enabled ? "warning" : "default"} data-testid="facebook-live">{c.live_enabled ? "uploads on" : "uploads off"}</Badge>
          <Badge variant={c.allow_public_publish ? "danger" : "default"} data-testid="facebook-public">{c.allow_public_publish ? "public publishing on" : "public publishing off"}</Badge>
          {c.language === language && <Badge variant="default">this language</Badge>}
        </div>
      </CardHeader>
      <CardContent className="space-y-2 text-xs">
        <p className="text-muted-foreground">Page: {c.brand} ({c.page.id}), {c.language}. Linked Instagram account: {c.linked_instagram_id}. The Page uses the same Meta system-user token as Instagram ({c.token_source ?? "none found"}), so there is no separate Connect or Disconnect here.</p>
        <p className="text-muted-foreground">Required permissions: {c.required_permissions.join(", ")}. {c.granted_permissions.length > 0 && <>Granted at the last test: {c.granted_permissions.join(", ")}. </>}{c.upload_limits && <>Meta&apos;s upload limit for this token: {gigabytes(c.upload_limits.size)}, {Math.round(c.upload_limits.length / 60)} minutes.</>}</p>
        {c.last_test && <p className="text-muted-foreground">Last test: {c.last_test.ok ? "passed" : `failed (${c.last_test.error})`} at {new Date(c.last_test.at).toLocaleString()}.</p>}
        <div className="space-y-1" data-testid="facebook-readiness">
          <p className="font-medium">Upload readiness: {c.upload_ready ? <span className="text-emerald-700 dark:text-emerald-400">the connection is ready</span> : "not ready"}</p>
          {c.upload_blockers.length > 0 && <ul className="list-disc pl-5 text-muted-foreground">{c.upload_blockers.map((b) => <li key={b}>{b}</li>)}</ul>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" size="sm" variant="outline" className={DISABLED} disabled={busy || !c.connected || !c.connection_live_enabled} onClick={() => void test()}>{busy ? "Working…" : "Test connection"}</Button>
          <span className="text-muted-foreground">Reads only: nothing is uploaded or published.</span>
        </div>
        {note && <p role="status" data-testid="facebook-note" className={`rounded border px-3 py-2 font-medium ${note.tone === "ok" ? "border-emerald-500/50 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" : "border-rose-500/50 bg-rose-500/10 text-rose-700 dark:text-rose-400"}`}>{note.tone === "ok" ? "✓ " : "✗ "}{note.text}</p>}
      </CardContent>
    </Card>
  );
}

/** The cover of a Reel: the one approved here and the thumbnail Facebook holds and uses (a read-only look at Facebook; nothing is sent). This application does not assign the cover yet, so the manual step is shown. */
function FacebookCover({ pub, coverUrl, videoId, caseId, language }: { pub: PublicationView; coverUrl: string | null; videoId: string; caseId: number; language: string }) {
  const [found, setFound] = useState<FacebookThumbnails | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState(false);
  const [set, setSet] = useState<number | null>(null);
  const { data: shapes } = useApi(() => coverShapesApi.overview(caseId, language, pub.asset_key), [caseId, language, pub.asset_key], { keepPrevious: true });
  const { data: connection } = useApi(() => facebookApi.connection(), [], { keepPrevious: true });
  const reel = shapes?.shapes.reel?.newest ?? null;
  const sendCover = async () => {
    if (!reel) return;
    setBusy(true); setProblem(null); setAsking(false);
    try { const done = await facebookApi.setCover(pub.id, reel.id); setFound(done); setSet(reel.version); } catch (e) { setProblem(message(e)); } finally { setBusy(false); }
  };
  const look = async () => {
    setBusy(true); setProblem(null);
    try { setFound(await facebookApi.thumbnails(pub.id)); } catch (e) { setProblem(message(e)); } finally { setBusy(false); }
  };
  const used = found?.thumbnails.find((t) => t.is_preferred) ?? null;
  return (
    <div className="space-y-2 rounded border border-border p-2" data-testid="facebook-cover">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">Cover</span>
        <Badge variant={reel || coverUrl ? "success" : "warning"} data-testid="facebook-cover-local">{reel ? `new cover v${reel.version} (${reel.status})` : coverUrl ? "approved here" : "no cover"}</Badge>
        <Badge variant={set ? "success" : "warning"} data-testid="facebook-cover-state">{set ? `reel cover v${set} sent to Facebook` : "no cover sent to Facebook yet"}</Badge>
      </div>
      <div className="flex flex-wrap items-start gap-4">
        <figure className="space-y-1">
          {/* eslint-disable-next-line @next/next/no-img-element -- served by our own API */}
          {reel ? <img src={apiFileUrl(reel.image_url)} alt={`New cover ${pub.asset_key}`} data-testid="facebook-cover-approved" className="aspect-[9/16] w-24 rounded border border-border object-cover" /> : coverUrl ? <img src={apiFileUrl(coverUrl)} alt={`Cover ${pub.asset_key}`} data-testid="facebook-cover-approved" className="aspect-[9/16] w-24 rounded border border-border object-cover" /> : <div className="aspect-[9/16] w-24 rounded border border-dashed border-border" />}
          <figcaption className="max-w-32 text-muted-foreground">{reel ? "New cover (not on Facebook yet)" : "Cover here"}</figcaption>
        </figure>
        <figure className="space-y-1">
          {used?.uri
            // eslint-disable-next-line @next/next/no-img-element -- a picture on Facebook's own server
            ? <img src={used.uri} alt={`Thumbnail Facebook uses for ${pub.asset_key}`} data-testid="facebook-cover-meta" className="aspect-[9/16] w-24 rounded border border-border object-cover" />
            : <div className="flex aspect-[9/16] w-24 items-center justify-center rounded border border-dashed border-border p-1 text-center text-muted-foreground">not read yet</div>}
          <figcaption className="max-w-32 text-muted-foreground">{used ? `Facebook uses #${used.id} (${used.width}×${used.height})` : "Thumbnail Facebook uses"}</figcaption>
        </figure>
        <div className="max-w-md space-y-1">
          <Button type="button" size="sm" variant="outline" className={DISABLED} disabled={busy} data-testid="facebook-cover-check" onClick={() => void look()}>{busy ? "Asking Facebook…" : "Check the thumbnail Facebook uses"}</Button>
          <p className="text-muted-foreground">Reads only. Video {videoId}.{found && ` Facebook holds ${found.thumbnails.length} thumbnails for it (checked ${new Date(found.checked_at).toLocaleTimeString()}).`}</p>
          {problem && <p className="text-rose-700 dark:text-rose-400" data-testid="facebook-cover-error">{problem}</p>}
          {reel && (
            <div className="space-y-1 rounded border border-border p-2" data-testid="facebook-cover-set">
              <p className="font-medium">New reel cover v{reel.version} ({reel.status})</p>
              {asking ? (
                <span className="flex flex-wrap items-center gap-2">A cover sent to Facebook cannot be changed or deleted through the API.
                  <Button type="button" size="sm" data-testid="facebook-cover-confirm" onClick={() => void sendCover()}>Yes, set it</Button><Button type="button" size="sm" variant="ghost" onClick={() => setAsking(false)}>Cancel</Button></span>
              ) : (
                <Button type="button" size="sm" variant="outline" className={DISABLED} disabled={busy || !connection?.cover_ready || reel.problems.length > 0} data-testid="facebook-cover-send" onClick={() => setAsking(true)}>Set this cover on the Facebook video</Button>
              )}
              {connection && !connection.cover_ready && <p className="text-amber-700 dark:text-amber-400" data-testid="facebook-cover-permissions">The token lacks {connection.cover_missing_permissions.join(", ")}. Generate the system-user token again with these permissions in Meta Business settings, store it, then press Test connection.</p>}
              {reel.problems.length > 0 && <p className="text-rose-700 dark:text-rose-400">This cover has problems: {reel.problems.join("; ")}</p>}
            </div>
          )}
          <p className="text-muted-foreground" data-testid="facebook-cover-manual">Facebook picks a frame itself unless a cover is set, and its feed cuts the 9:16 picture to about 4:5. Use the button above (it needs pages_read_user_content and pages_manage_engagement on the token), or in Meta Business Suite open the draft, choose Cover and upload the picture by hand. Reading the picture cannot tell a chosen cover from a chosen frame, so compare the two pictures here.</p>
        </div>
      </div>
    </div>
  );
}

export function FacebookUpload({ pub, uploads, onChanged, refetchUploads, coverUrl, caseId, language }: { caseId: number; language: string; pub: PublicationView; uploads: FacebookUploads | null; onChanged: () => void; refetchUploads: () => void; coverUrl: string | null }) {
  const entry = uploads?.uploads[String(pub.id)];
  const session = entry?.session ?? null;
  const [mode, setMode] = useState<FacebookMode | null>(null);
  const [typed, setTyped] = useState("");
  const [message_, setMessage] = useState<Note | null>(null);
  const [busy, setBusy] = useState(false);
  if (!entry || !uploads) return null;
  const done = session !== null && ["drafted", "published", "uncertain"].includes(session.status);
  const running = session !== null && FACEBOOK_ACTIVE.includes(session.status);
  const ready = entry.readiness.length === 0 && uploads.live_enabled && !running && !done;
  const modes: FacebookMode[] = ["UPLOAD_ONLY", "SAVE_DRAFT"];
  const canPublish = ready && uploads.allow_public_publish;
  const act = async (work: () => Promise<string>) => {
    setBusy(true); setMessage(null);
    try { setMessage({ tone: "ok", text: await work() }); } catch (e) { setMessage({ tone: "bad", text: message(e) }); } finally { setBusy(false); refetchUploads(); onChanged(); }
  };
  const start = () => act(async () => { await facebookApi.upload(pub.id, mode as FacebookMode, typed); setMode(null); setTyped(""); return "Started. Progress is shown below; nothing is published unless you chose Publish."; });
  return (
    <div className="space-y-2 border-t border-border pt-2 text-xs" data-testid="facebook-upload">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">Facebook {entry.kind === "film" ? "film" : "Reel"} upload</span>
        <Badge variant={uploads.automatic_publishing ? "warning" : "info"} data-testid="facebook-automatic">{uploads.automatic_publishing ? "published automatically at its planned time" : "automatic publishing off"}</Badge>
        {session && <Badge variant={session.status === "published" || session.status === "drafted" ? "success" : ["failed", "uncertain"].includes(session.status) ? "danger" : ["interrupted", "cancelled"].includes(session.status) ? "warning" : "info"} data-testid="facebook-session-status">{session.status}{session.status === "drafted" ? " (draft on the Page)" : ""}</Badge>}
        {entry.remote_url && <a href={entry.remote_url} target="_blank" rel="noreferrer" className="underline" data-testid="facebook-video-link">View on Facebook</a>}
        {session?.video_id && !entry.remote_url && <span className="text-muted-foreground" data-testid="facebook-video-id">Facebook video id {session.video_id}</span>}
      </div>
      {session && !done && (
        <div className="space-y-1" data-testid="facebook-progress">
          <progress className="h-2 w-64" max={100} value={session.percent} aria-label={`Upload progress ${pub.asset_key}`} />
          <p>{session.percent}% sent ({(session.bytes_sent / 1024 / 1024).toFixed(1)} of {(session.total_bytes / 1024 / 1024).toFixed(1)} MB){session.video_status ? ` · Meta: ${session.video_status}` : ""}{session.status === "ready" ? ` · uploaded${entry.kind === "film" ? ", file handle kept" : " and processed"}; nothing is on the Page yet` : ""}</p>
        </div>
      )}
      {entry.kind === "short" && session?.video_id && <FacebookCover pub={pub} coverUrl={coverUrl} videoId={session.video_id} caseId={caseId} language={language} />}
      {entry.text_stale && (
        <p className="rounded border border-amber-500/50 bg-amber-500/10 px-2 py-1 text-amber-700 dark:text-amber-400" data-testid="facebook-text-stale">
          The text was corrected after this video was finished on Facebook, so the draft there still has the old text. Change it in Meta Business Suite (open the draft and edit it), or press Cancel upload, delete the old draft there and upload again.
        </p>
      )}
      {session?.error && <p className="text-rose-700 dark:text-rose-400" data-testid="facebook-error">{session.error}</p>}
      {session?.required_action && <p className="rounded border border-dashed border-border p-2" data-testid="facebook-manual">Manual action: {ACTION_TEXT[session.required_action] ?? session.required_action}</p>}
      {!done && !running && entry.readiness.length > 0 && (
        <div data-testid="facebook-blockers"><p className="font-medium">Not ready to upload:</p><ul className="list-disc pl-5 text-muted-foreground">{entry.readiness.map((b) => <li key={b}>{b}</li>)}</ul></div>
      )}
      {mode ? (
        <div className="space-y-1 rounded border border-border p-2" data-testid="facebook-confirm">
          <p>This {MODE_TEXT[mode].what} To confirm, type exactly: <code>{entry.confirmations[mode]}</code></p>
          <input aria-label={`Confirmation ${pub.asset_key}`} data-testid="facebook-confirm-input" value={typed} onChange={(e) => setTyped(e.target.value)} className="w-full rounded border border-border bg-background px-2 py-1" />
          <div className="flex gap-2">
            <Button type="button" size="sm" className={DISABLED} disabled={busy || typed !== entry.confirmations[mode]} onClick={() => void start()}>{MODE_TEXT[mode].button}</Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => { setMode(null); setTyped(""); }}>Cancel</Button>
          </div>
        </div>
      ) : (
        <div className="flex flex-wrap items-center gap-2">
          {!done && modes.map((m) => <Button key={m} type="button" size="sm" variant={m === "PUBLISH" ? "outline" : "default"} className={DISABLED} disabled={busy || !ready} data-testid={`facebook-mode-${m}`} onClick={() => setMode(m)}>{session?.status === "interrupted" ? "Resume: " : ""}{MODE_TEXT[m].button}</Button>)}
          {session?.status === "processing" && <Button type="button" size="sm" variant="outline" className={DISABLED} disabled={busy} onClick={() => void act(async () => { await facebookApi.checkProcessing(pub.id); return "Asked Meta about the processing."; })}>Check processing</Button>}
          {!done && !running && session?.status !== "drafted" && (
            <Button type="button" size="sm" className={DISABLED} disabled={busy || !canPublish} data-testid="facebook-publish-now"
              onClick={() => void act(async () => { await facebookApi.upload(pub.id, "PUBLISH", null); return "Publishing started: the file is uploaded and then made public. Progress is shown here."; })}>Publish now</Button>
          )}
          {!done && !running && !uploads.allow_public_publish && <span className="text-muted-foreground" data-testid="facebook-publish-off">Publishing is switched off (config/meta_publishing.json: facebook.allow_public_publish): the button works when it is switched on.</span>}
          {session && (FACEBOOK_ACTIVE.includes(session.status) || ["interrupted", "ready", "drafted"].includes(session.status)) && (
            <Button type="button" size="sm" variant="ghost" className={DISABLED} disabled={busy || session.status === "uploading"} onClick={() => void act(async () => { await facebookApi.cancel(pub.id); return "Forgotten here. Nothing was deleted on Facebook."; })}>Cancel upload</Button>
          )}
        </div>
      )}
      {message_ && <p role="status" className={message_.tone === "ok" ? "text-emerald-700 dark:text-emerald-400" : "text-rose-700 dark:text-rose-400"}>{message_.text}</p>}
    </div>
  );
}
