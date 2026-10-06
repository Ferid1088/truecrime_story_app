"use client";

import { useState } from "react";
import { ExternalLink, Search } from "lucide-react";
import { api } from "@/lib/api";
import { langLabel } from "@/lib/format";
import type { CorpusHit, CorpusSearchResponse } from "@/lib/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { EmptyState, ErrorState } from "@/components/state";

const LANGS = [
  { value: "", label: "All languages" },
  { value: "en", label: "English" },
  { value: "de", label: "Deutsch" },
  { value: "fa", label: "فارسی" },
  { value: "ar", label: "العربية" },
];

function HitCard({ hit }: { hit: CorpusHit }) {
  return (
    <div className="rounded-md border border-border bg-card p-3">
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-foreground">
          {hit.source_title || `Source #${hit.source_id}`}
        </span>
        {hit.language && <Badge variant="default">{langLabel(hit.language)}</Badge>}
        <span className="ml-auto text-xs tabular-nums text-muted-foreground">
          score {hit.score.toFixed(3)}
          {hit.bm25_score > 0 && ` · bm25 ${hit.bm25_score.toFixed(2)}`}
          {hit.dense_score > 0 && ` · dense ${hit.dense_score.toFixed(2)}`}
        </span>
      </div>
      <p className="line-clamp-4 whitespace-pre-wrap text-sm text-muted-foreground">
        {hit.text}
      </p>
      <div className="mt-1.5 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
        <span>chunk #{hit.chunk_id} · source #{hit.source_id}</span>
        {hit.location && <span>loc: {hit.location}</span>}
        {hit.source_url && (
          <a
            href={hit.source_url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 hover:text-foreground"
          >
            original <ExternalLink className="size-3" />
          </a>
        )}
      </div>
    </div>
  );
}

export function CorpusSearchTab({ caseId }: { caseId: number }) {
  const [q, setQ] = useState("");
  const [language, setLanguage] = useState("");
  const [limit, setLimit] = useState(10);
  const [result, setResult] = useState<CorpusSearchResponse | null>(null);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function search() {
    const query = q.trim();
    if (!query) return;
    setSearching(true);
    setError(null);
    try {
      const res = await api.corpusSearch(
        caseId,
        query,
        language || undefined,
        limit,
      );
      setResult(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setResult(null);
    } finally {
      setSearching(false);
    }
  }

  return (
    <div className="space-y-4">
      <form
        className="flex flex-wrap items-end gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          search();
        }}
      >
        <div className="min-w-56 flex-1">
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search downloaded research — any language…"
            aria-label="Corpus search query"
          />
        </div>
        <Select
          value={language}
          onChange={(e) => setLanguage(e.target.value)}
          aria-label="Filter by language"
        >
          {LANGS.map((l) => (
            <option key={l.value} value={l.value}>
              {l.label}
            </option>
          ))}
        </Select>
        <Select
          value={String(limit)}
          onChange={(e) => setLimit(Number(e.target.value))}
          aria-label="Result limit"
        >
          {[5, 10, 20, 30].map((n) => (
            <option key={n} value={n}>
              top {n}
            </option>
          ))}
        </Select>
        <Button type="submit" size="sm" loading={searching} disabled={!q.trim()}>
          <Search className="size-3.5" /> Search
        </Button>
      </form>

      {error && <ErrorState message={error} onRetry={search} />}

      {!error && !result && !searching && (
        <EmptyState
          title="Search the case corpus"
          description="Hybrid BM25 + bge-m3 retrieval over every downloaded source chunk. Persian and Arabic queries match English evidence semantically."
        />
      )}

      {searching && (
        <p className="text-sm text-muted-foreground">
          Searching… (first search builds the index — can take a minute)
        </p>
      )}

      {result && !searching && (
        <div className="space-y-3">
          <p className="text-xs text-muted-foreground">
            {result.hits.length} hit{result.hits.length === 1 ? "" : "s"} across{" "}
            {result.chunks_indexed} indexed chunks ·{" "}
            {result.dense_enabled ? "BM25 + bge-m3 dense" : "BM25 only (dense unavailable)"}
          </p>
          {result.hits.length === 0 ? (
            <EmptyState
              title="No matches"
              description="No chunk in this case's corpus matched the query."
            />
          ) : (
            result.hits.map((h) => <HitCard key={h.chunk_id} hit={h} />)
          )}
        </div>
      )}
    </div>
  );
}
