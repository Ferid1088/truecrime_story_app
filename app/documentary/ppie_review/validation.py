"""Whether the recognizer may decide on its own, measured on clips a person labelled (phonetic benchmark, status human_verified, purpose
validation: a clip that only illustrates a reported problem never counts) and never on a model's opinion. Each clip is a word with its candidate readings; it is "said wrong" when the person heard another candidate than the text expects.
The labels are split by a fixed hash into a calibration part (the margin is chosen there) and a held-out part (only the held-out part is reported as
the result). The original clip and each slower copy are measured separately. The recognizer is validated only when the held-out part has enough
clips of both classes and its precision, recall and false-positive rate for "said wrong" meet the configured limits; until then every review is
advisory. The report is kept as the benchmark's result (a property of the model, not a pronunciation decision)."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections import Counter
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import PhoneticSample
from app.documentary.phonetic_benchmark import BASE
from app.documentary.ppie_review.acoustic import FRAME

REPORT = BASE / "validation.json"
MARGINS = [round(0.05 * k, 2) for k in range(1, 31)]


def held_out(sample_id: int) -> bool:
    return int(hashlib.sha256(f"split:{sample_id}".encode()).hexdigest(), 16) % 1000 >= ai_config.ppie_review.validation.calibration_share * 1000


def scores(listener, path: Path, candidates: list[dict], speed: float) -> dict[str, float]:
    """The loss per frame of each candidate over the whole clip (a copy at `speed`)."""
    from app.documentary.ppie_review.audio_prep import stretch

    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "clip.wav"
        stretch(path, copy, speed, ai_config.ppie_review.analysis_rate)
        lp = listener.log_probs(str(copy))
        out = {}
        for c in candidates:
            try:
                out[c["key"]] = listener.fit(lp, 0.0, lp.shape[0] * FRAME, c["ipa"]) / max(1, lp.shape[0])
            except ValueError:
                continue
        return out


def decide(losses: dict[str, float], expected: str, margin: float) -> str:
    if expected not in losses or len(losses) < 2:
        return "NOT_EVALUABLE"
    rival = min(v for k, v in losses.items() if k != expected)
    return "PASS" if rival - losses[expected] >= margin else "FAIL" if losses[expected] - rival >= margin else "UNCERTAIN"


def metrics(rows: list[tuple[bool, str]]) -> dict:
    """rows: (said wrong per the person, the recognizer's decision). A FAIL is a detection; UNCERTAIN is counted apart, never as right."""
    tp = sum(1 for wrong, d in rows if wrong and d == "FAIL")
    fp = sum(1 for wrong, d in rows if not wrong and d == "FAIL")
    fn = sum(1 for wrong, d in rows if wrong and d == "PASS")
    tn = sum(1 for wrong, d in rows if not wrong and d == "PASS")
    wrong, right = sum(1 for w, _ in rows if w), sum(1 for w, _ in rows if not w)
    return {"said_wrong": wrong, "said_right": right, "detected": tp, "false_alarms": fp, "missed": fn, "confirmed_right": tn,
            "uncertain": sum(1 for _, d in rows if d in ("UNCERTAIN", "NOT_EVALUABLE")),
            "precision": round(tp / (tp + fp), 3) if tp + fp else None, "recall": round(tp / wrong, 3) if wrong else None,
            "false_positive_rate": round(fp / right, 3) if right else None, "false_negative_rate": round(fn / wrong, 3) if wrong else None}


def evaluate(db: Session, listener, language: str = "fa") -> dict:
    """Scores every human-labelled clip at every analysis speed, chooses the margin on the calibration part and reports the held-out part."""
    cfg = ai_config.ppie_review.validation
    samples = [s for s in db.query(PhoneticSample).filter_by(language=language, status="human_verified", purpose="validation")
               if s.human_key and s.human_key not in ("unclear", "none") and s.expected_key]
    by_speed = {}
    for speed in ai_config.ppie_review.analysis_speeds:
        scored = [(s, scores(listener, BASE / s.audio_path, json.loads(s.candidates), speed)) for s in samples]
        calibration = [(s.human_key != s.expected_key, losses, s.expected_key) for s, losses in scored if not held_out(s.id)]
        margin = next((m for m in MARGINS if (p := metrics([(w, decide(losses, e, m)) for w, losses, e in calibration])["precision"]) is not None
                       and p >= cfg.min_precision), MARGINS[-1])
        test = [(s, s.human_key != s.expected_key, decide(losses, s.expected_key, margin)) for s, losses in scored if held_out(s.id)]
        result = metrics([(w, d) for _, w, d in test])
        result["by_category"] = {cat: metrics([(w, d) for s, w, d in test if s.category == cat]) for cat in sorted({s.category for s, _, _ in test})}
        result["margin"] = margin
        result["passes"] = (result["said_wrong"] >= cfg.min_labels_per_class and result["said_right"] >= cfg.min_labels_per_class
                            and (result["precision"] or 0) >= cfg.min_precision and (result["recall"] or 0) >= cfg.min_recall
                            and result["false_positive_rate"] is not None and result["false_positive_rate"] <= cfg.max_false_positive_rate)
        by_speed[str(speed)] = result
    best = by_speed["1.0"]
    report = {"model": listener.name, "labelled_clips": len(samples), "calibration_clips": sum(1 for s in samples if not held_out(s.id)),
              "held_out_clips": sum(1 for s in samples if held_out(s.id)), "categories": dict(Counter(s.category for s in samples)),
              "by_speed": by_speed, "validated": bool(best["passes"]), "margin": best["margin"],
              "slower_copy_helps": {k: bool(v["passes"]) and (v["recall"] or 0) > (best["recall"] or 0) for k, v in by_speed.items() if k != "1.0"}}
    return report


def save(report: dict) -> None:
    from app.documentary import storage

    storage.write_atomic(REPORT, json.dumps(report, ensure_ascii=False, indent=1))


def current() -> dict:
    """The last validation report; an empty, unvalidated one when none exists."""
    try:
        return json.loads(REPORT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"validated": False, "labelled_clips": 0, "note": "the recognizer has never been validated"}
