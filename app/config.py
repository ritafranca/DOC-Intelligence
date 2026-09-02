from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=False)


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "DOC Intelligence")
    environment: str = os.getenv("ENVIRONMENT", "development")
    testing: bool = _as_bool(os.getenv("TESTING"), False)
    demo_autoprocess: bool = _as_bool(os.getenv("DEMO_AUTOPROCESS"), False)

    database_url: str = os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://docintelligence:docintelligence@127.0.0.1:5432/docintelligence",
    )
    database_pool_size: int = int(os.getenv("DATABASE_POOL_SIZE", "10"))
    database_max_overflow: int = int(os.getenv("DATABASE_MAX_OVERFLOW", "20"))

    redis_url: str = os.getenv("REDIS_URL", "redis://:docintelligence@127.0.0.1:6379/0")
    queue_name: str = os.getenv("QUEUE_NAME", "doc-intelligence")
    worker_concurrency: int = int(os.getenv("WORKER_CONCURRENCY", "8"))
    max_processing_attempts: int = int(os.getenv("MAX_PROCESSING_ATTEMPTS", "3"))

    s3_endpoint_url: str | None = os.getenv("S3_ENDPOINT_URL", "http://127.0.0.1:9000")
    s3_region: str = os.getenv("S3_REGION", "us-east-1")
    s3_bucket: str = os.getenv("S3_BUCKET", "doc-intelligence")
    s3_access_key_id: str | None = os.getenv("S3_ACCESS_KEY_ID", "docintelligence")
    s3_secret_access_key: str | None = os.getenv("S3_SECRET_ACCESS_KEY", "docintelligence-secret")
    s3_sse_algorithm: str = os.getenv("S3_SSE_ALGORITHM", "AES256")
    s3_sse_kms_key_id: str | None = os.getenv("S3_SSE_KMS_KEY_ID")
    s3_auto_create_bucket: bool = _as_bool(os.getenv("S3_AUTO_CREATE_BUCKET"), True)

    auth_disabled: bool = _as_bool(os.getenv("AUTH_DISABLED"), False)
    auth_provider: str = os.getenv("AUTH_PROVIDER", "local").strip().lower()
    jwt_secret_key: str = os.getenv("JWT_SECRET_KEY", "dev-only-change-this-jwt-secret-now")
    jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")
    jwt_access_token_minutes: int = int(os.getenv("JWT_ACCESS_TOKEN_MINUTES", "480"))
    jwt_issuer: str = os.getenv("JWT_ISSUER", "doc-intelligence")
    jwt_audience: str = os.getenv("JWT_AUDIENCE", "doc-intelligence-spa")
    default_admin_name: str = os.getenv("DEFAULT_ADMIN_NAME", "Administrador")
    default_admin_email: str = os.getenv("DEFAULT_ADMIN_EMAIL", "admin@doc.local")
    default_admin_password: str = os.getenv("DEFAULT_ADMIN_PASSWORD", "admin")
    oidc_issuer: str = os.getenv("OIDC_ISSUER", "http://127.0.0.1:8080/realms/doc-intelligence")
    oidc_audience: str = os.getenv("OIDC_AUDIENCE", "doc-intelligence-api")
    oidc_client_id: str = os.getenv("OIDC_CLIENT_ID", "doc-intelligence-spa")
    oidc_scopes: str = os.getenv("OIDC_SCOPES", "openid profile email roles")
    oidc_algorithms: tuple[str, ...] = tuple(
        value.strip() for value in os.getenv("OIDC_ALGORITHMS", "RS256").split(",") if value.strip()
    )

    data_dir: Path = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data"))).resolve()
    max_upload_bytes: int = int(os.getenv("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))
    max_pdf_pages: int = int(os.getenv("MAX_PDF_PAGES", "40"))
    review_threshold: float = float(os.getenv("REVIEW_THRESHOLD", "0.85"))
    review_lease_seconds: int = int(os.getenv("REVIEW_LEASE_SECONDS", "600"))
    retention_days: int = int(os.getenv("RETENTION_DAYS", "365"))
    audit_hmac_key: str = os.getenv("AUDIT_HMAC_KEY", "dev-only-change-this-audit-key")

    extractor_strategy: str = os.getenv("EXTRACTOR_STRATEGY", "local")
    local_ocr_lang: str = os.getenv("LOCAL_OCR_LANG", "pt")
    local_ocr_cpu_threads: int = int(os.getenv("LOCAL_OCR_CPU_THREADS", "4"))
    local_ocr_executor_workers: int = int(os.getenv("LOCAL_OCR_EXECUTOR_WORKERS", "1"))
    local_ocr_poppler_path: str | None = os.getenv("LOCAL_OCR_POPPLER_PATH") or None
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY")
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o")
    openai_timeout_seconds: float = float(os.getenv("OPENAI_TIMEOUT_SECONDS", "50"))
    openai_max_retries: int = int(os.getenv("OPENAI_MAX_RETRIES", "2"))
    provider_url: str | None = os.getenv("LLM_PROVIDER_URL")
    provider_api_key: str | None = os.getenv("LLM_PROVIDER_API_KEY")
    provider_model: str = os.getenv("LLM_PROVIDER_MODEL", "vision-model-v1")
    provider_timeout_seconds: float = float(os.getenv("LLM_PROVIDER_TIMEOUT_SECONDS", "50"))
    prompt_version: str = os.getenv("PROMPT_VERSION", "document_extraction_v1")

    @property
    def temp_dir(self) -> Path:
        return self.data_dir / "tmp"

    def validate_runtime(self) -> None:
        if self.auth_provider not in {"local", "oidc"}:
            raise RuntimeError("AUTH_PROVIDER deve ser local ou oidc.")
        if self.auth_provider == "local" and self.jwt_algorithm != "HS256":
            raise RuntimeError("A autenticação local suporta somente JWT_ALGORITHM=HS256.")
        if self.extractor_strategy == "openai" and not self.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY é obrigatória quando EXTRACTOR_STRATEGY=openai.")
        if self.environment == "production":
            if not self.database_url.startswith("postgresql+asyncpg://"):
                raise RuntimeError("Produção exige DATABASE_URL postgresql+asyncpg.")
            if "ssl=require" not in self.database_url:
                raise RuntimeError("Produção exige TLS no PostgreSQL (ssl=require).")
            if not self.redis_url.startswith("rediss://"):
                raise RuntimeError("Produção exige Redis com TLS (rediss://).")
            if self.auth_disabled:
                raise RuntimeError("AUTH_DISABLED não pode ser usado em produção.")
            if self.auth_provider != "oidc":
                raise RuntimeError("Produção exige AUTH_PROVIDER=oidc.")
            if not self.oidc_issuer.startswith("https://"):
                raise RuntimeError("Produção exige OIDC_ISSUER com HTTPS.")
            if len(self.audit_hmac_key) < 32 or self.audit_hmac_key.startswith("dev-only"):
                raise RuntimeError("AUDIT_HMAC_KEY forte é obrigatório em produção.")
            if self.s3_sse_algorithm != "aws:kms" or not self.s3_sse_kms_key_id:
                raise RuntimeError("Produção exige S3_SSE_ALGORITHM=aws:kms e uma chave KMS.")
            if self.s3_auto_create_bucket:
                raise RuntimeError("S3_AUTO_CREATE_BUCKET deve ser false em produção.")
            if self.s3_endpoint_url and not self.s3_endpoint_url.startswith("https://"):
                raise RuntimeError("Produção exige endpoint S3 HTTPS.")
        if self.database_url.startswith("sqlite") and not self.testing:
            raise RuntimeError("SQLite foi removido do runtime; use PostgreSQL ou TESTING=true.")


settings = Settings()
