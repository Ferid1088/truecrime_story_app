"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Rocket } from "lucide-react";
import { ApiError, apiFileUrl, autopilotApi } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { LANGUAGES } from "@/lib/format";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog } from "@/components/ui/dialog";
import { AddOutfitPhoto } from "./add-outfit-photo";

const FILM_LANGUAGES = ["en", "de", "fa", "ar"];
const eur = (n: number) => `€${n.toFixed(2)}`;

/** The Start button's confirmation: the languages, the printed cost estimate and the cap. */
export function StartDialog({
  candidateId,
  title,
  open,
  onClose,
}: {
  candidateId: number;
  title: string;
  open: boolean;
  onClose: () => void;
}) {
  const router = useRouter();
  const [langs, setLangs] = useState<string[]>(FILM_LANGUAGES);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const estimate = useApi(
    () => (open ? autopilotApi.estimate(candidateId, langs) : Promise.resolve(null)),
    [open, candidateId, langs.join(",")],
    { keepPrevious: true },
  );
  const outfitOptions = useApi(
    () => (open ? autopilotApi.outfitOptions(candidateId, langs) : Promise.resolve(null)),
    [open, candidateId, langs.join(",")],
    { keepPrevious: true },
  );
  const [outfits, setOutfits] = useState<Record<string, string>>({});
  const [photos, setPhotos] = useState<Record<string, number[]>>({});
  const est = estimate.data;

  // Existing selections are the initial values; only explicit user changes
  // need to live in component state.
  const chosenOutfits = Object.fromEntries(
    (outfitOptions.data?.languages ?? []).map((row) => [row.language, outfits[row.language] || row.selected || ""]),
  );
  const chosenPhotos = Object.fromEntries(
    (outfitOptions.data?.languages ?? []).map((row) => {
      const option = row.options.find((candidate) => candidate.key === chosenOutfits[row.language]);
      const valid = new Set((option?.photos ?? []).filter((photo) => photo.status === "available").map((photo) => photo.id));
      const current = photos[row.language];
      const selected = current ? current.filter((id) => valid.has(id))
        : (option?.photos ?? []).filter((photo) => photo.selected).map((photo) => photo.id);
      return [row.language, selected];
    }),
  );

  function toggle(lang: string, on: boolean) {
    setLangs((prev) => FILM_LANGUAGES.filter((l) => (l === lang ? on : prev.includes(l))));
  }

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const run = await autopilotApi.start(candidateId, langs, chosenOutfits, chosenPhotos);
      router.push(`/autopilot/${run.id}`);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Start the autopilot"
      description={`${title} — research, texts, films, shorts and a dry-run publish queue, all automatic.`}
    >
      <div className="space-y-4">
        <fieldset>
          <legend className="mb-1.5 text-xs font-medium text-muted-foreground">Languages</legend>
          <div className="flex flex-wrap gap-x-4 gap-y-1.5">
            {FILM_LANGUAGES.map((lang) => (
              <Checkbox
                key={lang}
                label={LANGUAGES[lang] ?? lang}
                checked={langs.includes(lang)}
                disabled={lang === "en"}
                onChange={(e) => toggle(lang, e.target.checked)}
                data-testid={`start-language-${lang}`}
              />
            ))}
          </div>
          <p className="mt-1 text-xs text-muted-foreground">English is always made: every other language is told from it.</p>
        </fieldset>

        <fieldset className="space-y-2">
          <legend className="text-xs font-medium text-muted-foreground">Host outfit per language</legend>
          <p className="text-xs text-muted-foreground">
            Choose this before starting. The avatar stage will use only approved looks belonging to this outfit. Your
            choice is kept for the next films until you change it.
          </p>
          {outfitOptions.loading && <p className="text-xs text-muted-foreground">Loading outfit choices…</p>}
          {outfitOptions.error && <p className="text-xs text-rose-600">{outfitOptions.error}</p>}
          {outfitOptions.data?.languages.map((row) => (
            <div key={row.language} className="block text-sm">
              <span className="mb-1 block font-medium">{LANGUAGES[row.language] ?? row.language}</span>
              <select
                className="w-full rounded-md border border-border bg-background px-2.5 py-2 text-sm"
                value={chosenOutfits[row.language] ?? ""}
                disabled={busy || row.options.length === 0}
                onChange={(event) => {
                  setOutfits((current) => ({ ...current, [row.language]: event.target.value }));
                  setPhotos((current) => ({ ...current, [row.language]: [] }));
                }}
                data-testid={`start-outfit-${row.language}`}
              >
                <option value="">Choose an outfit…</option>
                {row.options.map((option) => (
                  <option key={option.key} value={option.key}>
                    {option.name}{option.source === "case" ? ` · ${option.status}` : " · library"}
                  </option>
                ))}
              </select>
              {row.options.length === 0 && (
                <span className="mt-1 block text-xs text-rose-600">No outfit is available for this language.</span>
              )}
              <AddOutfitPhoto language={row.language} outfitNames={row.options.map((o) => o.name)}
                onAdded={outfitOptions.refetch} />
              {(() => {
                const option = row.options.find((candidate) => candidate.key === chosenOutfits[row.language]);
                const available = (option?.photos ?? []).filter((photo) => photo.status === "available");
                if (!available.length) {
                  return option ? <span className="mt-1 block text-xs text-muted-foreground">No library photos available for this outfit.</span> : null;
                }
                return (
                  <div className="mt-2 rounded-md border border-border p-2">
                    <p className="mb-2 text-xs font-medium">Photos to use in this film</p>
                    <div className="flex flex-wrap gap-2">
                      {available.map((photo) => {
                        const selected = chosenPhotos[row.language]?.includes(photo.id) ?? false;
                        return (
                          <div key={photo.id} className={`w-20 rounded border p-1 ${selected ? "border-primary" : "border-border"}`}>
                            {/* eslint-disable-next-line @next/next/no-img-element */}
                            <img src={apiFileUrl(`/api/outfit-library/items/${photo.id}/image`)} alt={photo.situation} className="aspect-[3/4] w-full rounded object-cover" />
                            <Checkbox
                              label={photo.situation}
                              checked={selected}
                              disabled={busy}
                              onChange={(event) => setPhotos((current) => {
                                const ids = new Set(current[row.language] ?? chosenPhotos[row.language] ?? []);
                                if (event.target.checked) ids.add(photo.id); else ids.delete(photo.id);
                                return { ...current, [row.language]: [...ids] };
                              })}
                              className="mt-1 size-3"
                            />
                          </div>
                        );
                      })}
                    </div>
                  </div>
                );
              })()}
            </div>
          ))}
        </fieldset>

        <div className="rounded-md border border-border bg-muted/40 px-3 py-2 text-sm" data-testid="start-estimate">
          {est ? (
            <>
              <p>
                Estimated cost: <span className="font-semibold">{eur(est.estimated_eur)}</span>{" "}
                <span className="text-muted-foreground">
                  (range {eur(est.range_eur.low)}–{eur(est.range_eur.high)})
                </span>
              </p>
              <p className="text-xs text-muted-foreground" data-testid="start-research-estimate">
                Of this, picture research (image searches, vision checks, second checks): {eur(est.visual_research_eur)}.
              </p>
              <p className="text-xs text-muted-foreground">
                The only limit is the actual spend: the run stops before a paid call once {eur(est.cap_eur)} is spent.
                Publishing is a dry run: nothing is sent to any platform.
              </p>
            </>
          ) : (
            <p className="text-muted-foreground">Estimating…</p>
          )}
        </div>
        {estimate.error && <p className="text-xs text-rose-600">{estimate.error}</p>}
        {error && <p role="alert" className="text-xs text-rose-600">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button
            size="sm"
            onClick={start}
            loading={busy}
            disabled={!est || outfitOptions.loading || !outfitOptions.data
              || langs.some((lang) => !chosenOutfits[lang]
                || ((outfitOptions.data?.languages.find((row) => row.language === lang)?.options
                  .find((option) => option.key === chosenOutfits[lang])?.photos ?? [])
                  .some((photo) => photo.status === "available") && !chosenPhotos[lang]?.length))}
            data-testid="start-confirm"
          >
            <Rocket className="size-3.5" />
            Start
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
