from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CEnv(BaseSettings):
    model_config = SettingsConfigDict(env_file=Path(".env"), env_prefix="MOM_", extra="ignore")

    OPENAI_API_KEY: str  # loaded from .env or shell
    MODEL: str = Field("openai:gpt-4o", alias="MODEL")
    TMUX_SESSION: str = "llm-mom"
    TMUX_WINDOW: str = "main"

    POLL_SECS: float = 0.8
    TAIL_LINES: int = 160
    MAX_TRANSCRIPT: int = 200


c_env = CEnv() # type: ignore[reportCallIssue]
