from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./truecrime.db"

    youtube_api_key: str = ""

    # Research runs entirely through the TrueCrime Search Engine;
    # no LLM vendor performs web discovery. Devin/OpenRouter removed from
    # active workflows. Historical ResearchJob rows may still carry
    # provider="devin"/"openrouter" as readable data.
    research_provider: str = "truecrime"

    # --- security -------------------------------------------------------------------------
    # One shared access token for the single operator. Empty = no login, and the API then only
    # answers requests that come from this machine (loopback). Set it before exposing the API
    # to anything else (Docker, LAN, tunnel). Generate one: python -c "import secrets;print(secrets.token_urlsafe(32))"
    access_token: str = ""
    session_days: int = 14
    # Browser origins allowed to call the API (comma separated).
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    # Send the session cookie only over HTTPS (turn on behind a TLS proxy).
    cookie_secure: bool = False

    # Where documentary jobs run:
    #   "inline"   inside the API process (default; restarting the API stops running jobs)
    #   "external" in a separate worker (`python -m app.worker`); the API only queues them
    job_runner: str = "inline"
    worker_poll_seconds: float = 2.0
    # A running job whose worker has not reported for this long is considered dead.
    job_lease_seconds: float = 120.0
    # A job found dead is queued again automatically at most this many times (finished stages are reused).
    job_max_auto_resumes: int = 2
    # The worker touches this file every poll; the container health check reads its age.
    worker_alive_file: str = "/tmp/tc-worker-alive"

    # --- observability --------------------------------------------------------------------
    log_level: str = "INFO"
    # "text" for a terminal, "json" for log collectors (one JSON object per line).
    log_format: str = "text"
    # Record every outgoing call to an outside service (host, path, status, time, tokens, cost).
    provider_ledger: bool = True
    provider_ledger_days: int = 90

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
