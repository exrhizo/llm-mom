from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class CEnv(BaseSettings):
    model_config = SettingsConfigDict(env_file=Path(".env"), extra="ignore")

    OPENAI_API_KEY: str
    MODEL: str = "openai:gpt-4.1-nano"

    POLL_SECS: float = 0.8
    TAIL_LINES: int = 160
    MAX_TRANSCRIPT: int = 200

    DEFAULT_WAIT_SEC: float = 10.0
    IDLE_THRESHOLD: float = 3.0
    IDLE_SPIN_POLL_SECS: float = 0.2
    INJECT_PRESS_ENTER: bool = True


def get_env() -> CEnv:
    """Get environment configuration (useful for testing)"""
    return CEnv() # type: ignore[reportCallIssue]

c_env = get_env()

if __name__ == "__main__":
    print(c_env)
