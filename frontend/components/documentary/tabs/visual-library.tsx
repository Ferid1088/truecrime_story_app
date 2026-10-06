"use client";

import { useEffect, useId, useState } from "react";
import Image from "next/image";
import { ExternalLink, ImageOff, ImageUp, Search } from "lucide-react";
import { api, apiFileUrl } from "@/lib/api";
import { useApi } from "@/lib/hooks";
import { formatDate, humanize } from "@/lib/format";
import type { AssetRole, VerificationStatus, VisualAsset } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState, ErrorState } from "@/components/state";
import {
  type DocumentaryTabProps,
  InlineAlert,
  RIGHTS_OPTIONS,
  ROLE_OPTIONS,
  RightsBadge,
  RoleBadge,
  VERIFICATION_OPTIONS,
  VerificationBadge,
} from "../shared";

const GROUPS: { key: string; label: string; subjects: string[] }[] = [
  { key: "people", label: "People", subjects: ["person"] },
  { key: "places", label: "Places", subjects: ["place", "building", "landscape", "geography"] },
  { key: "organizations", label: "Organizations", subjects: ["organization"] },
  { key: "objects", label: "Objects", subjects: ["object", "vehicle"] },
  { key: "documents", label: "Documents", subjects: ["document"] },
  { key: "maps", label: "Maps", subjects: ["map"] },
  { key: "events", label: "Events", subjects: ["event"] },
  { key: "other", label: "Other", subjects: [] },
];

function groupOf(a: VisualAsset): string {
  if (a.type === "map") return "maps";
  if (a.type === "document") return "documents";
  const subject = (a.subject_type ?? "").toLowerCase();
  return GROUPS.find((g) => g.subjects.includes(subject))?.key ?? "other";
}

function altText(a: VisualAsset): string {
  return a.description || a.caption || a.title || `Visual ${a.asset_id}`;
}

export function VisualLibraryTab({ caseId, overview, refreshKey }: DocumentaryTabProps) {
  const ids = useId();
  const [role, setRole] = useState("");
  const [verification, setVerification] = useState("");
  const [rights, setRights] = useState("");
  const [q, setQ] = useState("");
  const [debouncedQ, setDebouncedQ] = useState("");
  const [tick, setTick] = useState(0);
  const [detail, setDetail] = useState<VisualAsset | null>(null);
  const [uploading, setUploading] = useState(false);

  useEffect(() => {
    const t = setTimeout(() => setDebouncedQ(q.trim()), 250);
    return () => clearTimeout(t);
  }, [q]);

  const { data, error, loading, refetch } = useApi(
    () => api.listVisuals(caseId, { role, verification, rights, q: debouncedQ }),
    [caseId, role, verification, rights, debouncedQ, refreshKey, tick],
    { keepPrevious: true },
  );

  const filtered = !!(role || verification || rights || debouncedQ);
  const counts = Object.entries(overview.visual_counts);
  const groups = GROUPS.map((g) => ({ ...g, items: (data ?? []).filter((a) => groupOf(a) === g.key) })).filter(
    (g) => g.items.length > 0,
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="max-w-2xl text-xs leading-5 text-muted-foreground">
            Real photos, documents and maps found for the film, each checked by a vision model. Found
            does not mean usable: rights decide what a preview or a published render may show.
          </p>
          {counts.length > 0 && (
            <p className="mt-1 text-xs text-muted-foreground">
              {counts.map(([status, n]) => `${n} ${humanize(status)}`).join(" · ")}
            </p>
          )}
        </div>
        <Button size="sm" onClick={() => setUploading(true)}>
          <ImageUp className="size-3.5" /> Upload image
        </Button>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <div className="relative w-full sm:w-64">
          <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search title, caption, entities…"
            aria-label="Search visuals"
            className="pl-8"
          />
        </div>
        <FilterSelect id={`${ids}-role`} label="Role" value={role} onChange={setRole} options={ROLE_OPTIONS} />
        <FilterSelect
          id={`${ids}-verification`}
          label="Verification"
          value={verification}
          onChange={setVerification}
          options={VERIFICATION_OPTIONS}
        />
        <FilterSelect id={`${ids}-rights`} label="Rights" value={rights} onChange={setRights} options={RIGHTS_OPTIONS} />
      </div>

      {error && data && <InlineAlert tone="error">{error}</InlineAlert>}

      {!data && loading && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <Skeleton key={i} className="aspect-[4/5]" />
          ))}
        </div>
      )}
      {!data && error && <ErrorState message={error} onRetry={refetch} />}
      {data && data.length === 0 && (
        <EmptyState
          title={filtered ? "No visuals match" : "No visuals yet"}
          description={
            filtered
              ? "Try clearing the search or a filter."
              : "Visual research runs during a pilot. You can also upload your own images."
          }
        />
      )}

      {groups.map((g) => (
        <section key={g.key} aria-labelledby={`${ids}-${g.key}`}>
          <h3 id={`${ids}-${g.key}`} className="mb-2 text-sm font-medium">
            {g.label} <span className="font-normal text-muted-foreground">({g.items.length})</span>
          </h3>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
            {g.items.map((a) => (
              <VisualCard key={a.id} asset={a} onOpen={() => setDetail(a)} />
            ))}
          </div>
        </section>
      ))}

      {detail && (
        <VisualDetailDialog
          key={detail.id}
          asset={detail}
          onClose={() => setDetail(null)}
          onSaved={(updated) => {
            setDetail(updated);
            setTick((t) => t + 1);
          }}
        />
      )}
      <UploadDialog
        caseId={caseId}
        open={uploading}
        onClose={() => setUploading(false)}
        onUploaded={(asset) => {
          setUploading(false);
          setTick((t) => t + 1);
          setDetail(asset);
        }}
      />
    </div>
  );
}

function FilterSelect({
  id,
  label,
  value,
  onChange,
  options,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: readonly string[];
}) {
  return (
    <div className="w-40">
      <label htmlFor={id} className="mb-1 block text-xs font-medium text-muted-foreground">
        {label}
      </label>
      <Select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">All</option>
        {options.map((o) => (
          <option key={o} value={o}>
            {humanize(o)}
          </option>
        ))}
      </Select>
    </div>
  );
}

function Thumb({ src, alt, sizes, contain }: { src: string; alt: string; sizes: string; contain?: boolean }) {
  const [failed, setFailed] = useState(false);
  if (failed) {
    return (
      <div className="flex size-full flex-col items-center justify-center gap-1 text-muted-foreground">
        <ImageOff className="size-5" aria-hidden />
        <span className="text-[10px]">image file missing</span>
      </div>
    );
  }
  return (
    <Image
      src={src}
      alt={alt}
      fill
      unoptimized
      sizes={sizes}
      className={contain ? "object-contain" : "object-cover"}
      onError={() => setFailed(true)}
    />
  );
}

function SourceLink({ asset }: { asset: VisualAsset }) {
  const href = asset.page_url || asset.source_url;
  const name = asset.source_name || asset.provider;
  if (!href) return <span className="text-muted-foreground">{name}</span>;
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="inline-flex max-w-full items-center gap-1 text-primary hover:underline"
    >
      <span className="truncate">{name}</span>
      <ExternalLink className="size-3 shrink-0" aria-hidden />
      <span className="sr-only">(opens in a new tab)</span>
    </a>
  );
}

function VisualCard({ asset, onOpen }: { asset: VisualAsset; onOpen: () => void }) {
  const text = asset.caption || asset.description || asset.title;
  return (
    <Card className="flex flex-col overflow-hidden">
      <button
        type="button"
        onClick={onOpen}
        aria-label={`Open details for ${asset.asset_id}`}
        className="relative block aspect-video w-full cursor-pointer bg-muted outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring/60"
      >
        <Thumb src={apiFileUrl(asset.thumbnail_url)} alt={altText(asset)} sizes="(min-width: 1280px) 20vw, (min-width: 640px) 30vw, 50vw" />
      </button>
      <div className="flex flex-1 flex-col gap-1.5 p-2.5 text-xs">
        <div className="flex items-center justify-between gap-2">
          <span className="truncate font-mono font-medium">{asset.asset_id}</span>
          <RoleBadge role={asset.role} />
        </div>
        <div className="flex flex-wrap gap-1">
          <VerificationBadge status={asset.verification} confidence={asset.verification_confidence} />
          <RightsBadge rights={asset.rights} />
        </div>
        <p className="text-[10px] text-muted-foreground">
          preview {asset.usable_preview ? "✓" : "✕"} · publish {asset.usable_publish ? "✓" : "✕"}
        </p>
        {text && (
          <p className="line-clamp-2 leading-4 text-foreground/80" dir="auto" title={text}>
            {text}
          </p>
        )}
        {asset.reveals.length > 0 && (
          <p className="text-[10px] text-muted-foreground">
            reveals <span className="font-mono">{asset.reveals.join(" ")}</span>
          </p>
        )}
        <div className="mt-auto min-w-0 pt-0.5">
          <SourceLink asset={asset} />
        </div>
      </div>
    </Card>
  );
}

function MetaRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[120px_1fr] gap-2 py-1">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  );
}

function detailText(value: unknown): string {
  if (value == null || value === "") return "—";
  if (Array.isArray(value)) return value.length ? value.map(String).join(", ") : "—";
  if (typeof value === "number") return Number.isInteger(value) ? String(value) : value.toFixed(2);
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function VisualDetailDialog({
  asset,
  onClose,
  onSaved,
}: {
  asset: VisualAsset;
  onClose: () => void;
  onSaved: (asset: VisualAsset) => void;
}) {
  const ids = useId();
  const [role, setRole] = useState<AssetRole>(asset.role);
  const [rights, setRights] = useState(asset.rights);
  const [verification, setVerification] = useState<VerificationStatus>(asset.verification);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const changes = {
    ...(role !== asset.role && { role }),
    ...(rights !== asset.rights && { rights }),
    ...(verification !== asset.verification && { verification }),
  };
  const dirty = Object.keys(changes).length > 0;

  async function save() {
    setSaving(true);
    setError(null);
    setSaved(false);
    try {
      const updated = await api.updateVisual(asset.id, changes);
      setSaved(true);
      onSaved(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  const verificationDetail = Object.entries(asset.verification_detail ?? {});

  return (
    <Dialog
      open
      onClose={onClose}
      title={asset.title || asset.asset_id}
      description={`${asset.asset_id} · ${humanize(asset.type)}${asset.subject_type ? ` · ${humanize(asset.subject_type)}` : ""}`}
      className="max-h-[90vh] max-w-4xl overflow-y-auto"
    >
      <div className="grid grid-cols-1 gap-5 md:grid-cols-[1fr_240px]">
        <div className="min-w-0 space-y-4">
          <div className="relative aspect-video overflow-hidden rounded-md bg-muted">
            <Thumb src={apiFileUrl(asset.image_url)} alt={altText(asset)} sizes="(min-width: 768px) 60vw, 100vw" contain />
          </div>
          <a
            href={apiFileUrl(asset.image_url)}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
          >
            Open full size <ExternalLink className="size-3" aria-hidden />
            <span className="sr-only">(opens in a new tab)</span>
          </a>

          <dl className="divide-y divide-border text-xs">
            <MetaRow label="Caption">
              <span dir="auto">{asset.caption || "—"}</span>
            </MetaRow>
            <MetaRow label="Description">
              <span dir="auto">{asset.description || "—"}</span>
            </MetaRow>
            <MetaRow label="Entities">{detailText(asset.entities)}</MetaRow>
            <MetaRow label="Reveals">{detailText(asset.reveals)}</MetaRow>
            <MetaRow label="Found for">{asset.found_for || "—"}</MetaRow>
            <MetaRow label="Source">
              <SourceLink asset={asset} />
            </MetaRow>
            {asset.source_url && asset.source_url !== asset.page_url && (
              <MetaRow label="Image URL">
                <span className="break-all font-mono text-[10px]">{asset.source_url}</span>
              </MetaRow>
            )}
            <MetaRow label="Provider">{asset.provider}</MetaRow>
            <MetaRow label="License">{asset.license || "—"}</MetaRow>
            <MetaRow label="Credit">{asset.credit || "—"}</MetaRow>
            <MetaRow label="Rights">
              <RightsBadge rights={asset.rights} />
              {asset.rights_reason && <span className="ml-1.5 text-muted-foreground">{asset.rights_reason}</span>}
            </MetaRow>
            <MetaRow label="Usable in">
              preview {asset.usable_preview ? "yes" : "no"} · publish {asset.usable_publish ? "yes" : "no"}
            </MetaRow>
            <MetaRow label="Size">{asset.width && asset.height ? `${asset.width} × ${asset.height}` : "—"}</MetaRow>
            <MetaRow label="Quality">{asset.quality != null ? `${Math.round(asset.quality * 100)}%` : "—"}</MetaRow>
            <MetaRow label="Date">{asset.date || "—"}</MetaRow>
            <MetaRow label="Location">{asset.location || "—"}</MetaRow>
            <MetaRow label="Added">{formatDate(asset.created_at)}</MetaRow>
            <MetaRow label="Human review">{asset.human_override ? "yes — set by a reviewer" : "no"}</MetaRow>
          </dl>

          <div>
            <h3 className="mb-1.5 flex items-center gap-2 text-xs font-medium">
              Verification <VerificationBadge status={asset.verification} confidence={asset.verification_confidence} />
            </h3>
            {verificationDetail.length ? (
              <dl className="divide-y divide-border rounded-md border border-border px-2.5 text-xs">
                {verificationDetail.map(([k, v]) => (
                  <MetaRow key={k} label={humanize(k)}>
                    <span dir="auto">{detailText(v)}</span>
                  </MetaRow>
                ))}
              </dl>
            ) : (
              <p className="text-xs text-muted-foreground">Not checked by the vision model yet.</p>
            )}
          </div>
        </div>

        <div className="h-fit space-y-3 rounded-md border border-border p-3 md:sticky md:top-0">
          <h3 className="text-xs font-medium">Review</h3>
          <div>
            <label htmlFor={`${ids}-role`} className="mb-1 block text-xs font-medium text-muted-foreground">
              Role
            </label>
            <Select id={`${ids}-role`} value={role} onChange={(e) => setRole(e.target.value as AssetRole)}>
              {ROLE_OPTIONS.map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </Select>
            <p className="mt-1 text-[10px] leading-4 text-muted-foreground">
              Evidence: genuine case material. Context: the real place or period. Illustration: generic, always labelled on screen.
            </p>
          </div>
          <div>
            <label htmlFor={`${ids}-rights`} className="mb-1 block text-xs font-medium text-muted-foreground">
              Rights
            </label>
            <Select id={`${ids}-rights`} value={rights} onChange={(e) => setRights(e.target.value)}>
              {RIGHTS_OPTIONS.map((o) => (
                <option key={o} value={o}>
                  {humanize(o)}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <label htmlFor={`${ids}-verification`} className="mb-1 block text-xs font-medium text-muted-foreground">
              Verification
            </label>
            <Select
              id={`${ids}-verification`}
              value={verification}
              onChange={(e) => setVerification(e.target.value as VerificationStatus)}
            >
              {VERIFICATION_OPTIONS.map((o) => (
                <option key={o} value={o}>
                  {humanize(o)}
                </option>
              ))}
            </Select>
          </div>
          <Button size="sm" className="w-full" onClick={save} loading={saving} disabled={!dirty}>
            Save review
          </Button>
          <p aria-live="polite" className="text-[11px] text-emerald-600 dark:text-emerald-400">
            {saved && !dirty ? "Saved." : ""}
          </p>
          {error && <InlineAlert tone="error">{error}</InlineAlert>}
        </div>
      </div>
    </Dialog>
  );
}

function UploadDialog({
  caseId,
  open,
  onClose,
  onUploaded,
}: {
  caseId: number;
  open: boolean;
  onClose: () => void;
  onUploaded: (asset: VisualAsset) => void;
}) {
  const ids = useId();
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [caption, setCaption] = useState("");
  const [role, setRole] = useState<AssetRole>("evidence");
  const [rights, setRights] = useState("owned");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setBusy(true);
    setError(null);
    try {
      const asset = await api.uploadVisual(caseId, { file, title, caption, role, rights });
      setFile(null);
      setTitle("");
      setCaption("");
      onUploaded(asset);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Upload image"
      description="Add your own photo or scan to the case's visual library."
    >
      <form onSubmit={submit} className="space-y-3">
        <div>
          <label htmlFor={`${ids}-file`} className="mb-1 block text-xs font-medium text-muted-foreground">
            Image file
          </label>
          <Input
            id={`${ids}-file`}
            type="file"
            accept="image/*"
            required
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="h-auto py-1.5 file:mr-3 file:cursor-pointer file:rounded file:border-0 file:bg-muted file:px-2 file:py-1 file:text-xs file:text-foreground"
          />
        </div>
        <div>
          <label htmlFor={`${ids}-title`} className="mb-1 block text-xs font-medium text-muted-foreground">
            Title (optional)
          </label>
          <Input id={`${ids}-title`} value={title} onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div>
          <label htmlFor={`${ids}-caption`} className="mb-1 block text-xs font-medium text-muted-foreground">
            Caption (optional)
          </label>
          <Input
            id={`${ids}-caption`}
            value={caption}
            onChange={(e) => setCaption(e.target.value)}
            placeholder="Who or what it shows, where and when"
            dir="auto"
          />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label htmlFor={`${ids}-role`} className="mb-1 block text-xs font-medium text-muted-foreground">
              Role
            </label>
            <Select id={`${ids}-role`} value={role} onChange={(e) => setRole(e.target.value as AssetRole)}>
              {ROLE_OPTIONS.map((o) => (
                <option key={o} value={o}>
                  {o}
                </option>
              ))}
            </Select>
          </div>
          <div>
            <label htmlFor={`${ids}-rights`} className="mb-1 block text-xs font-medium text-muted-foreground">
              Rights
            </label>
            <Select id={`${ids}-rights`} value={rights} onChange={(e) => setRights(e.target.value)}>
              {RIGHTS_OPTIONS.map((o) => (
                <option key={o} value={o}>
                  {humanize(o)}
                </option>
              ))}
            </Select>
          </div>
        </div>
        {error && <InlineAlert tone="error">{error}</InlineAlert>}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" size="sm" onClick={onClose}>
            Close
          </Button>
          <Button type="submit" size="sm" loading={busy} disabled={!file}>
            <ImageUp className="size-3.5" /> Upload
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
