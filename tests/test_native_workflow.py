import subprocess
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
