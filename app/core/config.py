from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "sqlite:///./truecrime.db"

    youtube_api_key: str = ""

    # Research runs entirely through the TrueCrime Search Engine;
    # no LLM vendor performs web discovery. Devin/OpenRouter removed from
    # active workflows. Historical ResearchJob rows may still carry
    # provider="devin"/"openrouter" as readable data.
    research_provider: str = "truecrime"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
