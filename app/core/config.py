from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    supabase_db_url: str = ""
    storage_bucket: str = "student-documents"
    allowed_origins: str = "http://localhost:3000,http://127.0.0.1:5500"
    max_upload_bytes: int = 15_000_000
    ocr_min_confidence: float = 0.55
    enable_persistence: bool = False

    @property
    def origins(self) -> list[str]:
        return [x.strip() for x in self.allowed_origins.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
