from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = Field(
        default="sqlite:///./truecrime.db",
        validation_alias=AliasChoices("TRUECRIME_DATABASE_URL", "DATABASE_URL"),
    )

    youtube_api_key: str = Field(
        default="",
        validation_alias=AliasChoices(
            "TRUECRIME_YOUTUBE_API_KEY", "TrueCrime_YOUTUBE_API_KEY", "YOUTUBE_API_KEY"
        ),
    )

    # Research runs entirely through the TrueCrime Search Engine;
    # no LLM vendor performs web discovery. Historical provider strings
    # remain readable in stored rows but are not active workflows.
    research_provider: str = "truecrime"

    # --- security -------------------------------------------------------------------------
    # One shared access token for the single operator. Empty = no login, and the API then only
    # answers requests that come from this machine (loopback). Set it before exposing the API
    # to anything else (Docker, LAN, tunnel). Generate one: python -c "import secrets;print(secrets.token_urlsafe(32))"
    access_token: str = Field(
        default="", validation_alias=AliasChoices("TRUECRIME_ACCESS_TOKEN", "ACCESS_TOKEN")
    )
    session_days: int = Field(
        default=14, validation_alias=AliasChoices("TRUECRIME_SESSION_DAYS", "SESSION_DAYS")
    )
    # Browser origins allowed to call the API (comma separated).
    cors_origins: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000",
        validation_alias=AliasChoices("TRUECRIME_CORS_ORIGINS", "CORS_ORIGINS"),
    )
    # Send the session cookie only over HTTPS (turn on behind a TLS proxy).
    cookie_secure: bool = Field(
        default=False, validation_alias=AliasChoices("TRUECRIME_COOKIE_SECURE", "COOKIE_SECURE")
    )

    # Where documentary jobs run:
    #   "inline"   inside the API process (default; restarting the API stops running jobs)
    #   "external" in a separate worker (`python -m app.worker`); the API only queues them
    job_runner: str = Field(
        default="inline", validation_alias=AliasChoices("TRUECRIME_JOB_RUNNER", "JOB_RUNNER")
    )
    worker_poll_seconds: float = Field(
        default=2.0,
        validation_alias=AliasChoices("TRUECRIME_WORKER_POLL_SECONDS", "WORKER_POLL_SECONDS"),
    )
    # A running job whose worker has not reported for this long is considered dead.
    job_lease_seconds: float = Field(
        default=120.0,
        validation_alias=AliasChoices("TRUECRIME_JOB_LEASE_SECONDS", "JOB_LEASE_SECONDS"),
    )
    # A job found dead is queued again automatically at most this many times (finished stages are reused).
    job_max_auto_resumes: int = Field(
        default=2,
        validation_alias=AliasChoices("TRUECRIME_JOB_MAX_AUTO_RESUMES", "JOB_MAX_AUTO_RESUMES"),
    )
    # The worker touches this file every poll; the container health check reads its age.
    worker_alive_file: str = Field(
        default="/tmp/tc-worker-alive",
        validation_alias=AliasChoices("TRUECRIME_WORKER_ALIVE_FILE", "WORKER_ALIVE_FILE"),
    )

    # --- observability --------------------------------------------------------------------
    log_level: str = Field(
        default="INFO", validation_alias=AliasChoices("TRUECRIME_LOG_LEVEL", "LOG_LEVEL")
    )
    # "text" for a terminal, "json" for log collectors (one JSON object per line).
    log_format: str = Field(
        default="text", validation_alias=AliasChoices("TRUECRIME_LOG_FORMAT", "LOG_FORMAT")
    )
    # Record every outgoing call to an outside service (host, path, status, time, tokens, cost).
    provider_ledger: bool = Field(
        default=True,
        validation_alias=AliasChoices("TRUECRIME_PROVIDER_LEDGER", "PROVIDER_LEDGER"),
    )
    provider_ledger_days: int = Field(
        default=90,
        validation_alias=AliasChoices(
            "TRUECRIME_PROVIDER_LEDGER_DAYS", "PROVIDER_LEDGER_DAYS"
        ),
    )

    @model_validator(mode="after")
    def _warn_legacy_environment(self):
        from app.core.env import ENV_ALIASES, env_value

        for canonical in ENV_ALIASES:
            env_value(canonical)
        return self

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
