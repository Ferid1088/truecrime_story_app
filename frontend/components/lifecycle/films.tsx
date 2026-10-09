"use client";

import { useId, useState } from "react";
import { Archive, ExternalLink, PlayCircle, Send } from "lucide-react";
import { api, apiFileUrl } from "@/lib/api";
import { formatDate, formatTimecode, langLabel } from "@/lib/format";
import type { Film } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ResolutionBadge } from "@/components/status-badge";
import { Collapsible, FieldLabel, FilmStateBadge, ProductionTypeBadge, filmTitle, openingLabel } from "./shared";

/**
 * The films of one case, with Publish and Archive. Publishing freezes the
 * case status at publication and recomputes the YouTube title, so it is
 * offered once — for a rendered film, never again for a published one.
 */
export function FilmList({ films, onChanged }: { films: Film[]; onChanged: () => void }) {
  const [publishing, setPublishing] = useState<Film | null>(null);
  const [archiving, setArchiving] = useState<Film | null>(null);
  const byId = new Map(films.map((f) => [f.id, f]));

  return (
    <>
      <ul className="divide-y divide-border">
        {films.map((f) => (
          <FilmItem
            key={f.id}
            film={f}
            original={f.original_video_id != null ? byId.get(f.original_video_id) ?? null : null}
            followUps={films.filter((x) => x.original_video_id === f.id)}
            onPublish={() => setPublishing(f)}
            onArchive={() => setArchiving(f)}
          />
        ))}
      </ul>
      <PublishDialog
        film={publishing}
        onClose={() => setPublishing(null)}
        onDone={() => {
          setPublishing(null);
          onChanged();
        }}
      />
      <ArchiveDialog
        film={archiving}
        onClose={() => setArchiving(null)}
        onDone={() => {
          setArchiving(null);
          onChanged();
        }}
      />
    </>
  );
}

function FilmItem({
  film: f,
  original,
  followUps,
  onPublish,
  onArchive,
}: {
  film: Film;
  original: Film | null;
  followUps: Film[];
  onPublish: () => void;
  onArchive: () => void;
}) {
  return (
    <li className="space-y-3 px-4 py-3">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="font-medium leading-snug">{filmTitle(f)}</p>
          {f.youtube_title && f.title && f.title !== f.youtube_title && (
            <p className="mt-0.5 text-xs text-muted-foreground">Film title: {f.title}</p>
          )}
          <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
            <FilmStateBadge state={f.state} />
            <ProductionTypeBadge type={f.production_type} />
            <Badge variant="outline">{langLabel(f.language)}</Badge>
            <Badge variant="outline">{f.mode}</Badge>
            {f.episode_number != null && <span>Episode {f.episode_number}</span>}
            <span>· film #{f.id}</span>
          </div>
        </div>
        <div className="flex shrink-0 flex-wrap gap-2">
          {f.state === "rendered" && (
            <Button size="sm" onClick={onPublish}>
              <Send className="size-3.5" /> Publish
            </Button>
          )}
          {f.state !== "archived" && (
            <Button size="sm" variant="outline" onClick={onArchive}>
              <Archive className="size-3.5" /> Archive
            </Button>
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 text-xs sm:grid-cols-3 lg:grid-cols-6">
        <div>
          <FieldLabel>At production</FieldLabel>
          <ResolutionBadge status={f.status_at_production} />
        </div>
        <div>
          <FieldLabel>At publication</FieldLabel>
          {f.status_at_publication ? (
            <ResolutionBadge status={f.status_at_publication} />
          ) : (
            <span className="text-muted-foreground">not published</span>
          )}
        </div>
        <div>
          <FieldLabel>Opening</FieldLabel>
          <span>{openingLabel(f.opening_strategy)}</span>
        </div>
        <div>
          <FieldLabel>Length</FieldLabel>
          <span className="tabular-nums">{formatTimecode(f.duration_seconds)}</span>
        </div>
        <div>
          <FieldLabel>Published</FieldLabel>
          <span>{formatDate(f.published_at)}</span>
        </div>
        <div>
          <FieldLabel>Rendered</FieldLabel>
          <span>{formatDate(f.created_at)}</span>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
        {f.video_url && (
          <a
            href={apiFileUrl(f.video_url)}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-primary hover:underline"
          >
            <PlayCircle className="size-3.5" aria-hidden /> Watch render
          </a>
        )}
        {f.youtube_url && (
          <a
            href={f.youtube_url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-primary hover:underline"
          >
            <ExternalLink className="size-3.5" aria-hidden /> YouTube
          </a>
        )}
        {f.production_type === "follow_up" && (
          <span className="text-muted-foreground">
            Update to{" "}
            <span className="text-foreground">
              {original ? filmTitle(original) : f.original_video_id != null ? `film #${f.original_video_id}` : "an earlier video"}
            </span>
            {original?.episode_number != null && ` (Episode ${original.episode_number})`}
          </span>
        )}
        {followUps.length > 0 && (
          <span className="text-muted-foreground">
            Follow-up{followUps.length === 1 ? "" : "s"}:{" "}
            <span className="text-foreground">{followUps.map(filmTitle).join(" · ")}</span>
          </span>
        )}
      </div>

      {(f.youtube_description || f.youtube_tags.length > 0) && (
        <Collapsible title="YouTube description & tags">
          {f.youtube_description && (
            <p className="whitespace-pre-line text-xs leading-5 text-foreground/90">{f.youtube_description}</p>
          )}
          {f.youtube_tags.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1">
              {f.youtube_tags.map((t) => (
                <Badge key={t} variant="outline">
                  {t}
                </Badge>
              ))}
            </div>
          )}
        </Collapsible>
      )}
    </li>
  );
}

function PublishDialog({ film, onClose, onDone }: { film: Film | null; onClose: () => void; onDone: () => void }) {
  return (
    <Dialog
      open={film != null}
      onClose={onClose}
      title="Publish film"
      description="Marks the film published now: the case status at publication is frozen and the YouTube title and description are recomputed with it."
    >
      {film && <PublishForm key={film.id} film={film} onCancel={onClose} onDone={onDone} />}
    </Dialog>
  );
}

function PublishForm({ film, onCancel, onDone }: { film: Film; onCancel: () => void; onDone: () => void }) {
  const [episode, setEpisode] = useState(film.episode_number != null ? String(film.episode_number) : "");
  const [url, setUrl] = useState(film.youtube_url ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ids = useId();

  const episodeNumber = episode.trim() ? Number(episode) : null;
  const badEpisode = episodeNumber != null && (!Number.isInteger(episodeNumber) || episodeNumber < 1);
  const badUrl = url.trim() !== "" && !/^https?:\/\//i.test(url.trim());

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      await api.publishFilm(film.id, {
        episode_number: episodeNumber,
        youtube_url: url.trim() || null,
      });
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <div className="space-y-3">
      <p className="text-sm font-medium">{filmTitle(film)}</p>
      <div>
        <label htmlFor={`${ids}-episode`} className="mb-1 block text-xs font-medium text-muted-foreground">
          Episode number
        </label>
        <Input
          id={`${ids}-episode`}
          type="number"
          min={1}
          step={1}
          inputMode="numeric"
          value={episode}
          onChange={(e) => setEpisode(e.target.value)}
          placeholder="Next free number of the language"
          aria-invalid={badEpisode || undefined}
        />
        {badEpisode && <p className="mt-1 text-[11px] text-rose-500">A whole number from 1.</p>}
      </div>
      <div>
        <label htmlFor={`${ids}-url`} className="mb-1 block text-xs font-medium text-muted-foreground">
          YouTube URL
        </label>
        <Input
          id={`${ids}-url`}
          type="url"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://www.youtube.com/watch?v=…"
          aria-invalid={badUrl || undefined}
        />
        {badUrl && <p className="mt-1 text-[11px] text-rose-500">Starts with http:// or https://.</p>}
      </div>
      {error && <p className="text-xs text-rose-500">{error}</p>}
      <div className="flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={onCancel}>
          Cancel
        </Button>
        <Button size="sm" onClick={submit} loading={busy} disabled={badEpisode || badUrl}>
          <Send className="size-3.5" /> Publish
        </Button>
      </div>
    </div>
  );
}

function ArchiveDialog({ film, onClose, onDone }: { film: Film | null; onClose: () => void; onDone: () => void }) {
  return (
    <Dialog
      open={film != null}
      onClose={onClose}
      title="Archive film?"
      description="The film stays in the channel's memory (archive, duplicate checks, follow-ups) but leaves the active list."
    >
      {film && <ArchiveForm key={film.id} film={film} onCancel={onClose} onDone={onDone} />}
    </Dialog>
  );
}

function ArchiveForm({ film, onCancel, onDone }: { film: Film; onCancel: () => void; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function archive() {
    setBusy(true);
    setError(null);
    try {
      await api.archiveFilm(film.id);
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  }

  return (
    <>
      <p className="mb-3 text-sm font-medium">{filmTitle(film)}</p>
      {error && <p className="mb-3 text-xs text-rose-500">{error}</p>}
      <div className="flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={onCancel}>
          Cancel
        </Button>
        <Button variant="destructive" size="sm" onClick={archive} loading={busy}>
          Archive film
        </Button>
      </div>
    </>
  );
}
