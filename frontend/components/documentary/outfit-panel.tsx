"use client";

import { useCallback, useState } from "react";
import { api, apiFileUrl } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { langLabel } from "@/lib/format";
import type { OutfitPanel, OutfitPanelLanguage, OutfitPanelOutfit } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog } from "@/components/ui/dialog";
import { InlineAlert } from "./shared";

function lookLabel(l: { stance: string; camera: string; framing: string }): string {
  return `${l.stance.toLowerCase()} · ${l.camera} · ${l.framing.toLowerCase().replaceAll("_", " ")}`;
}

/** A small approved-look thumbnail; clickable when it is a pickable fixed picture. */
function LookThumb({ lookId, hasImage, label, picked, onPick }: {
  lookId: number;
  hasImage: boolean;
  label: string;
  picked?: boolean;
  onPick?: () => void;
}) {
  const img = hasImage ? (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={apiFileUrl(`/api/host-looks/${lookId}/image`)} alt={label}
      className="aspect-video w-full rounded object-cover" />
  ) : (
    <div className="flex aspect-video w-full items-center justify-center rounded bg-muted text-[10px] text-muted-foreground">
      no picture
    </div>
  );
  if (!onPick) {
    return <div className="w-28 space-y-0.5">{img}<p className="text-[10px] leading-3 text-muted-foreground">{label}</p></div>;
  }
  return (
    <button type="button" onClick={onPick} aria-pressed={picked}
      className={`w-28 space-y-0.5 rounded-md border-2 p-0.5 text-left transition-colors ${picked ? "border-primary" : "border-transparent hover:border-border"}`}
      data-testid={`fixed-look-${lookId}`}>
      {img}
      <p className="text-[10px] leading-3 text-muted-foreground">{label}</p>
    </button>
  );
}

function LanguageRow({ caseId, row, panel, busy, onAct }: {
  caseId: number;
  row: OutfitPanelLanguage;
  panel: OutfitPanel;
  busy: boolean;
  onAct: (fn: () => Promise<OutfitPanel>) => void;
}) {
  const [sameOutfit, setSameOutfit] = useState(false);
  const [samePicture, setSamePicture] = useState(false);
  const [samePictureLook, setSamePictureLook] = useState<number | null>(null);
  const chosen = row.outfits.find((o) => o.id === row.chosen_outfit_id) ?? null;
  const twins = chosen ? (panel.name_in_languages[chosen.name.trim().toLowerCase()] ?? []) : [];
  const otherTwinLangs = twins.filter((l) => l !== row.language);
  const sameOutfitDisabled = !chosen || otherTwinLangs.length === 0;

  return (
    <section className="space-y-2 rounded-md border border-border p-3" data-testid={`outfit-row-${row.language}`}>
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-medium">{langLabel(row.language)}</h3>
        {row.final_rendered && (
          <Badge variant="warning">a full render exists — a change here needs a new render</Badge>
        )}
      </div>

      {row.outfits.length === 0 ? (
        <p className="text-xs text-muted-foreground">No outfits in this studio yet — create them in the Host tab.</p>
      ) : (
        <ul className="space-y-2" role="radiogroup" aria-label={`${langLabel(row.language)} outfit`}>
          {row.outfits.map((o: OutfitPanelOutfit) => (
            <li key={o.id} className="flex items-start gap-3">
              <label className="flex min-w-0 flex-1 cursor-pointer items-start gap-2">
                <input
                  type="radio"
                  name={`outfit-${row.language}`}
                  className="mt-1"
                  checked={o.id === row.chosen_outfit_id}
                  disabled={busy}
                  onChange={() => onAct(() => api.panelSelectOutfit(caseId, o.id))}
                />
                <span className="min-w-0">
                  <span className="text-sm">{o.name}</span>{" "}
                  <span className="text-[11px] text-muted-foreground">
                    {o.looks.length} approved {o.looks.length === 1 ? "picture" : "pictures"}
                  </span>
                  {o.looks.length === 0 && (
                    <span className="block text-[11px] text-amber-700 dark:text-amber-300">
                      approve or request this look in Host Studio
                    </span>
                  )}
                </span>
              </label>
              <div className="flex flex-wrap gap-1">
                {o.looks.map((lk) => (
                  <LookThumb key={lk.id} lookId={lk.id} hasImage={lk.has_image} label={lookLabel(lk)} />
                ))}
              </div>
            </li>
          ))}
        </ul>
      )}

      {chosen && (
        <div className="space-y-2 border-t border-border pt-2">
          <Checkbox
            label="Same outfit for all languages"
            checked={sameOutfit}
            disabled={busy || sameOutfitDisabled}
            title={sameOutfitDisabled && chosen ? "no other language has an outfit with this name" : undefined}
            onChange={(e) => {
              setSameOutfit(e.target.checked);
              if (!e.target.checked) setSamePicture(false);
              if (e.target.checked && chosen) {
                onAct(() => api.panelSameOutfit(caseId, chosen.id));
              }
            }}
          />
          {sameOutfitDisabled && chosen && (
            <p className="text-[11px] text-muted-foreground">no other language has an outfit with this name</p>
          )}
          {sameOutfit && (
            <div className="space-y-1 pl-6">
              <Checkbox
                label="Same picture for all languages"
                checked={samePicture}
                disabled={busy || chosen.looks.length === 0}
                onChange={(e) => setSamePicture(e.target.checked)}
              />
              {samePicture && (
                <div className="space-y-1">
                  <p className="text-[11px] text-muted-foreground">
                    Choose the picture; every language whose “{chosen.name}” outfit has the same
                    pose, camera and framing gets it pinned.
                  </p>
                  <div className="flex flex-wrap gap-1" role="radiogroup" aria-label="picture for all languages">
                    {chosen.looks.map((lk) => (
                      <LookThumb key={lk.id} lookId={lk.id} hasImage={lk.has_image} label={lookLabel(lk)}
                        picked={samePictureLook === lk.id}
                        onPick={() => {
                          setSamePictureLook(lk.id);
                          onAct(() => api.panelSamePicture(caseId, chosen.id, lk.id));
                        }} />
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
          <Checkbox
            label="One fixed picture"
            checked={row.fixed_look_id != null}
            disabled={busy || chosen.looks.length === 0}
            onChange={(e) => {
              if (e.target.checked) return; // a picture must be picked below
              onAct(() => api.panelFixedLook(caseId, chosen.id, null));
            }}
          />
          {row.fixed_look_id == null && chosen.looks.length > 0 && (
            <div className="space-y-1 pl-6">
              <p className="text-[11px] text-muted-foreground">
                Pick a picture to use it for every appearance of this language (no rotation).
              </p>
              <div className="flex flex-wrap gap-1" role="radiogroup" aria-label={`${langLabel(row.language)} fixed picture`}>
                {chosen.looks.map((lk) => (
                  <LookThumb key={lk.id} lookId={lk.id} hasImage={lk.has_image} label={lookLabel(lk)}
                    picked={false}
                    onPick={() => onAct(() => api.panelFixedLook(caseId, chosen.id, lk.id))} />
                ))}
              </div>
            </div>
          )}
          {row.fixed_look_id != null && (
            <p className="pl-6 text-[11px] text-muted-foreground">
              Picture #{row.fixed_look_id} is used for every appearance. Untick to rotate the
              approved pictures in order again.
            </p>
          )}
        </div>
      )}
      {!chosen && row.outfits.length > 0 && (
        <p className="text-[11px] text-muted-foreground">
          No outfit chosen — drafts still work; the final film asks for a choice.
        </p>
      )}
    </section>
  );
}

/** The Outfits panel (O2): one row per language — choose the outfit and optionally pin
 * one picture. The choice is stored per case and language and may be changed until the
 * final render; nothing here creates pictures or costs money. */
export function OutfitPanelDialog({ caseId, open, onClose }: {
  caseId: number;
  open: boolean;
  onClose: () => void;
}) {
  const [tick, setTick] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<OutfitPanel["result"] | undefined>(undefined);
  // keepPrevious: a refetch after an action must not unmount the rows (their ticked
  // checkboxes are local state that a remount would lose)
  const state = useApi(() => api.outfitPanel(caseId), [caseId, tick], { keepPrevious: true });
  const panel = state.data;

  const act = useCallback(async (fn: () => Promise<OutfitPanel>) => {
    setBusy(true);
    setError(null);
    try {
      const next = await fn();
      setResult(next.result);
      setTick((t) => t + 1);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, []);

  return (
    <Dialog open={open} onClose={onClose} title="Outfits"
      description="The outfit the host wears in each language's film. Saved per language; a change after a render needs a new render."
      className="max-w-3xl">
      {state.loading && !panel && <p className="text-sm text-muted-foreground">Loading…</p>}
      {state.error && <InlineAlert tone="error">{state.error}</InlineAlert>}
      {error && <InlineAlert tone="error">{error}</InlineAlert>}
      {result && (result.skipped.length > 0 || result.reason) && (
        <p className="mb-3 text-xs text-muted-foreground" role="status">
          {result.applied.length > 0 && `applied: ${result.applied.map(langLabel).join(", ")}. `}
          {result.skipped.length > 0 &&
            `skipped: ${result.skipped.map(langLabel).join(", ")}: no outfit with this name. `}
          {result.reason}
        </p>
      )}
      {panel && (
        <div className="max-h-[70vh] space-y-3 overflow-y-auto">
          {panel.languages.map((row) => (
            <LanguageRow key={row.language} caseId={caseId} row={row} panel={panel}
              busy={busy} onAct={act} />
          ))}
        </div>
      )}
    </Dialog>
  );
}
