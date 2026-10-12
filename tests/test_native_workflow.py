import subprocess
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_native_scripts_help_does_not_start_services():
    for script in ("dev_up.sh", "dev_down.sh", "searxng_up.sh"):
        result = subprocess.run(
            ["bash", str(ROOT / "scripts" / script), "--help"],
            capture_output=True, text=True, check=False,
        )
        assert result.returncode == 0
        assert "Usage:" in result.stdout


def test_native_process_scripts_exist_and_have_expected_order():
    up = (ROOT / "scripts/dev_up.sh").read_text()
    assert "brew services start postgresql@16" in up
    assert '"$ROOT/scripts/searxng_up.sh"' in up
    assert up.index('"$ROOT/scripts/searxng_up.sh"') < up.index("start_process api")
    assert up.index("start_process api") < up.index("start_process worker")
    assert up.index("start_process worker") < up.index("start_process frontend")
    assert "docker" not in up.lower()


def test_dev_status_skips_postgres_for_sqlite_and_lists_all_components():
    env = os.environ.copy()
    env["TRUECRIME_DATABASE_URL"] = "sqlite:///./truecrime.db"
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/dev_up.sh"), "--status"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    assert "postgresql: not used (sqlite)" in result.stdout
    for component in ("searxng:", "api:", "worker:", "frontend:"):
        assert component in result.stdout


def test_dev_status_checks_postgres_when_configured():
    env = os.environ.copy()
    env["TRUECRIME_DATABASE_URL"] = "postgresql+psycopg://localhost/truecrime"
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/dev_up.sh"), "--status"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    assert "postgresql: not used (sqlite)" not in result.stdout


def test_require_search_stops_before_starting_services_when_unavailable():
    env = os.environ.copy()
    env.update({
        "TRUECRIME_DATABASE_URL": "sqlite:///./truecrime.db",
        "TRUECRIME_SEARXNG_URL": "http://127.0.0.1:1",
        "SEARXNG_DIR": "",
    })
    result = subprocess.run(
        ["bash", str(ROOT / "scripts/dev_up.sh"), "--require-search"],
        cwd=ROOT, env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "searxng is unavailable" in combined.lower()
    assert "--require-search" in combined


def test_searxng_dir_is_configured_only_in_ignored_local_file(tmp_path):
    script = (ROOT / "scripts/searxng_up.sh").read_text()
    assert "SEARXNG_DIR" in script and ".env.local" in script
    assert "--set-dir" in script


def test_dev_up_records_pid_only_after_readiness_and_cleans_failed_start():
    up = (ROOT / "scripts/dev_up.sh").read_text()
    assert 'new_pid=$!' in up
    assert 'echo "$new_pid" > "$pidfile"' in up
    assert up.index('_wait_for_port') < up.index('echo "$new_pid" > "$pidfile"')
    assert 'did not become ready; its pidfile was not recorded.' in up
    assert 'kill "$new_pid"' in up


def test_native_search_template_has_json_api_and_local_secret_placeholder():
    template = (ROOT / "config/searxng/settings.yml.template").read_text()
    assert "format" in template or "json" in template
    assert "__TRUECRIME_SEARXNG_SECRET__" in template
    assert "127.0.0.1" in template


def test_removed_service_files_and_container_only_variables_are_absent():
    for path in ("Dockerfile", ".dockerignore", "docker-compose.yml", "frontend/Dockerfile"):
        assert not (ROOT / path).exists()
    env = (ROOT / ".env.example").read_text()
    for name in ("TRUECRIME_POSTGRES_PASSWORD", "TRUECRIME_PY_REQUIREMENTS", "TRUECRIME_SEARXNG_SECRET"):
        assert name not in env
