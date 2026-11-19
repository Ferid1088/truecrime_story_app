"use client";

import { useEffect, useRef, useState } from "react";
import { facebookApi, metaPublishingApi, publishingApi, youtubeApi, type AccountConnection, type ConnectionState, type LampState, type PublicationLamp } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export const PORTALS = ["YouTube", "Instagram", "Facebook", "TikTok"] as const;
export type Portal = (typeof PORTALS)[number];
export const LANGUAGES = ["fa", "en", "de", "ar", "es", "it", "fr", "pt", "tr"];
const DISABLED = "disabled:opacity-100 disabled:bg-muted disabled:text-muted-foreground disabled:border disabled:border-dashed disabled:border-border disabled:cursor-not-allowed";
const PORTAL_OF: Record<string, Portal> = { youtube: "YouTube", instagram: "Instagram", facebook: "Facebook" };
const NOT_BUILT = "The connection for this portal is not built yet.";

/** One lamp of the report: a language on a portal. `ok` is green only when the account is connected, is the registered one and holds every permission. */
export type Lamp = { portal: Portal; language: string; ok: boolean; why: string; registered: boolean; testable: boolean };

const STATE_TEXT: Record<ConnectionState, string> = {
  verified: "verified", known_not_authenticated: "not connected", not_created: "account not created", awaiting_creation: "awaiting channel creation", auth_failed: "last check failed",
  needs_owner_authorization: "needs owner authorization",
};

function lampOf(row: AccountConnection): Lamp {
  return { portal: PORTAL_OF[row.platform], language: row.language, ok: row.state === "verified", why: row.state === "auth_failed" && row.last_error ? `last check failed: ${row.last_error}` : STATE_TEXT[row.state],
    registered: row.account_id !== null, testable: row.testable && row.state !== "known_not_authenticated" };
}

/** The connection state of every portal and language: the one source of the lamps, Connect all and Test all (the server's connection table: one row per language and platform). */
export function usePortalLamps() {
  const table = useApi(() => publishingApi.connections(), [], { keepPrevious: true });
  const rows = table.data?.connections ?? [];
  const lamps: Lamp[] = PORTALS.flatMap((portal) => LANGUAGES.map((language): Lamp => {
    const row = rows.find((r) => r.platform === portal.toLowerCase() && r.language === language);
    return row ? lampOf(row) : { portal, language, ok: false, why: portal === "TikTok" ? NOT_BUILT : "no account configured", registered: false, testable: false };
  }));
  return { lamps, table, loaded: !!table.data, refetch: table.refetch };
}

function Dot({ lamp }: { lamp: Lamp }) {
  return <span role="img" aria-label={`${lamp.portal} ${lamp.language}: ${lamp.ok ? "connected" : "disconnected"}`} title={`${lamp.portal} ${lamp.language.toUpperCase()}: ${lamp.why}`} data-testid={`lamp-${lamp.portal.toLowerCase()}-${lamp.language}`}
    data-state={lamp.ok ? "on" : "off"} className={`inline-block h-3.5 w-3.5 rounded-full ${lamp.ok ? "bg-emerald-500 shadow-[0_0_6px_1px_rgba(16,185,129,0.6)]" : "bg-rose-500"}`} />;
}

/** The small report: a green lamp for every connected language of a portal, a red one for every disconnected one. */
export function LampReport({ lamps, portals = PORTALS }: { lamps: Lamp[]; portals?: readonly Portal[] }) {
  return (
    <div className="overflow-x-auto" data-testid="lamp-report">
      <table className="text-xs">
        <thead><tr><th className="pr-4 text-left font-medium text-muted-foreground">Portal</th>{LANGUAGES.map((l) => <th key={l} className="px-2 text-center font-medium text-muted-foreground">{l.toUpperCase()}</th>)}<th className="pl-3 text-left font-medium text-muted-foreground">Connected</th></tr></thead>
        <tbody>
          {portals.map((portal) => {
            const row = lamps.filter((l) => l.portal === portal);
            return (
              <tr key={portal}>
                <td className="py-1 pr-4 font-medium">{portal}</td>
                {row.map((l) => <td key={l.language} className="px-2 py-1 text-center"><Dot lamp={l} /></td>)}
                <td className="pl-3 text-muted-foreground" data-testid={`lamp-count-${portal.toLowerCase()}`}>{row.filter((l) => l.ok).length} of {row.length}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** "YouTube DE, AR, ES: channel id not registered": the lamps that share a portal and a reason, in one line. */
function grouped(lamps: Lamp[]): string[] {
  const groups = new Map<string, string[]>();
  for (const l of lamps) groups.set(`${l.portal}: ${l.why}`, [...(groups.get(`${l.portal}: ${l.why}`) ?? []), l.language.toUpperCase()]);
  return [...groups].map(([key, langs]) => { const [portal, ...why] = key.split(": "); return `${portal} ${langs.join(", ")}: ${why.join(": ")}`; });
}

type Result = { ok: boolean; text: string };
const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** Connect all: Google and Facebook each need the owner to approve in the browser, so the languages are connected one after the other; each return from the provider continues with the next. Every connection is still verified on its own. */
export function ConnectAll({ portals, returnTo, lamps, loaded }: { portals: readonly Portal[]; returnTo: string; lamps: Lamp[]; loaded: boolean }) {
  const scope = portals.length === 1 ? portals[0].toLowerCase() : "all";
  const pending = lamps.filter((l) => portals.includes(l.portal) && !l.ok && l.registered && (l.portal === "YouTube" || l.portal === "Instagram"));
  const skipped = lamps.filter((l) => portals.includes(l.portal) && !l.ok && !pending.includes(l));
  const [error, setError] = useState<string | null>(() => {
    const params = typeof window === "undefined" ? new URLSearchParams() : new URLSearchParams(window.location.search);
    return params.get("connect_all") === scope && (params.get("youtube") ?? params.get("instagram")) === "error" ? params.get("detail") ?? "The last connection failed, so the remaining ones were not started." : null;
  });
  const [busy, setBusy] = useState(false);
  const continued = useRef(false);
  const next = async (list: Lamp[]) => {
    const first = list[0];
    if (!first) return;
    setBusy(true); setError(null);
    try {
      const back = `${returnTo}&connect_all=${scope}`;
      const { authorization_url } = first.portal === "YouTube" ? await youtubeApi.connect(first.language, back) : await metaPublishingApi.connect(back, first.language);
      window.location.assign(authorization_url);
    } catch (e) { setError(message(e)); setBusy(false); }
  };
  useEffect(() => {
    if (!loaded || continued.current) return;
    const params = new URLSearchParams(window.location.search);
    if (params.get("connect_all") !== scope) return;
    continued.current = true;
    const outcome = params.get("youtube") ?? params.get("instagram");
    params.delete("connect_all");
    window.history.replaceState(null, "", `${window.location.pathname}?${params.toString()}`);
    if (outcome === "connected") setTimeout(() => void next(pending), 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded]);
  return (
    <Card data-testid={`connect-all-${scope}`}>
      <CardHeader><CardTitle>Connect all{portals.length === 1 ? ` ${portals[0]} channels` : " portals"}</CardTitle></CardHeader>
      <CardContent className="space-y-3 text-xs">
        {portals.every((p) => p === "Facebook")
          ? <p className="text-muted-foreground" data-testid="connect-all-facebook-note">Facebook has nothing to connect here: the Page «رد خاموش» is reached with the same Meta system-user token as Instagram, so no browser approval is needed. The lamp is green when the last Test connection passed. Press <b>Test all</b> (or Test connection under Languages) to check it again; the Page&apos;s uploads are under <b>Content</b>.</p>
          : <p className="text-muted-foreground">Each language is approved in your browser, one after the other: after every approval you return here and the next one starts. If one fails or picks the wrong account, nothing is stored for it and the rest stop.</p>}
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" size="sm" className={DISABLED} disabled={busy || pending.length === 0} onClick={() => void next(pending)} data-testid={`connect-all-start-${scope}`}>
            {pending.length === 0 ? "Nothing left to connect" : `Connect ${pending.length} ${pending.length === 1 ? "account" : "accounts"}`}
          </Button>
          {pending.map((l) => <Badge key={`${l.portal}-${l.language}`} variant="warning">{l.portal} {l.language.toUpperCase()} next</Badge>)}
        </div>
        {skipped.length > 0 && (
          <div className="space-y-0.5">
            <p className="font-medium">Cannot be connected yet:</p>
            <ul className="list-disc pl-5 text-muted-foreground">{grouped(skipped).map((line) => <li key={line}>{line}</li>)}</ul>
          </div>
        )}
        {error && <p role="status" data-testid="connect-all-error" className="rounded border border-rose-500/50 bg-rose-500/10 px-3 py-2 font-medium text-rose-700 dark:text-rose-400">✗ {error}</p>}
      </CardContent>
    </Card>
  );
}

/** Test all: a read-only check of every connected account, one after the other. */
export function TestAll({ portals, lamps, onDone }: { portals: readonly Portal[]; lamps: Lamp[]; onDone: () => void }) {
  const scope = portals.length === 1 ? portals[0].toLowerCase() : "all";
  const all = lamps.filter((l) => portals.includes(l.portal));
  const testable = (l: Lamp) => l.testable;
  const rows = all.filter(testable);
  const untestable = all.filter((l) => !testable(l));
  const [results, setResults] = useState<Record<string, Result>>({});
  const [running, setRunning] = useState(false);
  const key = (l: Lamp) => `${l.portal}-${l.language}`;
  const run = async () => {
    setRunning(true); setResults({});
    for (const l of rows) {
      let result: Result;
      try {
        if (l.portal === "YouTube") { const r = await youtubeApi.test(l.language); result = { ok: true, text: `${r.channel_title} (${r.channel_id}): token works, permissions ${r.scopes.join(", ")}` }; }
        else if (l.portal === "Facebook") { const r = await facebookApi.testConnection(l.language); result = { ok: true, text: `${r.page.name} (${r.page.id}): token works, Page token verified, linked to Instagram @${r.linked_instagram.username}, permissions ${r.permissions.join(", ")}` }; }
        else { const r = await metaPublishingApi.testConnection(l.language); result = { ok: true, text: `@${r.account.username} (${r.account.id}): token works, permissions ${r.permissions.join(", ")}` }; }
      } catch (e) { result = { ok: false, text: message(e) }; }
      setResults((r) => ({ ...r, [key(l)]: result }));
    }
    setRunning(false); onDone();
  };
  return (
    <Card data-testid={`test-all-${scope}`}>
      <CardHeader><CardTitle>Test all{portals.length === 1 ? ` ${portals[0]} channels` : " portals"}</CardTitle></CardHeader>
      <CardContent className="space-y-3 text-xs">
        <p className="text-muted-foreground">Asks every connected account for its identity: the token must work and the account must be the registered one. Nothing is uploaded or published.</p>
        <Button type="button" size="sm" disabled={running} onClick={() => void run()} data-testid={`test-all-start-${scope}`}>{running ? "Testing…" : "Test all connections"}</Button>
        <ul className="space-y-1">
          {rows.map((l) => {
            const r = results[key(l)];
            return (
              <li key={key(l)} data-testid={`test-result-${l.portal.toLowerCase()}-${l.language}`} className="flex flex-wrap items-center gap-2">
                <span className="w-40 font-medium">{l.portal} {l.language.toUpperCase()}</span>
                {r ? <span className={r.ok ? "text-emerald-700 dark:text-emerald-400" : "text-rose-700 dark:text-rose-400"}>{r.ok ? "✓" : "✗"} {r.text}</span> : <span className="text-muted-foreground">{l.ok ? "connected: not tested yet" : l.why}</span>}
              </li>
            );
          })}
        </ul>
        {untestable.length > 0 && <div className="space-y-0.5 text-muted-foreground" data-testid="test-all-untested"><p className="font-medium text-foreground">Not tested (nothing connected):</p>{grouped(untestable).map((line) => <p key={line}>{line}</p>)}</div>}
      </CardContent>
    </Card>
  );
}

const STATE_STYLE: Record<LampState, { dot: string; label: string }> = {
  published: { dot: "bg-emerald-500 shadow-[0_0_6px_1px_rgba(16,185,129,0.6)]", label: "published" },
  waiting: { dot: "bg-sky-500", label: "waiting for its publishing time" },
  failed: { dot: "bg-rose-500", label: "publishing failed" },
  uncertain: { dot: "bg-amber-500", label: "outcome unknown: check the platform" },
  unscheduled: { dot: "bg-muted-foreground/30", label: "not scheduled" },
};

/** The publishing plan against what really happened, per portal and language, one lamp per film and Short: green published, blue waiting for its time, red failed. */
export function PublishingStatus({ publications, onOpen, portals = PORTALS }: { publications: PublicationLamp[]; onOpen: (portal: Portal, publication: PublicationLamp) => void; portals?: readonly Portal[] }) {
  const count = (s: LampState) => publications.filter((p) => p.state === s).length;
  return (
    <div className="space-y-2" data-testid="publishing-status">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        {(Object.keys(STATE_STYLE) as LampState[]).map((s) => (
          <span key={s} className="flex items-center gap-1.5"><span className={`inline-block h-3 w-3 rounded-full ${STATE_STYLE[s].dot}`} />{STATE_STYLE[s].label} <span data-testid={`status-count-${s}`}>({count(s)})</span></span>
        ))}
      </div>
      <div className="overflow-x-auto">
        <table className="text-xs">
          <thead><tr><th className="pr-4 text-left font-medium text-muted-foreground">Portal</th>{LANGUAGES.map((l) => <th key={l} className="px-2 text-center font-medium text-muted-foreground">{l.toUpperCase()}</th>)}</tr></thead>
          <tbody>
            {portals.map((portal) => (
              <tr key={portal}>
                <td className="py-1 pr-4 font-medium">{portal}</td>
                {LANGUAGES.map((language) => {
                  const cell = publications.filter((p) => p.platform === portal.toLowerCase() && p.language === language).sort((a, b) => (a.kind === b.kind ? a.asset_key.localeCompare(b.asset_key) : a.kind === "film" ? -1 : 1));
                  return (
                    <td key={language} className="px-2 py-1" data-testid={`status-cell-${portal.toLowerCase()}-${language}`}>
                      {cell.length === 0 ? <span className="block text-center text-muted-foreground/50">–</span> : (
                        <span className="flex items-center justify-center gap-1">
                          {cell.map((p) => (
                            <button key={p.id} type="button" data-testid={`status-${portal.toLowerCase()}-${language}-${p.asset_key}`} data-state={p.state} onClick={() => onOpen(portal, p)}
                              aria-label={`${portal} ${language.toUpperCase()} ${p.kind === "film" ? "film" : p.asset_key}: ${STATE_STYLE[p.state].label}. Open it.`}
                              title={`${p.kind === "film" ? "Film" : p.asset_key} · ${STATE_STYLE[p.state].label}${p.scheduled_at ? ` · ${new Date(p.scheduled_at).toLocaleString()}` : ""}${p.state === "failed" && p.error ? ` · ${p.error}` : ""} (click to open the video and cover)`}
                              className={`inline-flex h-6 min-w-6 cursor-pointer items-center justify-center rounded-full px-1 text-[10px] font-semibold leading-none text-white hover:ring-2 hover:ring-foreground/40 ${STATE_STYLE[p.state].dot}`}>
                              {p.kind === "film" ? "F" : p.asset_key}
                            </button>
                          ))}
                        </span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-muted-foreground">Each lamp is labelled: F is the film, C1 to C5 are the Shorts. Click a lamp to open that video and its cover; hover it for its time and, when it failed, why.</p>
    </div>
  );
}
