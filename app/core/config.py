from pydantic_settings import BaseSettings


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class Settings(BaseSettings):
    DB_USER: str
    DB_PASSWORD: str
    DB_HOST: str
    DB_PORT: int = 3306
    DB_NAME: str
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"
    AUTO_SEED: bool = True

    @property
    def cors_origins_list(self) -> list[str]:
        return _split_csv(self.CORS_ORIGINS)

    class Config:
        env_file = ".env"


settings = Settings()
