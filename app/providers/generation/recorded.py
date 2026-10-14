"""Deterministic recorded provider payloads for the isolated E2E stack."""

from __future__ import annotations

import json

from app.providers.generation.base import GenerationProvider, GenerationResult


class RecordedE2EGenerationProvider(GenerationProvider):
    name = "recorded-e2e"

    def is_configured(self) -> bool:
        return True

    async def check_health(self) -> bool:
        return True

    async def generate_text(self, role: str, system: str, user: str, images=None) -> GenerationResult:
        return GenerationResult(text="Recorded E2E response", model="recorded-e2e", provider=self.name)

    async def generate_structured(self, role: str, system: str, user: str, images=None):
        try:
            payload = json.loads(user)
        except (TypeError, ValueError):
            payload = {}
        language = str(payload.get("language") or "English")
        titles = {
            "English": "The Harbor Ledger",
            "German": "Das Hafenprotokoll",
            "Persian (Farsi)": "راز دفتر بندر",
            "Arabic": "سر دفتر الميناء",
        }.get(language, "The Harbor Ledger")
        if role == "case_naming_agent":
            count = max(1, min(int(payload.get("count") or 1), 12))
            return ({"editorial_concept": "the last recorded signal",
                     "candidates": [{"title": f"{titles} {index}", "angle": "the last signal"}
                                    for index in range(1, count + 1)]}, self._result(role))
        if role in {"case_title_critic", "native_title_critic"}:
            titles_in = payload.get("titles") or []
            return ({"scores": [{"title": title, "specificity": 0.9,
                                  "memorability": 0.8, "curiosity": 0.8,
                                  "documentary_tone": 0.9, "sensationalism_risk": 0.1,
                                  "spoiler_risk": 0.1, "epistemic_risk": 0.1,
                                  "native_quality": 0.95, "reason": "recorded payload"}
                                 for title in titles_in]}, self._result(role))
        if role == "case_discovery_agent":
            results = payload.get("results") or []
            candidates = []
            for item in results[:12]:
                title = str(item.get("title") or "").strip()
                if not title:
                    continue
                candidates.append({
                    "title": title,
                    "aliases": [],
                    "key_people": [],
                    "location": "",
                    "incident_date": None,
                    "latest_development_date": "2026-01-01",
                    "resolution_status": "SOLVED",
                    "resolution_evidence": "Recorded provider evidence.",
                    "source_urls": [item.get("url")] if item.get("url") else [],
                    "rationale": "Recorded provider candidate from the discovery result.",
                    "narrative_potential": 0.8,
                    "source_richness": "high",
                    "languages_available": ["en"],
                    "suggested_queries": [item.get("query") or title],
                    "angles": ["The documented sequence of events"],
                })
            return ({"candidates": candidates}, self._result(role))
        if role in {"shorts.write", "chapter_writer"}:
            spoken = str(payload.get("spoken_text") or "Recorded evidence-led short.").strip()
            language_code = str(payload.get("language") or "en")
            hook = str(payload.get("hook") or spoken[:96]) if language_code == "en" else spoken[:96]
            cta = {"fa": "روایت کامل مبتنی بر شواهد را ببینید.",
                   "ar": "شاهد القصة الكاملة المبنية على الأدلة.",
                   "de": "Sehen Sie die vollständige faktenbasierte Geschichte."}.get(
                       language_code, "Watch the full evidence-led story.")
            return ({"hook": hook, "body": spoken[:240],
                     "cta": cta,
                     "captions": [{"start": 0.1, "end": min(float(payload.get("target_duration") or 8) - 0.1, 4.0),
                                   "text": spoken[:42]}], "language": language_code}, self._result(role))
        if role in {"shorts.audit", "chapter_auditor"}:
            return ({"approved": True, "reasons": []}, self._result(role))
        return ({}, self._result(role))

    @staticmethod
    def _result(role: str) -> GenerationResult:
        return GenerationResult(text=json.dumps({"role": role}), model="recorded-e2e",
                                provider="recorded-e2e", generation_id=f"recorded-{role}")
