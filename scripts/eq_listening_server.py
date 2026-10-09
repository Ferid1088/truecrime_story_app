"""Blind A/B/C listening-test server for the dynamic-EQ validation.

Stdlib only. Serves the test page + audio from
`Claude outputs/eq_validation/`, stores submitted ratings as JSON files
in `eq_validation/ratings/`, and aggregates them into a per-language
report on request.

    .venv/bin/python scripts/eq_listening_server.py [port]   # default 8787
"""
import json
import random
import sys
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
DOCROOT = ROOT / "Claude outputs" / "eq_validation"
RATINGS = DOCROOT / "ratings"

METHODS = {"a_original": "Original", "b_static_eq": "Static EQ",
           "c_dynamic_eq": "Dynamic EQ"}
AXES = ["naturalness", "harshness_reduction", "voice_clarity",
        "warmth_depth", "speaker_identity", "listening_comfort"]


def scan_samples():
    """lang -> [{name, files, events}] for every dir with 3 variants."""
    out = {}
    for lang_dir in sorted(DOCROOT.iterdir()):
        if not lang_dir.is_dir() or lang_dir.name in ("sources", "ratings"):
            continue
        samples = []
        for sdir in sorted(lang_dir.iterdir()):
            files = {m: f"{lang_dir.name}/{sdir.name}/{m}.mp3"
                     for m in METHODS
                     if (sdir / f"{m}.mp3").exists()}
            if len(files) != 3:
                continue
            entry = {"name": sdir.name, "files": files, "events": []}
            dyn = sdir / "dynamics.json"
            if dyn.exists():
                d = json.loads(dyn.read_text())
                ev = [{"start": e["start"], "end": e["end"],
                       "max_gr_db": e["max_gr_db"], "band": b["name"]}
                      for b in d.get("bands", []) for e in b.get("events", [])]
                if d.get("deesser"):
                    ev += [{"start": e["start"], "end": e["end"],
                            "max_gr_db": e["max_gr_db"], "band": "deesser"}
                           for e in d["deesser"].get("events", [])]
                ev.sort(key=lambda e: -e["max_gr_db"])
                entry["events"] = ev[:12]
                entry["duration"] = d.get("duration_seconds")
            samples.append(entry)
        if samples:
            out[lang_dir.name] = samples
    return out


def aggregate():
    """All submitted ratings -> per-language means per real identity."""
    rows = []
    for f in sorted(RATINGS.glob("*.json")):
        try:
            rows.append(json.loads(f.read_text()))
        except (ValueError, OSError):
            continue
    agg = {}
    for r in rows:
        lang = r.get("language", "?")
        mapping = r.get("mapping", {})          # letter -> method key
        for letter, axes in (r.get("ratings") or {}).items():
            method = METHODS.get(mapping.get(letter), "?")
            cell = agg.setdefault(lang, {}).setdefault(
                method, {"n": 0, **{a: 0.0 for a in AXES}})
            cell["n"] += 1
            for a in AXES:
                v = axes.get(a)
                if isinstance(v, (int, float)):
                    cell[a] += v
    for lang, methods in agg.items():
        for m, cell in methods.items():
            n = max(cell["n"], 1)
            for a in AXES:
                cell[a] = round(cell[a] / n, 2)
    return {"submissions": len(rows), "by_language": agg}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(DOCROOT), **kw)

    def log_message(self, *a):  # quiet
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/test"):
            p = DOCROOT / "listening_test.html"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(p.read_bytes())
        elif path == "/api/samples":
            self._json(scan_samples())
        elif path == "/api/report":
            self._json(aggregate())
        elif path == "/api/report.md":
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.end_headers()
            self.wfile.write(render_report().encode())
        else:
            super().do_GET()

    def do_POST(self):
        if urlparse(self.path).path == "/api/ratings":
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._json({"ok": False, "error": "bad json"}, 400)
            RATINGS.mkdir(exist_ok=True)
            name = f"{body.get('language','x')}_{body.get('sample','x')}_" \
                   f"{int(time.time())}_{random.randint(1000,9999)}.json"
            (RATINGS / name).write_text(
                json.dumps(body, ensure_ascii=False, indent=1))
            return self._json({"ok": True, "file": name})
        self.send_error(404)


def render_report():
    agg = aggregate()
    lines = ["# Blind listening test — results", "",
             f"Submissions: {agg['submissions']}", ""]
    axes = AXES
    for lang, methods in sorted(agg["by_language"].items()):
        lines += [f"## {lang}", "", "| Method | n | " + " | ".join(axes) + " |",
                  "|---|---|" + "---|" * len(axes)]
        for m in ("Original", "Static EQ", "Dynamic EQ"):
            c = methods.get(m)
            if c:
                lines.append(f"| {m} | {c['n']} | "
                             + " | ".join(str(c[a]) for a in axes) + " |")
        lines.append("")
    (DOCROOT / "listening_report.md").write_text("\n".join(lines))
    return "\n".join(lines)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8787
    print(f"Listening test → http://localhost:{port}/test")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
