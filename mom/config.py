from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class CEnv(BaseSettings):
    model_config = SettingsConfigDict(env_file=Path(__file__).parent.parent / ".env", extra="ignore")

    MOM_PORT: int = 6541
    MOM_LOG_FILE: Path = Path("logs/mom.log")
    MOM_LOG_LEVEL: str = "INFO"
    MOM_HEARTBEAT_MINS: float = 5.0

    OPENAI_API_KEY: str
    MODEL: str = "openai:gpt-4.1-nano"
    MODEL_CTX_SIZE: int = 1_000_000

    POLL_SECS: float = 0.8

    DEFAULT_WAIT_SEC: float = 10.0
    IDLE_THRESHOLD: float = 3.0
    IDLE_SPIN_POLL_SECS: float = 0.2


def get_env() -> CEnv:
    """Get environment configuration (useful for testing)"""
    return CEnv() # type: ignore[reportCallIssue]

c_env = get_env()

if __name__ == "__main__":
    print(c_env)
