"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Rocket } from "lucide-react";
import { ApiError, autopilotApi } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { LANGUAGES } from "@/lib/format";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog } from "@/components/ui/dialog";
import { OutfitChoice } from "./outfit-choice";

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
            <OutfitChoice
              key={row.language}
              row={row}
              value={chosenOutfits[row.language] ?? ""}
              photoIds={chosenPhotos[row.language] ?? []}
              disabled={busy}
              onOutfit={(key) => {
                setOutfits((current) => ({ ...current, [row.language]: key }));
                setPhotos((current) => ({ ...current, [row.language]: [] }));
              }}
              onPhotos={(ids) => setPhotos((current) => ({ ...current, [row.language]: ids }))}
              onAdded={outfitOptions.refetch}
              testId={`start-outfit-${row.language}`}
            />
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
