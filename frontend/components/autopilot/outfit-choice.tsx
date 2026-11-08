"use client";

import { apiFileUrl } from "@/lib/api";
import type { AutopilotOutfitLanguage } from "@/lib/api";
import { LANGUAGES } from "@/lib/format";
import { Checkbox } from "@/components/ui/checkbox";
import { AddOutfitPhoto } from "./add-outfit-photo";

/** The host outfit of one language and the library photos used in its film: shared by the Start dialog and the case page. */
export function OutfitChoice({
  row,
  value,
  photoIds,
  disabled,
  onOutfit,
  onPhotos,
  onAdded,
  testId,
}: {
  row: AutopilotOutfitLanguage;
  value: string;
  photoIds: number[];
  disabled: boolean;
  onOutfit: (key: string) => void;
  onPhotos: (ids: number[]) => void;
  onAdded: () => void;
  testId: string;
}) {
  const option = row.options.find((candidate) => candidate.key === value);
  const available = (option?.photos ?? []).filter((photo) => photo.status === "available");
  return (
    <div className="block text-sm">
      <span className="mb-1 block font-medium">{LANGUAGES[row.language] ?? row.language}</span>
      <select
        className="w-full rounded-md border border-border bg-background px-2.5 py-2 text-sm"
        value={value}
        disabled={disabled || row.options.length === 0}
        onChange={(event) => onOutfit(event.target.value)}
        data-testid={testId}
      >
        <option value="">Choose an outfit…</option>
        {row.options.map((candidate) => (
          <option key={candidate.key} value={candidate.key}>
            {candidate.name}{candidate.source === "case" ? ` · ${candidate.status}` : " · library"}
          </option>
        ))}
      </select>
      {row.options.length === 0 && (
        <span className="mt-1 block text-xs text-rose-600">No outfit is available for this language.</span>
      )}
      <AddOutfitPhoto language={row.language} outfitNames={row.options.map((o) => o.name)} onAdded={onAdded} />
      {!available.length && option && (
        <span className="mt-1 block text-xs text-muted-foreground">No library photos available for this outfit.</span>
      )}
      {available.length > 0 && (
        <div className="mt-2 rounded-md border border-border p-2">
          <p className="mb-2 text-xs font-medium">Photos to use in this film</p>
          <div className="flex flex-wrap gap-2">
            {available.map((photo) => {
              const selected = photoIds.includes(photo.id);
              return (
                <div key={photo.id} className={`w-20 rounded border p-1 ${selected ? "border-primary" : "border-border"}`}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={apiFileUrl(`/api/outfit-library/items/${photo.id}/image`)} alt={photo.situation} className="aspect-[3/4] w-full rounded object-cover" />
                  <Checkbox
                    label={photo.situation}
                    checked={selected}
                    disabled={disabled}
                    onChange={(event) => {
                      const ids = new Set(photoIds);
                      if (event.target.checked) ids.add(photo.id); else ids.delete(photo.id);
                      onPhotos([...ids]);
                    }}
                    className="mt-1 size-3"
                  />
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
