"""The container setup is checked as text: this environment has no Docker daemon, so what can go wrong
silently (a published database port, a root user, a secret in the file) is pinned here."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
SERVICES = compose["services"]


def ports(svc):
    return svc.get("ports", [])


def test_every_published_port_is_bound_to_loopback():
    for name, svc in SERVICES.items():
        for p in ports(svc):
            assert str(p).startswith("127.0.0.1:"), f"{name} publishes {p} on all interfaces"


def test_database_is_not_published_and_has_a_healthcheck():
    pg = SERVICES["postgres"]
    assert not ports(pg)
    assert "healthcheck" in pg


def test_api_and_worker_share_one_job_runner_setting_and_database():
    env = compose["x-app-env"]
    assert env["TRUECRIME_JOB_RUNNER"] == "external"
    assert env["TRUECRIME_DATABASE_URL"].startswith("postgresql+psycopg://")
    assert SERVICES["worker"]["command"] == ["python", "-m", "app.worker"]
    assert SERVICES["api"]["environment"] == SERVICES["worker"]["environment"] == env


def test_access_token_and_db_password_are_required_not_defaulted():
    text = (ROOT / "docker-compose.yml").read_text()
    assert "${TRUECRIME_ACCESS_TOKEN:-${ACCESS_TOKEN:?" in text
    assert "${TRUECRIME_POSTGRES_PASSWORD:-${POSTGRES_PASSWORD:?" in text


def test_start_order_waits_for_healthy_dependencies():
    assert SERVICES["api"]["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert SERVICES["worker"]["depends_on"]["api"]["condition"] == "service_healthy"
    assert SERVICES["web"]["depends_on"]["api"]["condition"] == "service_healthy"


def test_containers_drop_capabilities_and_forbid_new_privileges():
    for name in ("api", "worker", "web"):
        svc = SERVICES[name]
        assert svc["cap_drop"] == ["ALL"], name
        assert "no-new-privileges:true" in svc["security_opt"], name


def _env_pairs():
    blocks = [compose["x-app-env"]] + [svc.get("environment", {}) for svc in SERVICES.values()]
    for env in blocks:
        items = env.items() if isinstance(env, dict) else (e.split("=", 1) for e in env)
        yield from items


def test_secret_values_come_from_the_environment_never_written_in_the_file():
    seen = 0
    for key, value in _env_pairs():
        if re.search(r"(?i)password|token|secret|api_key", key):
            seen += 1
            assert str(value).startswith("${"), f"{key} has a literal value"
    assert seen >= 3


@pytest.mark.parametrize("path", ["Dockerfile", "frontend/Dockerfile"])
def test_images_run_as_a_non_root_user_with_a_healthcheck(path):
    text = (ROOT / path).read_text()
    user = re.findall(r"^USER\s+(\S+)", text, re.M)
    assert user and user[-1] not in ("root", "0")
    assert "HEALTHCHECK" in text
    assert re.search(r"^FROM .* AS (builder|build)", text, re.M), "multi-stage build expected"


def test_api_image_does_not_ship_tests_secrets_or_data():
    ignore = (ROOT / ".dockerignore").read_text().split()
    for must in (".env", ".git", "data/", "tests/", "truecrime.db*"):
        assert must in ignore, must
    text = (ROOT / "Dockerfile").read_text()
    assert "COPY . ." not in text


def test_web_dockerfile_ignores_node_modules():
    assert "node_modules" in (ROOT / "frontend/.dockerignore").read_text()


def test_env_example_lists_every_variable_compose_requires():
    ex = (ROOT / ".env.example").read_text()
    for var in (
        "TRUECRIME_ACCESS_TOKEN", "TRUECRIME_POSTGRES_PASSWORD",
        "TRUECRIME_PUBLIC_API_URL", "TRUECRIME_COOKIE_SECURE",
        "TRUECRIME_CORS_ORIGINS",
    ):
        assert re.search(rf"^{var}=", ex, re.M), var


@pytest.mark.skipif(not shutil.which("docker"), reason="docker CLI not installed")
def test_docker_compose_accepts_the_file():
    env = {"POSTGRES_PASSWORD": "x", "ACCESS_TOKEN": "y", "PATH": __import__("os").environ["PATH"],
           "HOME": __import__("os").environ.get("HOME", "/tmp")}
    r = subprocess.run(["docker", "compose", "config", "-q"], cwd=ROOT, env=env, capture_output=True, text=True)
    if "docker" in r.stderr.lower() and "not a docker command" in r.stderr.lower():
        pytest.skip("docker compose plugin missing")
    assert r.returncode == 0, r.stderr


def test_every_long_running_service_has_a_real_healthcheck():
    # `docker compose up --wait` (CI, deploy) fails on a service without one
    image_defined = {"api": "Dockerfile", "web": "frontend/Dockerfile"}
    for name in ("postgres", "api", "worker", "web"):
        hc = SERVICES[name].get("healthcheck")
        if name in image_defined:
            assert hc is None or not hc.get("disable"), name
            assert "HEALTHCHECK" in (ROOT / image_defined[name]).read_text(), name
        else:
            assert hc and not hc.get("disable"), name
