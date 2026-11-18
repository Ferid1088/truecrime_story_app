"use client";

import { apiFileUrl } from "@/lib/api";
import type { NewCovers } from "./cover-triptych";

/** How a vertical Short is cropped where it is shown: the cut is a centre crop of the 9:16 picture (the top and bottom rows go). */
const SHAPES = [
  { key: "reel", name: "Full-screen Reel", ratio: "9 / 16", note: "9:16: the whole picture" },
  { key: "feed", name: "Facebook Feed", ratio: "4 / 5", note: "4:5: the top and bottom 15% are cut" },
  { key: "grid", name: "Profile grid", ratio: "1 / 1", note: "1:1: the top and bottom 22% are cut" },
] as const;

/** The cover in the three places a Short appears. Only covers are shown, never a frame of the film (a frame catches the host mid-word). When covers were made for the three shapes, each place shows
 * its own composition (nothing is cropped from another); otherwise the single cover is cropped. */
export function PlacementPreviews({ coverUrl, covers = {}, label }: { coverUrl: string | null; covers?: NewCovers; label: string }) {
  const own = Object.keys(covers).length > 0;
  if (!coverUrl && !own) return null;
  return (
    <div className="space-y-1 text-xs" data-testid={`placement-${label}`}>
      <p className="font-medium">Where it will be seen <span className="font-normal text-muted-foreground">· {own ? "the new covers, each made for its own shape (proposals)" : "preview only: a simulated centre crop of the cover, not how Facebook really renders it"}</span></p>
      <div className="flex flex-wrap items-end gap-4">
        {SHAPES.map((shape) => (
          <figure key={shape.key} className="space-y-1" data-testid={`placement-${label}-${shape.key}`}>
            <div className="flex items-end gap-2">
              {covers[shape.key] ? (
                <div className="w-24 overflow-hidden rounded border border-border bg-black" style={{ aspectRatio: shape.ratio }} title={`New cover for this shape, version ${covers[shape.key]!.version} (${covers[shape.key]!.status})`} data-testid={`placement-${label}-${shape.key}-own`}>
                  {/* eslint-disable-next-line @next/next/no-img-element -- the cover is served by our own API */}
                  <img src={covers[shape.key]!.url} alt={`New cover made for ${shape.name}`} className="h-full w-full object-cover object-center" />
                </div>
              ) : coverUrl && (
                <div className="w-24 overflow-hidden rounded border border-border bg-black" style={{ aspectRatio: shape.ratio }} title={`Approved cover, ${shape.note}`}>
                  {/* eslint-disable-next-line @next/next/no-img-element -- the cover is served by our own API */}
                  <img src={apiFileUrl(coverUrl)} alt={`Approved cover as ${shape.name}`} className="h-full w-full object-cover object-center" />
                </div>
              )}
            </div>
            <figcaption className="max-w-52 text-muted-foreground"><span className="font-medium text-foreground">{shape.name}</span><br />{covers[shape.key] ? `Its own composition, v${covers[shape.key]!.version} (${covers[shape.key]!.status})` : shape.note}</figcaption>
          </figure>
        ))}
      </div>
    </div>
  );
}
