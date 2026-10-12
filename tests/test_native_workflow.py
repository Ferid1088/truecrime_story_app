import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _free_port() -> int:
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _fake_dev_root(tmp_path: Path, *, frontend_failure: bool = False) -> tuple[Path, dict[str, str]]:
    root = tmp_path / "app"
    (root / "scripts").mkdir(parents=True)
    (root / ".venv/bin").mkdir(parents=True)
    (root / "frontend/node_modules/.bin").mkdir(parents=True)
    (root / "bin").mkdir()
    shutil.copy2(ROOT / "scripts/dev_up.sh", root / "scripts/dev_up.sh")
    shutil.copy2(ROOT / "scripts/dev_down.sh", root / "scripts/dev_down.sh")
    shutil.copy2(ROOT / "scripts/detached_exec.py", root / "scripts/detached_exec.py")
    (root / "scripts/dev_up.sh").chmod(0o755)
    (root / "scripts/dev_down.sh").chmod(0o755)
    (root / "scripts/searxng_up.sh").write_text("#!/usr/bin/env bash\nexit 1\n")
    (root / "scripts/searxng_up.sh").chmod(0o755)
    (root / "frontend/node_modules/.bin/next").write_text("#!/bin/sh\nexit 0\n")
    (root / "frontend/node_modules/.bin/next").chmod(0o755)

    service = root / "fake_service.py"
    service.write_text(
        "import socket\n"
        "import sys\n"
        "import time\n"
        "\n"
        "mode = sys.argv[1]\n"
        "if mode == 'frontend' and '--fail' in sys.argv:\n"
        "    print('FATAL frontend failed to bind', flush=True)\n"
        "    raise SystemExit(7)\n"
        "if mode in ('api', 'frontend'):\n"
        "    port = int(sys.argv[sys.argv.index('--port') + 1])\n"
        "    sock = socket.socket()\n"
        "    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
        "    sock.bind(('127.0.0.1', port))\n"
        "    sock.listen()\n"
        "while True:\n"
        "    time.sleep(1)\n"
    )
    python = Path(sys.executable)
    (root / ".venv/bin/python").write_text(
        "#!/bin/sh\n"
        f"case \"$1\" in *detached_exec.py) exec {python} \"$@\";; esac\n"
        f"if [ \"$1\" = \"-m\" ] && [ \"$2\" = \"uvicorn\" ]; then exec {python} {service} api \"$@\"; fi\n"
        f"exec {python} {service} worker \"$@\"\n"
    )
    (root / ".venv/bin/python").chmod(0o755)
    npm = root / "bin/npm"
    failure_arg = " --fail" if frontend_failure else ""
    npm.write_text(
        "#!/bin/sh\n"
        "port=\"\"\n"
        "while [ $# -gt 0 ]; do\n"
        "  if [ \"$1\" = \"--port\" ]; then shift; port=\"$1\"; fi\n"
        "  shift\n"
        "done\n"
        f"exec {python} {service} frontend --port \"$port\"{failure_arg}\n"
    )
    npm.chmod(0o755)
    api_port = _free_port()
    frontend_port = _free_port()
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{root / 'bin'}:{env['PATH']}",
            "TRUECRIME_DATABASE_URL": "sqlite:///./truecrime.db",
            "TRUECRIME_SEARXNG_URL": "http://127.0.0.1:1",
            "SEARXNG_DIR": "",
            "TRUECRIME_API_PORT": str(api_port),
            "TRUECRIME_FRONTEND_PORT": str(frontend_port),
        }
    )
    return root, env


def _stop_fake_stack(root: Path, env: dict[str, str]) -> None:
    subprocess.run(
        ["bash", str(root / "scripts/dev_down.sh")],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _native_search_root(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    root, env = _fake_dev_root(tmp_path)
    (root / "config/searxng").mkdir(parents=True)
    shutil.copy2(ROOT / "config/searxng/settings.yml.template",
                 root / "config/searxng/settings.yml.template")
    shutil.copy2(ROOT / "scripts/searxng_up.sh", root / "scripts/searxng_up.sh")
    (root / "scripts/searxng_up.sh").chmod(0o755)
    port = _free_port()
    runner = root / "fake_searxng"
    runner.write_text(
        "#!/bin/sh\n"
        f"exec {sys.executable} {root / 'fake_searxng.py'}\n"
    )
    runner.chmod(0o755)
    (root / "fake_searxng.py").write_text(
        "from http.server import BaseHTTPRequestHandler, HTTPServer\n"
        "from urllib.parse import urlparse\n"
        "import os\n"
        "\n"
        "class Handler(BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        if urlparse(self.path).path != '/search':\n"
        "            self.send_response(404)\n"
        "            self.end_headers()\n"
        "            return\n"
        "        body = b'{\"results\": []}'\n"
        "        self.send_response(200)\n"
        "        self.send_header('Content-Type', 'application/json')\n"
        "        self.send_header('Content-Length', str(len(body)))\n"
        "        self.end_headers()\n"
        "        self.wfile.write(body)\n"
        "    def log_message(self, *_args):\n"
        "        pass\n"
        "\n"
        "port = int(urlparse(os.environ['TRUECRIME_SEARXNG_URL']).port)\n"
        "HTTPServer(('127.0.0.1', port), Handler).serve_forever()\n"
    )
    env.update({
        "TRUECRIME_SEARXNG_URL": f"http://127.0.0.1:{port}",
        "SEARXNG_RUNNER": str(runner),
        "SEARXNG_DIR": "",
    })
    return root, env, runner


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


def test_dev_up_stops_stale_same_directory_next_server(tmp_path):
    root, env = _fake_dev_root(tmp_path)
    next_server = root / "frontend/next-server"
    next_server.write_text(
        "#!/bin/sh\n"
        "trap 'kill $child 2>/dev/null; exit 0' TERM INT\n"
        "sleep 60 & child=$!\n"
        "wait $child\n"
    )
    next_server.chmod(0o755)
    stale = subprocess.Popen([str(next_server)], cwd=root / "frontend")
    try:
        time.sleep(0.2)
        result = subprocess.run(
            ["bash", str(root / "scripts/dev_up.sh")],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        assert result.returncode == 0, result.stderr
        assert f"stopped stale frontend pid {stale.pid}" in result.stdout
        assert stale.poll() is not None
        assert "Local services started" in result.stdout
        assert "dev_up.sh --status" in result.stdout
    finally:
        if stale.poll() is None:
            stale.terminate()
            stale.wait(timeout=5)
        _stop_fake_stack(root, env)


def test_dev_up_does_not_stop_foreign_directory_next_server(tmp_path):
    root, env = _fake_dev_root(tmp_path)
    foreign = tmp_path / "foreign/frontend"
    foreign.mkdir(parents=True)
    next_server = foreign / "next-server"
    next_server.write_text("#!/bin/sh\nsleep 60\n")
    next_server.chmod(0o755)
    foreign_server = subprocess.Popen([str(next_server)], cwd=foreign)
    try:
        time.sleep(0.2)
        result = subprocess.run(
            ["bash", str(root / "scripts/dev_up.sh")],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        assert result.returncode == 0, result.stderr
        assert f"stopped stale frontend pid {foreign_server.pid}" not in result.stdout
        assert foreign_server.poll() is None
    finally:
        if foreign_server.poll() is None:
            foreign_server.send_signal(signal.SIGTERM)
            foreign_server.wait(timeout=5)
        _stop_fake_stack(root, env)


def test_frontend_failure_is_reported_by_status_and_dev_up_fails(tmp_path):
    root, env = _fake_dev_root(tmp_path, frontend_failure=True)
    try:
        result = subprocess.run(
            ["bash", str(root / "scripts/dev_up.sh")],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "Services failed after readiness verification: frontend" in combined
        assert "Local services started" not in combined

        status = subprocess.run(
            ["bash", str(root / "scripts/dev_up.sh"), "--status"],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert status.returncode == 0
        assert "frontend: stopped" in status.stdout
        assert "last error: FATAL frontend failed to bind" in status.stdout
    finally:
        _stop_fake_stack(root, env)


def test_detached_fake_services_survive_launcher_exit_and_group_kill(tmp_path):
    root, env = _fake_dev_root(tmp_path)
    result = subprocess.run(
        ["bash", str(root / "scripts/dev_up.sh")],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    pidfiles = [root / "data/dev" / f"{name}.pid" for name in ("api", "worker", "frontend")]
    pids = [int(path.read_text()) for path in pidfiles]
    try:
        assert all(subprocess.run(["kill", "-0", str(pid)], check=False).returncode == 0
                   for pid in pids)
        for pid in pids:
            assert os.getsid(pid) == pid
    finally:
        _stop_fake_stack(root, env)

    # A separate harmless service proves that killing the launcher's process
    # group does not reach a service that detached into its own session.
    service = root / "survivor.sh"
    survivor_pid = root / "survivor.pid"
    service.write_text(
        f"#!/bin/sh\necho $$ > {survivor_pid}\ntrap 'exit 0' TERM INT\nsleep 60\n"
    )
    service.chmod(0o755)
    launcher = subprocess.Popen(
        ["bash", "-c", f"{sys.executable} {root / 'scripts/detached_exec.py'} {service}; sleep 60"],
        cwd=root,
        start_new_session=True,
    )
    try:
        for _ in range(20):
            if survivor_pid.exists():
                break
            time.sleep(0.1)
        child_pid = int(survivor_pid.read_text())
        os.killpg(launcher.pid, signal.SIGTERM)
        launcher.wait(timeout=5)
        assert launcher.returncode != 0
        assert subprocess.run(["kill", "-0", str(child_pid)], check=False).returncode == 0
    finally:
        if survivor_pid.exists():
            child_pid = int(survivor_pid.read_text())
            os.kill(child_pid, signal.SIGTERM)


def test_native_searxng_runner_is_recorded_after_health_and_stopped_by_dev_down(tmp_path):
    root, env, _runner = _native_search_root(tmp_path)
    try:
        result = subprocess.run(
            ["bash", str(root / "scripts/dev_up.sh")],
            cwd=root, env=env, capture_output=True, text=True, check=False, timeout=20,
        )
        assert result.returncode == 0, result.stderr
        assert "SearXNG is healthy" in result.stdout
        pidfile = root / "data/searxng/searxng.pid"
        assert pidfile.exists()
        search_pid = int(pidfile.read_text())
        assert subprocess.run(["kill", "-0", str(search_pid)], check=False).returncode == 0

        down = subprocess.run(
            ["bash", str(root / "scripts/dev_down.sh")],
            cwd=root, env=env, capture_output=True, text=True, check=False,
        )
        assert down.returncode == 0
        assert subprocess.run(["kill", "-0", str(search_pid)], check=False).returncode != 0
        assert not pidfile.exists()
    finally:
        _stop_fake_stack(root, env)


def test_dev_down_never_stops_external_healthy_searxng(tmp_path):
    root, env, runner = _native_search_root(tmp_path)
    env["TRUECRIME_SEARXNG_URL"] = f"http://127.0.0.1:{_free_port()}"
    external = subprocess.Popen([str(runner)], cwd=root, env=env)
    try:
        time.sleep(0.4)
        assert subprocess.run(["curl", "-fsS", f"{env['TRUECRIME_SEARXNG_URL']}/search?q=test&format=json"],
                              check=False, stdout=subprocess.DEVNULL).returncode == 0
        down = subprocess.run(
            ["bash", str(root / "scripts/dev_down.sh")],
            cwd=root, env=env, capture_output=True, text=True, check=False,
        )
        assert down.returncode == 0
        assert external.poll() is None
    finally:
        if external.poll() is None:
            external.terminate()
            external.wait(timeout=5)


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
