"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { api, ApiError, apiFileUrl, publishingAiApi, publishingApi, youtubeApi, type PublicationLamp, type PublicationMetadata, type PublicationView, type StudioAsset, type YouTubeChannel, type YouTubeUploads } from "@/lib/api";
import { AiPanel, ApprovePlanPanel, CalendarPanel, Preview } from "./publishing-intelligence";
import { InstagramConnectionPanel } from "./instagram-connection";
import { ConnectAll, LampReport, PORTALS, PublishingStatus, TestAll, usePortalLamps, type Portal } from "./publishing-portals";
import { TabBar } from "@/components/ui/tabs";
import { ACTIVE_UPLOAD, YouTubeChannelsPanel, YouTubeUpload } from "./youtube-publishing";
import { useApi } from "@/lib/hooks";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { TableSkeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/state";

const REFRESH_MS = 10_000;
/** Disabled actions must not look like available ones: flat, muted, dashed (the colour of the variant is overridden, not just dimmed). */
const DISABLED = "disabled:opacity-100 disabled:bg-muted disabled:text-muted-foreground disabled:border disabled:border-dashed disabled:border-border disabled:cursor-not-allowed";
const LANGUAGES = ["fa", "en", "de", "ar", "es", "it", "fr", "pt", "tr"];
const PLATFORM_LABEL: Record<string, string> = { youtube: "YouTube", facebook: "Facebook", instagram: "Instagram", tiktok: "TikTok" };
const SECTIONS = [...PORTALS, "Publishing plan"] as const;
const STUDIO_VIEWS = ["Overview", "Connect all", "Test all"] as const;
const VIEWS = ["Connection", "Content"] as const;
const CONNS = ["Languages", "Connect all", "Test all"] as const;
type Section = (typeof SECTIONS)[number];
type View = (typeof VIEWS)[number];
type Conn = (typeof CONNS)[number];
type StudioView = (typeof STUDIO_VIEWS)[number];
const STATUS_TONE: Record<string, "default" | "success" | "warning" | "danger" | "info"> = {
  draft: "default", content_approved: "info", publication_approved: "success", scheduled: "success", voided: "warning", expired: "warning",
  uploading: "info", processing: "info", ready: "info", publishing: "info", published: "success", failed: "danger", publish_uncertain: "danger",
};
const LINK_TONE: Record<string, "default" | "success" | "warning" | "danger" | "info"> = {
  url_recorded: "info", link_created: "info", clickability_verified: "success", manual_action_required: "warning", unsupported: "default",
};
const METHOD_LABEL: Record<string, string> = {
  related_video: "Related Video", link_post: "Link post", description_link: "Link in the description", profile_link: "Profile link",
  plain_text_cta: "Plain-text call to action", comment_to_dm: "Comment to DM",
};

function megabytes(bytes: number | null) {
  return bytes == null ? "-" : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

const tagsText = (tags: string[]) => tags.join(" ");
const parseTags = (text: string) => text.split(/[\s,،]+/).filter(Boolean);
const chaptersText = (items: { time: string; title: string }[]) => items.map((c) => `${c.time} ${c.title}`).join("\n");
const parseChapters = (text: string) => text.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => ({ time: l.split(/\s+/)[0], title: l.split(/\s+/).slice(1).join(" ") }));
const SOURCE_LABEL = { default: "default text", ai: "AI-written", owner: "edited by you" } as const;

function PublicationCard({ pub, language, advice, onChanged, extra }: { pub: PublicationView; language: string; advice?: { method: string; reason: string }; onChanged: () => void; extra?: ReactNode }) {
  const [caption, setCaption] = useState(pub.caption);
  const [title, setTitle] = useState(pub.metadata.title);
  const [hashtags, setHashtags] = useState(tagsText(pub.metadata.hashtags));
  const [keywords, setKeywords] = useState(pub.metadata.keywords.join(", "));
  const [chapters, setChapters] = useState(chaptersText(pub.metadata.chapters));
  const [cta, setCta] = useState(pub.metadata.cta);
  const [local, setLocal] = useState(pub.scheduled_local ?? "");
  const [visibility, setVisibility] = useState(pub.visibility);
  const [notes, setNotes] = useState<Record<number, string>>({});
  const [message, setMessage] = useState<{ tone: "ok" | "bad"; text: string; reasons?: string[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const label = `${PLATFORM_LABEL[pub.platform] ?? pub.platform} ${pub.kind} ${pub.asset_key}`;
  const editable = pub.eligibility.edit;                       // what may be done is decided by the backend, with the rules the actions themselves apply
  const metadataDirty = title !== pub.metadata.title || hashtags !== tagsText(pub.metadata.hashtags) || keywords !== pub.metadata.keywords.join(", ") || chapters !== chaptersText(pub.metadata.chapters) || cta !== pub.metadata.cta;
  const dirty = caption !== pub.caption || local !== (pub.scheduled_local ?? "") || visibility !== pub.visibility || metadataDirty;
  const metadataBody = (): Partial<PublicationMetadata> => ({ title, hashtags: parseTags(hashtags), keywords: keywords.split(/[,،]/).map((k) => k.trim()).filter(Boolean), chapters: parseChapters(chapters), cta });
  const posted = caption.length + (parseTags(hashtags).length > 0 ? 2 + parseTags(hashtags).join(" ").length : 0);
  // what the server holds changed (a save, a reload): the fields show it (the documented "adjust state while rendering" pattern, not an effect)
  const held = `${pub.caption}|${pub.scheduled_local}|${pub.visibility}|${JSON.stringify(pub.metadata)}`;
  const [seen, setSeen] = useState(held);
  if (seen !== held) {
    setSeen(held);
    setCaption(pub.caption);
    setTitle(pub.metadata.title);
    setHashtags(tagsText(pub.metadata.hashtags));
    setKeywords(pub.metadata.keywords.join(", "));
    setChapters(chaptersText(pub.metadata.chapters));
    setCta(pub.metadata.cta);
    setLocal(pub.scheduled_local ?? "");
    setVisibility(pub.visibility);
  }

  const why = (action: keyof PublicationView["eligibility"]) => {
    if (pub.eligibility[action] && !dirty) return undefined;
    if (dirty && action !== "withdraw") return "Save or discard your changes first.";
    const reasons = [...pub.blockers, ...(action === "schedule" ? pub.schedule_blockers : [])];
    return reasons.length > 0 ? reasons.join("; ") : `Not available while the publication is ${pub.status.replace("_", " ")}.`;
  };

  const run = async (work: () => Promise<unknown>, done: string) => {
    setBusy(true);
    setMessage(null);
    try {
      await work();
      setMessage({ tone: "ok", text: done });
    } catch (e) {
      const reasons = e instanceof ApiError && e.data && typeof e.data === "object" && Array.isArray((e.data as { reasons?: unknown }).reasons)
        ? ((e.data as { reasons: string[] }).reasons) : [];
      setMessage({ tone: "bad", text: reasons.length > 0 ? "Refused by the server:" : e instanceof Error ? e.message : String(e), reasons });
    } finally {
      setBusy(false);
      onChanged();
    }
  };

  return (
    <div className="space-y-3 rounded-md border border-border p-3" data-testid={`publication-${pub.platform}-${pub.kind}-${pub.asset_key}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">{PLATFORM_LABEL[pub.platform] ?? pub.platform}</span>
          <Badge variant={STATUS_TONE[pub.status] ?? "default"} data-testid="status">{pub.status.replace("_", " ")}</Badge>
          {pub.status === "publication_approved" || pub.status === "scheduled" ? <Badge variant={pub.approval_valid ? "success" : "warning"}>{pub.approval_valid ? "approval current" : "approval stale"}</Badge> : null}
          <Badge variant={pub.metadata_source === "ai" ? "accent" : pub.metadata_source === "owner" ? "info" : "default"} data-testid="metadata-source">{SOURCE_LABEL[pub.metadata_source]}</Badge>
          {pub.sequence != null && <Badge variant="outline" data-testid="sequence">#{pub.sequence} on this platform</Badge>}
        </div>
        <span className="text-xs text-muted-foreground" data-testid="file-note">{pub.prepared_copy ? "prepared copy" : "original file"} · hash {pub.content_hash || "-"} · {pub.cover.approved ? "cover approved" : "no approved cover"}</span>
      </div>

      {pub.suggestion && (
        <div className="flex flex-wrap items-center gap-2 rounded border border-indigo-500/30 bg-indigo-500/5 px-2 py-1 text-xs" data-testid="suggestion">
          <span>A different AI version of this text exists.</span>
          <Button type="button" size="sm" variant="outline" className={DISABLED} disabled={busy || !editable} onClick={() => void run(() => publishingAiApi.useSuggestion(pub.id), "AI version used.")}>Use the AI version</Button>
        </div>
      )}
      {pub.limit.title_max > 0 && (
        <label className="block text-xs font-medium">Title <span className="font-normal text-muted-foreground">{title.length} / {pub.limit.title_max}{pub.limit.title_required ? " · required" : ""}</span>
          <input aria-label={`Title ${label}`} value={title} disabled={!editable} onChange={(e) => setTitle(e.target.value)} dir={language === "fa" || language === "ar" ? "rtl" : "ltr"}
            className="mt-1 block w-full rounded border border-border bg-background px-2 py-1 text-sm font-normal" />
        </label>
      )}
      <label className="block text-xs font-medium">Caption <span className="font-normal text-muted-foreground" data-testid="caption-count">{posted} / {pub.limit.text_max} with hashtags{pub.limit.evidence === "unverified" ? " (limit not verified)" : ""}</span>
        <textarea aria-label={`Caption ${label}`} value={caption} disabled={!editable} onChange={(e) => setCaption(e.target.value)} dir={language === "fa" || language === "ar" ? "rtl" : "ltr"}
          className="mt-1 block min-h-16 w-full rounded border border-border bg-background px-2 py-1 text-sm font-normal" />
      </label>
      <label className="block text-xs font-medium">Hashtags <span className="font-normal text-muted-foreground">{parseTags(hashtags).length} (at most {pub.limit.hashtags_max}, {pub.limit.hashtags_recommended} recommended)</span>
        <input aria-label={`Hashtags ${label}`} value={hashtags} disabled={!editable} onChange={(e) => setHashtags(e.target.value)} dir={language === "fa" || language === "ar" ? "rtl" : "ltr"}
          className="mt-1 block w-full rounded border border-border bg-background px-2 py-1 text-sm font-normal" />
      </label>
      {pub.limit.keywords_max_chars > 0 && (
        <label className="block text-xs font-medium">Keywords <span className="font-normal text-muted-foreground">at most {pub.limit.keywords_max_chars} characters</span>
          <input aria-label={`Keywords ${label}`} value={keywords} disabled={!editable} onChange={(e) => setKeywords(e.target.value)} dir={language === "fa" || language === "ar" ? "rtl" : "ltr"}
            className="mt-1 block w-full rounded border border-border bg-background px-2 py-1 text-sm font-normal" />
        </label>
      )}
      {pub.limit.chapters && pub.kind === "film" && (
        <label className="block text-xs font-medium">Chapters <span className="font-normal text-muted-foreground">one per line: time, then title (the times come from the film)</span>
          <textarea aria-label={`Chapters ${label}`} value={chapters} disabled={!editable} onChange={(e) => setChapters(e.target.value)} dir={language === "fa" || language === "ar" ? "rtl" : "ltr"}
            className="mt-1 block min-h-16 w-full rounded border border-border bg-background px-2 py-1 text-sm font-normal" />
        </label>
      )}
      <label className="block text-xs font-medium">Call to action
        <input aria-label={`Call to action ${label}`} value={cta} disabled={!editable} onChange={(e) => setCta(e.target.value)} dir={language === "fa" || language === "ar" ? "rtl" : "ltr"}
          className="mt-1 block w-full rounded border border-border bg-background px-2 py-1 text-sm font-normal" />
      </label>
      {pub.metadata_findings.length > 0 && (
        <ul className="space-y-0.5 text-xs" data-testid="metadata-findings">
          {pub.metadata_findings.map((f, i) => <li key={`${i}-${f.type}-${f.message}`} className={f.severity === "error" ? "text-rose-700 dark:text-rose-400" : "text-amber-700 dark:text-amber-400"}>{f.severity === "error" ? "Must fix" : "Note"}: {f.message}</li>)}
        </ul>
      )}
      {(advice || pub.metadata.routing_note) && (
        <p className="text-xs text-muted-foreground" data-testid="routing-advice">{advice && <>AI advice: {advice.method.replaceAll("_", " ")}. {advice.reason} </>}{pub.metadata.routing_note}</p>
      )}
      <div className="flex flex-wrap items-end gap-3">
        <label className="text-xs font-medium">Publish at (local time of the language)
          <input aria-label={`Publish at ${label}`} type="datetime-local" value={local} disabled={!editable} onChange={(e) => setLocal(e.target.value)}
            className="mt-1 block rounded border border-border bg-background px-2 py-1 text-sm font-normal" />
        </label>
        <label className="text-xs font-medium">Visibility
          <Select aria-label={`Visibility ${label}`} value={visibility} disabled={!editable} onChange={(e) => setVisibility(e.target.value)} className="mt-1 w-32">
            <option value="private">private</option>
            <option value="unlisted">unlisted</option>
            <option value="public">public</option>
          </Select>
        </label>
        <Button type="button" variant="outline" size="sm" className={DISABLED} title={dirty ? undefined : "Nothing to save."} disabled={!dirty || busy || !editable}
          onClick={() => void run(() => publishingApi.edit(pub.id, { caption, scheduled_local: local || undefined, clear_schedule: !local && pub.scheduled_local != null, visibility, ...(metadataDirty ? { metadata: metadataBody() } : {}) }), "Saved.")}>
          Save changes
        </Button>
      </div>

      {pub.blockers.length > 0 && (
        <div className="text-xs text-rose-700 dark:text-rose-400" data-testid="blockers">
          Blocked, each reason on its own:
          <ul className="list-disc space-y-0.5 pl-5">{pub.blockers.map((b, i) => <li key={`${i}-${b}`}>{b}</li>)}</ul>
        </div>
      )}
      {pub.warnings.length > 0 && <p className="text-xs text-muted-foreground">{pub.warnings.join(" ")}</p>}
      {pub.schedule_blockers.length > 0 && (
        <div className="text-xs text-amber-700 dark:text-amber-400" data-testid="schedule-blockers">
          Cannot be scheduled yet:
          <ul className="list-disc pl-5">{pub.schedule_blockers.map((b, i) => <li key={`${i}-${b}`}>{b}</li>)}</ul>
        </div>
      )}
      {pub.error && <p className="text-xs text-rose-700 dark:text-rose-400">{pub.error}</p>}

      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" variant="outline" className={DISABLED} title={why("approve_content")} disabled={busy || dirty || !pub.eligibility.approve_content}
          onClick={() => void run(() => publishingApi.approveContent(pub.id), "Content approved.")}>Approve content</Button>
        <Button type="button" size="sm" className={DISABLED} title={why("approve_publication")} disabled={busy || dirty || !pub.eligibility.approve_publication}
          onClick={() => void run(() => publishingApi.approvePublication(pub.id), "Publication approved.")}>Approve publication</Button>
        <Button type="button" size="sm" variant="outline" className={DISABLED} title={why("schedule")} disabled={busy || dirty || !pub.eligibility.schedule}
          onClick={() => void run(() => publishingApi.schedule(pub.id), "Scheduled.")}>Schedule</Button>
        <Button type="button" size="sm" variant="ghost" className={DISABLED} title={why("withdraw")} disabled={busy || !pub.eligibility.withdraw}
          onClick={() => void run(() => publishingApi.revoke(pub.id), "Approval withdrawn.")}>Withdraw approval</Button>
      </div>
      {pub.publication_approved_at && pub.approval_expires_at && (
        <p className="text-xs text-muted-foreground">Approved {new Date(pub.publication_approved_at).toLocaleString()} · valid until {new Date(pub.approval_expires_at).toLocaleString()}</p>
      )}
      {message && (
        <div role="status" className={`text-xs ${message.tone === "ok" ? "text-emerald-700 dark:text-emerald-400" : "text-rose-700 dark:text-rose-400"}`}>
          {(() => {
            const fresh = (message.reasons ?? []).filter((r) => !pub.blockers.includes(r));       // a reason that is already listed above is not repeated
            const listed = (message.reasons ?? []).length > 0 && fresh.length === 0;
            return (
              <>
                <p>{listed ? "Refused by the server: the reasons are listed above." : message.text}</p>
                {fresh.length > 0 && <ul className="list-disc space-y-0.5 pl-5" data-testid="refusal-reasons">{fresh.map((r, i) => <li key={`${i}-${r}`}>{r}</li>)}</ul>}
              </>
            );
          })()}
        </div>
      )}

      {extra}

      {pub.links.length > 0 && (
        <div className="space-y-2 border-t border-border pt-2" data-testid="routing">
          <div className="text-xs font-medium">How viewers reach the film</div>
          {pub.links.map((link) => (
            <div key={link.id} className="flex flex-wrap items-center gap-2 text-xs">
              <span className="min-w-44">{METHOD_LABEL[link.method] ?? link.method} → {link.target}</span>
              <Badge variant={LINK_TONE[link.status] ?? "default"}>{link.status.replaceAll("_", " ")}</Badge>
              <span className="text-muted-foreground">{link.url ?? "no URL yet"}</span>
              <Select aria-label={`Link status ${link.method} ${label}`} value={link.status} className="w-52"
                onChange={(e) => void run(() => publishingApi.routing(link.id, { status: e.target.value, evidence_note: notes[link.id] }), "Link updated.")}>
                {["url_recorded", "link_created", "clickability_verified", "manual_action_required", "unsupported"].map((s) => <option key={s} value={s}>{s.replaceAll("_", " ")}</option>)}
              </Select>
              <input aria-label={`Evidence ${link.method} ${label}`} placeholder="How it was checked" defaultValue={link.evidence_note}
                onChange={(e) => setNotes({ ...notes, [link.id]: e.target.value })} className="min-w-48 rounded border border-border bg-background px-2 py-1" />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function CoverBlock({ caseId, language, asset, onChanged }: { caseId: number; language: string; asset: StudioAsset; onChanged: () => void }) {
  const [asking, setAsking] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const key = asset.kind === "film" ? "long" : asset.key;
  const make = async () => {
    setBusy(true); setNote(null); setAsking(false);
    try { await api.makeCoverImage(caseId, language, key, true); setNote("A new picture was made. Approve it in the Covers tab of the documentary workspace; this Studio then uses it."); } catch (e) { setNote(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); onChanged(); }
  };
  return (
    <div className="space-y-1 text-xs" data-testid={`cover-${asset.key}`}>
      {/* eslint-disable-next-line @next/next/no-img-element -- the cover is served by our own API, not an optimizable static asset */}
      {asset.cover_url ? <img src={apiFileUrl(asset.cover_url)} alt={`Cover ${asset.key}`} className={asset.kind === "film" ? "aspect-video w-48 rounded object-cover" : "aspect-[9/16] w-20 rounded object-cover"} /> : <p className="text-muted-foreground">No cover picture yet.</p>}
      <p className="text-muted-foreground">Cover: {asset.cover_state}</p>
      {asking ? (
        <span className="flex items-center gap-2">Asks the image model (paid).
          <Button type="button" size="sm" onClick={() => void make()}>Yes, make one</Button><Button type="button" size="sm" variant="ghost" onClick={() => setAsking(false)}>Cancel</Button></span>
      ) : (
        <Button type="button" size="sm" variant="outline" className={DISABLED} disabled={busy} onClick={() => setAsking(true)}>{busy ? "Making…" : "Generate an alternative cover"}</Button>
      )}
      {note && <p role="status" className="max-w-56 text-muted-foreground">{note}</p>}
    </div>
  );
}

type YouTubeProps = { uploads: YouTubeUploads | null; status: YouTubeChannel | null; refetchUploads: () => void; filmUrl: string | null };

function AssetBlock({ caseId, asset, language, advice, onChanged, youtube, platform }: { caseId: number; asset: StudioAsset; language: string; advice: Record<string, { method: string; reason: string }>; onChanged: () => void; youtube: YouTubeProps | null; platform?: string }) {
  const publications = platform ? asset.publications.filter((p) => p.platform === platform) : asset.publications;
  if (platform && publications.length === 0) return null;
  return (
    <div id={`asset-view-${asset.key}`} className="scroll-mt-4">
    <Card data-testid={`asset-${asset.key}`}>
      <CardHeader>
        <CardTitle>{asset.kind === "film" ? `Film · ${asset.label}` : asset.label}</CardTitle>
        <span className="text-xs text-muted-foreground">{asset.file ?? "no file"} · {megabytes(asset.bytes)} · cover: {asset.cover_state}</span>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-start gap-4">
          <Preview url={asset.preview_url} kind={asset.kind} />
          <CoverBlock caseId={caseId} language={language} asset={asset} onChanged={onChanged} />
        </div>
        {publications.length === 0
          ? <p className="text-sm text-muted-foreground">Not planned yet. Create the plan to see its destinations.</p>
          : publications.map((pub) => <PublicationCard key={pub.id} pub={pub} language={language} advice={advice[`${pub.platform}/${pub.kind}`]} onChanged={onChanged}
              extra={pub.platform === "youtube" && youtube ? <YouTubeUpload pub={pub} onChanged={onChanged} {...youtube} /> : undefined} />)}
      </CardContent>
    </Card>
    </div>
  );
}

/** The Publishing Studio of one case: the film and the ShortClips of a language, their destinations, approvals and routing links. */
export function PublishingTab({ caseId }: { caseId: number }) {
  const [language, setLanguage] = useState(() => (typeof window === "undefined" ? "fa" : new URLSearchParams(window.location.search).get("language") ?? "fa"));
  const { data, error, loading, refetch } = useApi(() => publishingApi.studio(caseId, language), [caseId, language], { keepPrevious: true });
  const status = useApi(() => publishingApi.overview(caseId), [caseId], { keepPrevious: true });
  const ai = useApi(() => publishingAiApi.state(caseId, language), [caseId, language], { keepPrevious: true });
  const refetchAi = ai.refetch;
  const refetchStatus = status.refetch;
  useEffect(() => { refetchStatus(); }, [data, refetchStatus]);                // an edit, approval or schedule of the language changes its lamps
  const ytChannels = useApi(() => youtubeApi.channels(), [], { keepPrevious: true });
  const youtubeOn = !!ytChannels.data?.channels.some((c) => c.language === language);
  const yt = useApi(() => (youtubeOn ? youtubeApi.uploads(caseId, language) : Promise.resolve(null)), [caseId, language, youtubeOn], { keepPrevious: true });
  const refetchUploads = yt.refetch;
  const uploadActive = Object.values(yt.data?.uploads ?? {}).some((u) => u.session !== null && ACTIVE_UPLOAD.includes(u.session.status));
  useEffect(() => {
    if (!uploadActive) return;
    const timer = setInterval(() => { refetchUploads(); refetch(); }, 3000);
    return () => clearInterval(timer);
  }, [uploadActive, refetchUploads, refetch]);
  const [startDate, setStartDate] = useState("");
  const [planning, setPlanning] = useState(false);
  const initial = (name: string, allowed: readonly string[], fallback: string) => { const v = typeof window === "undefined" ? null : new URLSearchParams(window.location.search).get(name); return v && allowed.includes(v) ? v : fallback; };
  const [section, setSection] = useState<Section>(() => initial("portal", SECTIONS, "YouTube") as Section);
  const [view, setView] = useState<View>(() => initial("view", VIEWS, "Connection") as View);
  const [studioView, setStudioView] = useState<StudioView>(() => initial("studio", STUDIO_VIEWS, "Overview") as StudioView);
  const [conn, setConn] = useState<Conn>(() => initial("conn", CONNS, "Languages") as Conn);
  const portals = usePortalLamps();
  const target = useRef<{ language: string; key: string } | null>(null);
  const [opened, setOpened] = useState(0);
  const choose = (setter: (v: never) => void, name: string) => (value: string) => {
    (setter as (v: string) => void)(value);
    const params = new URLSearchParams(window.location.search);
    params.set(name, value);
    window.history.replaceState(null, "", `${window.location.pathname}?${params.toString()}`);
  };

  /** A lamp of the status grid: go to that portal's Content view of the language and show the video and cover of the clicked film or Short. */
  const openAsset = (portal: Portal, pub: PublicationLamp) => {
    setLanguage(pub.language);
    choose(setSection, "portal")(portal);
    choose(setView, "view")("Content");
    target.current = { language: pub.language, key: pub.asset_key };
    setOpened((n) => n + 1);
  };
  useEffect(() => {
    const wanted = target.current;
    if (!wanted || data?.language !== wanted.language || view !== "Content") return;
    const el = document.getElementById(`asset-view-${wanted.key}`);
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "start" });
    el.classList.add("ring-2", "ring-sky-500", "rounded-lg");
    setTimeout(() => el.classList.remove("ring-2", "ring-sky-500"), 2500);
    target.current = null;
  }, [opened, data, view]);

  // Eligibility is decided by the server and can change under an open page (an approval ages, a requirement stops holding): fetch again every few seconds and whenever the page returns.
  useEffect(() => {
    const again = () => { if (document.visibilityState === "visible") { refetch(); refetchAi(); } };
    const timer = setInterval(again, REFRESH_MS);
    document.addEventListener("visibilitychange", again);
    window.addEventListener("focus", again);
    return () => { clearInterval(timer); document.removeEventListener("visibilitychange", again); window.removeEventListener("focus", again); };
  }, [refetch, refetchAi]);
  const [planError, setPlanError] = useState<string | null>(null);

  if (!data && loading) return <TableSkeleton rows={4} cols={3} />;
  if (!data && error) return <ErrorState message={error} onRetry={refetch} />;
  if (!data) return null;

  const filmUrl = data.assets.find((a) => a.kind === "film")?.publications.find((p) => p.platform === "youtube")?.remote_url ?? null;

  const makePlan = async (reset: boolean) => {
    setPlanning(true);
    setPlanError(null);
    try {
      await publishingApi.plan(caseId, language, { start_date: startDate || undefined, reset_schedule: reset });
    } catch (e) {
      setPlanError(e instanceof Error ? e.message : String(e));
    } finally {
      setPlanning(false);
      refetch();
    }
  };

  const portal: Portal | null = (PORTALS as readonly string[]).includes(section) ? (section as Portal) : null;
  const scope: readonly Portal[] = portal ? [portal] : [];
  const base = `/cases/${caseId}?tab=Publishing&language=${language}&portal=${encodeURIComponent(section)}&view=${view}&conn=${encodeURIComponent(conn)}`;
  const returnTo = base;
  const studioReturnTo = `${base}&studio=${encodeURIComponent(studioView)}`;
  const youtubeProps: YouTubeProps | null = youtubeOn ? { uploads: yt.data ?? null, status: ytChannels.data?.channels.find((c) => c.language === language) ?? null, refetchUploads, filmUrl } : null;
  const visibleAssets = data.assets.filter((a) => !portal || a.publications.some((p) => p.platform === portal.toLowerCase()));

  const scheduleInfo = data.scheduled && data.schedule ? (
    <p className="text-muted-foreground">
      Schedule: {data.schedule.publish_time_local} ({data.schedule.timezone}) · an approval is valid for {data.approval_max_age_days} days · automatic window {data.missed_window_minutes} minutes.
      Nothing here publishes: this is the plan and its approvals. A YouTube upload is private and needs your typed confirmation.
    </p>
  ) : <p className="text-amber-700 dark:text-amber-400">This language has no publishing schedule (config/publishing.json), so its publishing is not active.</p>;

  const content = visibleAssets.length === 0
    ? <EmptyState title={portal ? `Nothing to publish on ${portal} yet` : "Nothing to publish yet"} description={portal ? `This language has no ${portal} content for this case.` : "This language has no film and no valid ShortClip for this case."} />
    : visibleAssets.map((asset) => <AssetBlock key={`${asset.kind}-${asset.key}`} caseId={caseId} asset={asset} language={language} advice={ai.data?.latest?.result?.analysis.routing ?? {}} onChanged={refetch}
        youtube={youtubeProps} platform={portal ? portal.toLowerCase() : undefined} />);

  const connection = (
    <div className="space-y-4">
      {portal && <Card><CardContent className="pt-4"><LampReport lamps={portals.lamps} portals={[portal]} /></CardContent></Card>}
      <TabBar tabs={CONNS} value={conn} onChange={choose(setConn, "conn")} idPrefix="publishing-conn" label="Connection views" />
      {conn === "Languages" && (portal === "YouTube" ? <YouTubeChannelsPanel channels={ytChannels.data?.channels ?? null} onChanged={ytChannels.refetch} language={language} returnTo={returnTo} />
        : portal === "Instagram" ? <InstagramConnectionPanel language={language} returnTo={returnTo} />
        : <Card><CardContent className="pt-4 text-sm text-muted-foreground">The {portal} connection is not built yet: no account can be connected here, and nothing can be published to {portal}.</CardContent></Card>)}
      {conn === "Connect all" && <ConnectAll portals={scope} returnTo={returnTo} lamps={portals.lamps} loaded={portals.loaded} />}
      {conn === "Test all" && <TestAll portals={scope} lamps={portals.lamps} onDone={portals.refetch} />}
    </div>
  );

  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Publishing Studio</CardTitle>
          <div className="[&>div]:mb-0" data-testid="language-tabs">
            <TabBar tabs={LANGUAGES.map((l) => l.toUpperCase())} value={language.toUpperCase()} onChange={(l) => setLanguage(l.toLowerCase())} idPrefix="publishing-language" label="Language" />
          </div>
        </CardHeader>
        <CardContent className="space-y-3 text-sm">
          <LampReport lamps={portals.lamps} />
          <TabBar tabs={STUDIO_VIEWS} value={studioView} onChange={choose(setStudioView, "studio")} idPrefix="publishing-studio" label="All portals" />
          {studioView === "Overview" && scheduleInfo}
          {studioView === "Connect all" && <ConnectAll portals={PORTALS} returnTo={studioReturnTo} lamps={portals.lamps} loaded={portals.loaded} />}
          {studioView === "Test all" && <TestAll portals={PORTALS} lamps={portals.lamps} onDone={portals.refetch} />}
          <p className="pt-1 text-xs font-medium text-muted-foreground">Publishing status, every language</p>
          <PublishingStatus publications={status.data?.publications ?? []} onOpen={openAsset} />
        </CardContent>
      </Card>
      {error && <p className="text-xs text-rose-500">Could not refresh: {error}</p>}
      <TabBar tabs={SECTIONS} value={section} onChange={choose(setSection, "portal")} idPrefix="publishing-section" label="Portals" />
      {section === "Publishing plan" ? (
        <div className="space-y-4" data-testid="publishing-plan-tab">
          <Card>
            <CardHeader><CardTitle>Publishing plan</CardTitle></CardHeader>
            <CardContent className="space-y-3 text-sm">
              {scheduleInfo}
              {data.scheduled && (
                <div className="flex flex-wrap items-end gap-3">
                  <label className="text-xs font-medium">Plan starts on
                    <input aria-label="Plan start date" type="date" value={startDate || data.default_start || ""} onChange={(e) => setStartDate(e.target.value)}
                      className="mt-1 block rounded border border-border bg-background px-2 py-1 text-sm font-normal" />
                  </label>
                  <Button type="button" size="sm" disabled={planning} onClick={() => void makePlan(false)}>{data.planned ? "Refresh plan" : "Create plan"}</Button>
                  {data.planned && <Button type="button" size="sm" variant="outline" disabled={planning} onClick={() => void makePlan(true)}>Reset unapproved times</Button>}
                </div>
              )}
              {planError && <p role="alert" className="text-xs text-rose-700 dark:text-rose-400">{planError}</p>}
            </CardContent>
          </Card>
          {data.scheduled && <AiPanel caseId={caseId} language={language} studio={data} data={ai.data} error={ai.error} refetch={refetchAi} onChanged={refetch} />}
          {data.scheduled && data.planned && <CalendarPanel studio={data} onChanged={refetch} />}
          {data.scheduled && data.planned && <ApprovePlanPanel caseId={caseId} language={language} onChanged={refetch} />}
        </div>
      ) : (
        <div className="space-y-4" data-testid={`portal-${section.toLowerCase().replace(" ", "-")}`}>
          <TabBar tabs={VIEWS} value={view} onChange={choose(setView, "view")} idPrefix="publishing-view" label="Portal views" />
          {view === "Connection" ? connection : content}
        </div>
      )}
    </div>
  );
}
