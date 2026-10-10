"""The secret scanner must catch real-looking keys, ignore placeholders, and never print a secret in full."""
import importlib.util
import subprocess
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "scripts" / "secret_scan.py"
_spec = importlib.util.spec_from_file_location("secret_scan", _PATH)
secret_scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(secret_scan)

# Fake secrets are assembled at runtime so this file itself stays clean under the scanner.
FAKE_SK = "sk-" + "A1b2C3d4" * 4
FAKE_AWS = "AKIA" + "ABCDEFGH12345678"
FAKE_GH = "ghp_" + "a1B2c3D4e5" * 4
FAKE_ASSIGN = 'TrueCrime_ELEVENLABS_API_KEY = "' + "q9Zr" * 8 + '"'


def test_detects_common_key_shapes():
    text = "\n".join([f"x = '{FAKE_SK}'", FAKE_AWS, f"token {FAKE_GH}", FAKE_ASSIGN])
    findings = secret_scan.scan_text("f.py", text)
    kinds = " ".join(findings)
    assert len(findings) == 4
    for kind in ("OpenAI-style key", "AWS access key id", "GitHub token", "Assigned secret"):
        assert kind in kinds


def test_detects_a_private_key_block():
    header = "-----BEGIN " + "RSA PRIVATE KEY-----"
    assert secret_scan.scan_text("k.pem", header)


def test_report_never_contains_the_full_secret():
    findings = secret_scan.scan_text("f.py", FAKE_SK)
    assert findings and FAKE_SK not in findings[0]


def test_placeholders_and_clean_text_are_ignored():
    text = "\n".join(
        [
            'API_KEY = "your-api-key-goes-here-please"',
            'TOKEN = "<paste-token-here-xxxxxxxxxxxx>"',
            "no secrets on this line",
            "sk-short",
            'secret = "UNIQUE_RAW_TRANSCRIPT_PHRASE"',
            'secret_key: "readable-local-dev-only-phrase-change-in-production"',
        ]
    )
    assert secret_scan.scan_text("f.py", text) == []


def test_tree_scan_reads_tracked_files_only(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.py").write_text(f"KEY = '{FAKE_SK}'\n", encoding="utf-8")
    (tmp_path / "untracked.py").write_text(f"KEY = '{FAKE_SK}'\n", encoding="utf-8")
    (tmp_path / ".env.example").write_text(f"KEY = '{FAKE_SK}'\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py", ".env.example"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    findings = secret_scan.scan_tree()
    assert len(findings) == 1 and findings[0].startswith("tracked.py:1")


def test_main_exit_codes(tmp_path, monkeypatch, capsys):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "ok.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "ok.py"], cwd=tmp_path, check=True)
    monkeypatch.chdir(tmp_path)
    assert secret_scan.main([]) == 0
    (tmp_path / "bad.py").write_text(f"KEY = '{FAKE_SK}'\n", encoding="utf-8")
    subprocess.run(["git", "add", "bad.py"], cwd=tmp_path, check=True)
    assert secret_scan.main([]) == 1
    assert "FAIL secret scan" in capsys.readouterr().out
