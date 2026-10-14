import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_native_scripts_help_does_not_start_services():
    for script in ("dev_up.sh", "dev_down.sh"):
        result = subprocess.run(["bash", str(ROOT / "scripts" / script), "--help"],
                                capture_output=True, text=True, check=False)
        assert result.returncode == 0
        assert "Usage:" in result.stdout


def test_native_process_scripts_have_expected_order_and_no_retired_search_service():
    up = (ROOT / "scripts/dev_up.sh").read_text()
    assert '"$ROOT/scripts/pg_up.sh"' in up
    assert "start_process api" in up
    assert up.index("start_process api") < up.index("start_process worker")
    assert up.index("start_process worker") < up.index("start_process frontend")
    assert "BRAVE_SEARCH_API_KEY" in up


def test_dev_status_lists_brave_and_app_components():
    env = os.environ.copy()
    env["TRUECRIME_DATABASE_URL"] = "sqlite:///./truecrime.db"
    result = subprocess.run(["bash", str(ROOT / "scripts/dev_up.sh"), "--status"],
                            cwd=ROOT, env=env, capture_output=True, text=True,
                            check=False)
    assert result.returncode == 0
    assert "postgresql: not used (sqlite)" in result.stdout
    assert "brave:" in result.stdout
    for component in ("api:", "worker:", "frontend:"):
        assert component in result.stdout


def test_require_search_stops_before_starting_services_when_brave_key_missing():
    env = os.environ.copy()
    env["BRAVE_SEARCH_API_KEY"] = ""
    env["TRUECRIME_DATABASE_URL"] = "sqlite:///./truecrime.db"
    result = subprocess.run(["bash", str(ROOT / "scripts/dev_up.sh"), "--require-search"],
                            cwd=ROOT, env=env, capture_output=True, text=True,
                            check=False)
    assert result.returncode != 0
    assert "brave search is unconfigured" in (result.stdout + result.stderr).lower()


def test_dev_up_records_pid_only_after_readiness():
    up = (ROOT / "scripts/dev_up.sh").read_text()
    assert 'new_pid=$!' in up
    assert 'echo "$new_pid" > "$pidfile"' in up
    assert up.index('_wait_for_port') < up.index('echo "$new_pid" > "$pidfile"')
