#!/usr/bin/env python3
"""Scan for committed secrets (API keys, tokens, private keys).

Standard library only.

    python scripts/secret_scan.py                 # every tracked text file in the working tree
    git log -p --all | python scripts/secret_scan.py --stdin   # the whole git history

Exit code 1 when something that looks like a secret is found. Matches are
printed with the secret itself masked, so the report is safe to share.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

PATTERNS = [
    ("OpenAI-style key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}")),
    ("Anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    ("Private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "Assigned secret",
        re.compile(
            r"""(?ix)
            \b[A-Z0-9_]*(?:API_?KEY|SECRET|TOKEN|PASSWORD)[A-Z0-9_]*\b
            \s*[:=]\s*
            ['"]([A-Za-z0-9_\-+/=.]{24,})['"]
            """
        ),
    ),
]

# Files that are expected to hold placeholder values, or are not worth scanning.
SKIP_SUFFIXES = {".lock", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".mp4", ".mp3", ".wav", ".pdf", ".ico", ".woff", ".woff2", ".db"}
SKIP_NAMES = {"package-lock.json", ".env.example"}
MAX_BYTES = 2_000_000
PLACEHOLDER = re.compile(r"(?i)(your[_\-]?|example|placeholder|changeme|xxxx|<[^>]+>|\.\.\.)")


def mask(text: str) -> str:
    return text[:4] + "…" + text[-2:] if len(text) > 8 else "…"


def scan_text(label: str, text: str) -> list[str]:
    findings: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for name, pattern in PATTERNS:
            match = pattern.search(line)
            if not match:
                continue
            secret = match.group(1) if match.groups() else match.group(0)
            if PLACEHOLDER.search(secret):
                continue
            # A generic "KEY = '...'" assignment only counts when the value looks random:
            # real keys mix letters and digits, readable phrases and constants do not.
            if name == "Assigned secret" and not (re.search(r"\d", secret) and re.search(r"[A-Za-z]", secret)):
                continue
            findings.append(f"{label}:{lineno}: {name}: {mask(secret)}")
    return findings


def tracked_files() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-z"], check=True, capture_output=True).stdout
    return [Path(p) for p in out.decode("utf-8").split("\0") if p]


def scan_tree() -> list[str]:
    findings: list[str] = []
    for path in tracked_files():
        if path.name in SKIP_NAMES or path.suffix.lower() in SKIP_SUFFIXES or not path.is_file():
            continue
        if path.stat().st_size > MAX_BYTES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        findings.extend(scan_text(str(path), text))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stdin", action="store_true", help="scan text piped in (for example `git log -p --all`)")
    args = parser.parse_args(argv)
    findings = scan_text("<stdin>", sys.stdin.read()) if args.stdin else scan_tree()
    if findings:
        print(f"FAIL secret scan: {len(findings)} possible secret(s)")
        for f in findings[:100]:
            print(f"  - {f}")
        return 1
    print("OK secret scan: nothing found")
    return 0


if __name__ == "__main__":
    sys.exit(main())
