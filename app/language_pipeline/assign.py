"""Where the facts beyond the 100-fact pack belong in the story.

The plan assigns evidence ids to its acts, but only from the 100-fact pack. The shared draft
reads every promoted fact, so each fact beyond the pack is given to the act whose assigned
evidence it is closest to (multilingual embeddings, cosine to the act's centroid)."""

from __future__ import annotations

import numpy as np

from app.language_pipeline.materials import SharedMaterial
from app.research_engine.embeddings import cosine_matrix


def extra_fact_ids(material: SharedMaterial) -> list[str]:
    in_pack = {f["id"] for f in material.pack.get("facts") or []}
    return [f["id"] for f in material.draft_pack.get("facts") or [] if f["id"] not in in_pack]


async def extra_evidence_by_act(material: SharedMaterial, embedder) -> dict[str, list[str]]:
    """act id -> ids of the extra facts nearest to that act, in the draft pack's order."""
    extras = {f["id"]: f for f in material.draft_pack.get("facts") or []}
    ids = extra_fact_ids(material)
    result: dict[str, list[str]] = {act_id: [] for act_id in material.act_ids}
    if not ids or embedder is None or not material.acts:
        return result
    packed = {f["id"]: f for f in material.pack.get("facts") or []}
    centroids, acts = [], []
    for act in material.acts:
        texts = [packed[i]["claim"] for i in act.get("evidence_ids") or [] if i in packed]
        texts += [str(act.get("purpose") or ""), *map(str, act.get("key_beats") or [])]
        vectors = await embedder.embed([t for t in texts if t])
        if vectors.size:
            centroids.append(vectors.mean(axis=0))
            acts.append(str(act["id"]))
    if not centroids:
        return result
    claims = await embedder.embed([extras[i]["claim"] or "" for i in ids])
    best = cosine_matrix(claims, np.vstack(centroids)).argmax(axis=1)
    for fact_id, act_index in zip(ids, best, strict=True):
        result[acts[int(act_index)]].append(fact_id)
    return result


async def related_to_contradictions(material: SharedMaterial, embedder, per_contradiction: int) -> dict[str, list[str]]:
    """contradiction id -> ids of the facts nearest to what it describes (the evidence on both sides of a
    dispute), so an act package that carries the contradiction carries the facts it is about."""
    contradictions = material.draft_pack.get("contradictions") or []
    facts = material.draft_pack.get("facts") or []
    if not contradictions or not facts or embedder is None or per_contradiction <= 0:
        return {}
    vectors = await embedder.embed([f"{c.get('topic') or ''}. {c.get('description') or ''}" for c in contradictions])
    claims = await embedder.embed([f["claim"] or "" for f in facts])
    nearest = np.argsort(-cosine_matrix(vectors, claims), axis=1)[:, :per_contradiction]
    return {c["id"]: [facts[int(i)]["id"] for i in row] for c, row in zip(contradictions, nearest, strict=True)}
