"use client";

import { useId } from "react";
import { formatTimecode, humanize, langLabel } from "@/lib/format";
import type {
  DocumentaryMode,
  DocumentaryRunOptions,
  DocumentarySettings,
  FilmMinutesRange,
  RenderProfile,
} from "@/lib/types";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

export const PILOT_MIN_SECONDS = 11;
export const PILOT_MAX_SECONDS = 1800;
/** Default length of a master story written from zero. */
export const DEFAULT_TARGET_MINUTES = 50;

/** Form state of the options a single run and a batch share. */
export interface RunOptionsState {
  languages: string[];
  mode: DocumentaryMode;
  pilotSeconds: string;
  profile: RenderProfile;
  refreshVisuals: boolean;
}

export function initialRunOptions(settings: DocumentarySettings): RunOptionsState {
  return {
    languages: settings.languages,
    mode: "pilot",
    pilotSeconds: String(settings.pilot_seconds),
    profile: "preview",
    refreshVisuals: false,
  };
}

function pilotValue(options: RunOptionsState): number | null {
  const value = Number(options.pilotSeconds);
  return options.pilotSeconds.trim() !== "" &&
    Number.isFinite(value) &&
    value >= PILOT_MIN_SECONDS &&
    value <= PILOT_MAX_SECONDS
    ? value
    : null;
}

export function runOptionsPayload(options: RunOptionsState): DocumentaryRunOptions {
  return {
    languages: options.languages,
    mode: options.mode,
    pilot_seconds: options.mode === "pilot" ? pilotValue(options) : null,
    render_profile: options.profile,
    refresh_visuals: options.refreshVisuals,
  };
}

/** Why the options cannot start a run (empty when they can). */
export function runOptionsBlockers(options: RunOptionsState, settings: DocumentarySettings): string[] {
  const blockers: string[] = [];
  if (options.languages.length === 0) blockers.push("Select at least one language.");
  const missingVoice = options.languages.filter((l) => !settings.voices[l]?.voice_id);
  if (missingVoice.length)
    blockers.push(`No narrator voice is configured for ${missingVoice.map(langLabel).join(", ")}.`);
  if (options.mode === "pilot" && pilotValue(options) == null)
    blockers.push(`Pilot length must be ${PILOT_MIN_SECONDS}–${PILOT_MAX_SECONDS} seconds.`);
  return blockers;
}

/** Target film length in minutes, or null when outside the allowed range. */
export function parseTargetMinutes(value: string, range: FilmMinutesRange): number | null {
  const minutes = Number(value);
  return value.trim() !== "" && Number.isFinite(minutes) && minutes >= range.min && minutes <= range.max
    ? minutes
    : null;
}

export function Choice({
  name,
  checked,
  onSelect,
  title,
  description,
}: {
  name: string;
  checked: boolean;
  onSelect: () => void;
  title: string;
  description: string;
}) {
  return (
    <label
      className={cn(
        "flex cursor-pointer gap-2.5 rounded-md border px-3 py-2 transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring/40",
        checked ? "border-primary bg-accent/40" : "border-border hover:bg-muted/50",
      )}
    >
      <input
        type="radio"
        name={name}
        checked={checked}
        onChange={onSelect}
        className="mt-0.5 size-3.5 shrink-0 cursor-pointer accent-primary outline-none"
      />
      <span>
        <span className="block text-sm font-medium">{title}</span>
        <span className="block text-[11px] leading-4 text-muted-foreground">{description}</span>
      </span>
    </label>
  );
}

export function TargetMinutesInput({
  id,
  value,
  onChange,
  range,
  label = "Target length (minutes)",
}: {
  id: string;
  value: string;
  onChange: (value: string) => void;
  range: FilmMinutesRange;
  label?: string;
}) {
  const valid = parseTargetMinutes(value, range) != null;
  return (
    <div>
      <label htmlFor={id} className="mb-1 block text-xs font-medium text-muted-foreground">
        {label}
      </label>
      <Input
        id={id}
        type="number"
        inputMode="numeric"
        min={range.min}
        max={range.max}
        step={5}
        value={value}
        aria-invalid={!valid}
        aria-describedby={`${id}-hint`}
        onChange={(e) => onChange(e.target.value)}
      />
      <p id={`${id}-hint`} className="mt-1 text-[11px] text-muted-foreground">
        {range.min}–{range.max} min
      </p>
    </div>
  );
}

/** Languages, mode (+ pilot length), render profile and picture refresh. */
export function RunOptionsFields({
  settings,
  value,
  onChange,
}: {
  settings: DocumentarySettings;
  value: RunOptionsState;
  onChange: (next: RunOptionsState) => void;
}) {
  const ids = useId();
  const range = settings.film_minutes;
  const pilot = pilotValue(value);
  const set = (patch: Partial<RunOptionsState>) => onChange({ ...value, ...patch });
  const profileRights = (p: RenderProfile) => (settings.rights_profiles[p] ?? []).map(humanize).join(", ");

  return (
    <>
      <fieldset>
        <legend className="mb-1.5 text-xs font-medium text-muted-foreground">Languages</legend>
        <div className="grid grid-cols-2 gap-x-3 gap-y-2">
          {settings.languages.map((l) => (
            <div key={l}>
              <Checkbox
                label={langLabel(l)}
                checked={value.languages.includes(l)}
                onChange={(e) =>
                  set({
                    languages: settings.languages.filter((x) =>
                      x === l ? e.target.checked : value.languages.includes(x),
                    ),
                  })
                }
              />
              {!settings.voices[l]?.voice_id && (
                <p className="mt-0.5 pl-5.5 text-[10px] text-amber-600 dark:text-amber-400">no narrator voice</p>
              )}
              {settings.speech_script[l] === "finglish" && (
                <p className="mt-0.5 pl-5.5 text-[10px] text-muted-foreground">narrated in Finglish</p>
              )}
            </div>
          ))}
        </div>
      </fieldset>

      <fieldset className="space-y-2">
        <legend className="mb-1.5 text-xs font-medium text-muted-foreground">Mode</legend>
        <Choice
          name={`${ids}-mode`}
          checked={value.mode === "pilot"}
          onSelect={() => set({ mode: "pilot" })}
          title="Pilot"
          description={`The opening ${pilot != null ? formatTimecode(pilot) : "minutes"} of the full film in every selected language.`}
        />
        <Choice
          name={`${ids}-mode`}
          checked={value.mode === "full"}
          onSelect={() => set({ mode: "full" })}
          title="Full film"
          description={`The complete ${range.min}–${range.max} minute film. Refused when the story is too short or too long.`}
        />
        {value.mode === "pilot" && (
          <div className="pt-1">
            <label htmlFor={`${ids}-pilot`} className="mb-1 block text-xs font-medium text-muted-foreground">
              Pilot length (seconds)
            </label>
            <Input
              id={`${ids}-pilot`}
              type="number"
              inputMode="numeric"
              min={PILOT_MIN_SECONDS}
              max={PILOT_MAX_SECONDS}
              step={10}
              value={value.pilotSeconds}
              aria-invalid={pilot == null}
              aria-describedby={`${ids}-pilot-hint`}
              onChange={(e) => set({ pilotSeconds: e.target.value })}
            />
            <p id={`${ids}-pilot-hint`} className="mt-1 text-[11px] text-muted-foreground">
              Default {formatTimecode(settings.pilot_seconds)} · allowed {PILOT_MIN_SECONDS}–{PILOT_MAX_SECONDS} s
            </p>
          </div>
        )}
      </fieldset>

      <fieldset className="space-y-2">
        <legend className="mb-1.5 text-xs font-medium text-muted-foreground">Render profile</legend>
        <Choice
          name={`${ids}-profile`}
          checked={value.profile === "preview"}
          onSelect={() => set({ profile: "preview" })}
          title="Preview"
          description={`Internal review copy. Pictures with rights: ${profileRights("preview")}.`}
        />
        <Choice
          name={`${ids}-profile`}
          checked={value.profile === "publish"}
          onSelect={() => set({ profile: "publish" })}
          title="Publish"
          description={`Only pictures cleared for publication: ${profileRights("publish")}.`}
        />
      </fieldset>

      <div className="space-y-1">
        <Checkbox
          label="Search and plan pictures again"
          checked={value.refreshVisuals}
          onChange={(e) => set({ refreshVisuals: e.target.checked })}
          aria-describedby={`${ids}-refresh-hint`}
        />
        <p id={`${ids}-refresh-hint`} className="text-[11px] leading-4 text-muted-foreground">
          Off: reuse the existing visual plan (no new research or picture checks). On: new research,
          verification and shot direction — use after reviewing the Visual Library.
        </p>
      </div>
    </>
  );
}
