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
  const est = estimate.data;

  function toggle(lang: string, on: boolean) {
    setLangs((prev) => FILM_LANGUAGES.filter((l) => (l === lang ? on : prev.includes(l))));
  }

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const run = await autopilotApi.start(candidateId, langs);
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

        <div className="rounded-md border border-border bg-muted/40 px-3 py-2 text-sm" data-testid="start-estimate">
          {est ? (
            <>
              <p>
                Estimated cost: <span className="font-semibold">{eur(est.estimated_eur)}</span>{" "}
                <span className="text-muted-foreground">
                  (range {eur(est.range_eur.low)}–{eur(est.range_eur.high)})
                </span>
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
          <Button size="sm" onClick={start} loading={busy} disabled={!est} data-testid="start-confirm">
            <Rocket className="size-3.5" />
            Start
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
