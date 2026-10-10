from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./truecrime.db"

    youtube_api_key: str = ""

    # Research runs entirely through the TrueCrime Search Engine;
    # no LLM vendor performs web discovery. Devin/OpenRouter removed from
    # active workflows. Historical ResearchJob rows may still carry
    # provider="devin"/"openrouter" as readable data.
    research_provider: str = "truecrime"

    # Where documentary jobs run:
    #   "inline"   inside the API process (default; restarting the API stops running jobs)
    #   "external" in a separate worker (`python -m app.worker`); the API only queues them
    job_runner: str = "inline"
    worker_poll_seconds: float = 2.0
    # A running job whose worker has not reported for this long is considered dead.
    job_lease_seconds: float = 120.0
    # A job found dead is queued again automatically at most this many times (finished stages are reused).
    job_max_auto_resumes: int = 2

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
