"use client";

import { useState } from "react";
import { RefreshCw } from "lucide-react";
import { api, apiFileUrl } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatDateTime, langLabel } from "@/lib/format";
import type { OutfitLibraryOutfit } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

/** The outfit library for one language: exactly the outfits found in its studio folder
 * under data/outfits, with photos. "Create outfit" makes a case outfit of it (then it
 * can be selected). Rescan re-reads the folder. */
export function OutfitLibraryBrowser({
  caseId,
  language,
  onCreated,
}: {
  caseId: number;
  language: string;
  onCreated?: () => void;
}) {
  const [tick, setTick] = useState(0);
  const { data: library } = useApi(() => api.outfitLibrary(), [tick], { keepPrevious: true });
  const [busy, setBusy] = useState<string | "rescan" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const studio = library?.studios.find((s) => s.studio === language);
  const outfits = studio?.outfits ?? [];
  const lastScan = library?.last_scan ?? null;

  const act = async (key: string, fn: () => Promise<unknown>) => {
    setBusy(key as typeof busy);
    setError(null);
    try {
      await fn();
      setTick((t) => t + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card data-testid={`outfit-library-${language}`}>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle className="text-sm">Outfit library — {langLabel(language)}</CardTitle>
        <Button
          size="sm"
          variant="secondary"
          loading={busy === "rescan"}
          onClick={() => act("rescan", () => api.rescanOutfitLibrary())}
          data-testid="rescan-outfits"
        >
          <RefreshCw className="size-3.5" /> Rescan outfits
        </Button>
      </CardHeader>
      <CardContent className="space-y-3 p-3 text-xs">
        <p className="text-muted-foreground">
          {library
            ? `${outfits.length} outfits · ${library.counts.available} photos`
            : "Loading…"}
          {library && library.counts.missing_on_disk > 0 && (
            <span className="text-amber-600">
              {" "}· {library.counts.missing_on_disk} missing on disk
            </span>
          )}
          {lastScan && (
            <span className="block">
              Last scan {formatDateTime(lastScan.finished_at)} ({lastScan.trigger}):{" "}
              {lastScan.added} new, {lastScan.updated} changed, {lastScan.missing} missing
              {lastScan.problems.length > 0 && `, ${lastScan.problems.length} problems`}
            </span>
          )}
        </p>

        {outfits.length === 0 && (
          <p className="text-muted-foreground">
            No outfits in data/outfits/{language} — drop folders named
            {" "}{language}_&lt;outfit&gt;_&lt;situation&gt;.png there and rescan.
          </p>
        )}

        {outfits.map((outfit) => (
          <LibraryOutfitRow
            key={outfit.ref}
            outfit={outfit}
            busy={busy === outfit.ref}
            onUse={() =>
              act(outfit.ref, async () => {
                await api.createOutfit(caseId, {
                  language,
                  name: outfit.slug.replace(/(?<=[a-z0-9])(?=[A-Z])/g, " "),
                  outfit_ref: outfit.ref,
                });
                onCreated?.();
              })
            }
          />
        ))}

        {lastScan && lastScan.problems.length > 0 && (
          <details className="rounded-md border border-amber-500/40 bg-amber-500/5 p-2">
            <summary className="cursor-pointer text-amber-700 dark:text-amber-300">
              {lastScan.problems.length} problem{lastScan.problems.length === 1 ? "" : "s"} at last scan
            </summary>
            <ul className="mt-1 list-disc pl-4 text-muted-foreground">
              {lastScan.problems.slice(0, 20).map((p, i) => (
                <li key={i}>{p.file ?? p.folder}: {p.issue}</li>
              ))}
            </ul>
          </details>
        )}
        {error && <p className="text-rose-600 dark:text-rose-400">{error}</p>}
      </CardContent>
    </Card>
  );
}

function LibraryOutfitRow({
  outfit,
  busy,
  onUse,
}: {
  outfit: OutfitLibraryOutfit;
  busy: boolean;
  onUse: () => void;
}) {
  const available = outfit.items.filter((i) => i.status === "available");
  return (
    <div className="space-y-1.5 rounded-md border border-border p-2.5" data-testid={`library-outfit-${outfit.slug}`}>
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-medium">{outfit.slug.replace(/(?<=[a-z0-9])(?=[A-Z])/g, " ")}</span>
        <Badge variant="outline">{outfit.ref}</Badge>
        {outfit.in_use && <Badge variant="info">in use</Badge>}
        <span className="text-muted-foreground">{available.length} photos</span>
        <Button size="sm" variant="secondary" loading={busy} onClick={onUse}
          data-testid={`use-library-outfit-${outfit.slug}`}>
          Create outfit
        </Button>
      </div>
      <div className="flex flex-wrap gap-2">
        {outfit.items.map((item) => (
          <div key={item.id} className="w-24 space-y-0.5" title={item.issues.join("; ") || item.situation}>
            {item.status === "available" ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={apiFileUrl(`/api/outfit-library/items/${item.id}/image`)}
                alt={item.situation}
                className="aspect-[3/4] w-full rounded border border-border object-cover"
              />
            ) : (
              <div className="flex aspect-[3/4] w-full items-center justify-center rounded border border-dashed border-amber-500/50 bg-muted text-center text-[9px] text-amber-600">
                missing on disk
              </div>
            )}
            <p className="text-[10px] leading-3 text-muted-foreground">
              {item.situation}
              {item.stance !== "unknown" && ` · ${item.stance}`}
            </p>
            {item.issues.length > 0 && (
              <p className="text-[9px] leading-3 text-amber-600">{item.issues.join("; ")}</p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
