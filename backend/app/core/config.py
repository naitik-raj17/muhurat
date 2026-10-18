"""Application settings (environment) and YAML configuration (models, policy, templates)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEV_JWT_SECRET = "dev-only-change-me-dev-only-change-me-dev-only"  # noqa: S105
_DEV_PII_KEY = "OgletkQ1VqIM0qMckEAS5U-inbQvCGA1EZS54BOEFnA="
_DEV_HASH_KEY = "dev-only-hash-key-change-me"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(_REPO_ROOT / ".env", ".env"), extra="ignore", case_sensitive=False
    )

    app_env: Literal["dev", "test", "production"] = "dev"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:5173"
    config_dir: Path = Field(default=_REPO_ROOT / "config")

    database_url: str = "postgresql+asyncpg://wedding:wedding@localhost:5432/wedding"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret: str = _DEV_JWT_SECRET
    jwt_expires_minutes: int = 480
    rate_limit_per_minute: int = 300

    pii_encryption_key: str = _DEV_PII_KEY
    pii_hash_key: str = _DEV_HASH_KEY

    storage_backend: Literal["minio", "memory"] = "minio"
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"  # noqa: S105
    minio_bucket: str = "wedding-files"
    minio_secure: bool = False

    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    embedding_provider: Literal["sentence_transformers", "hash"] = "sentence_transformers"
    pinecone_api_key: str = ""
    pinecone_index: str = "wedding-vendors"

    whatsapp_access_token: str = ""
    whatsapp_phone_number_id: str = ""
    whatsapp_app_secret: str = ""
    gmail_smtp_user: str = ""
    gmail_smtp_app_password: str = ""
    mock_webhook_secret: str = "dev-only-mock-webhook-secret"  # noqa: S105
    # Echoed back during Meta's webhook subscription handshake (GET /webhooks/whatsapp).
    whatsapp_verify_token: str = ""

    google_calendar_credentials_file: str = ""
    razorpay_key_id: str = ""
    razorpay_key_secret: str = ""

    # The ONE job system in use: "inprocess" (FastAPI background tasks + the in-process outbox
    # worker, Phase 2) or "temporal" (Phase 3: Temporal runs the outbox drain, reminders and
    # the per-wedding workflows). Never both at once.
    job_backend: Literal["inprocess", "temporal"] = "inprocess"
    temporal_host: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "wedding-workflows"
    # How often each wedding's workflow runs the agents (a nudge signal runs them sooner).
    wedding_workflow_interval_minutes: int = 360
    # The Temporal worker serves its own Prometheus metrics (agent actions, outbox) here.
    worker_metrics_port: int = 9100

    sentry_dsn: str = ""

    # In-process outbox dispatcher (job_backend=inprocess only). Tests dispatch explicitly.
    outbox_worker_enabled: bool = True
    outbox_poll_seconds: float = 2.0

    @model_validator(mode="after")
    def _no_dev_secrets_in_production(self) -> Settings:
        if self.app_env == "production":
            dev_values = {
                "JWT_SECRET": (self.jwt_secret, _DEV_JWT_SECRET),
                "PII_ENCRYPTION_KEY": (self.pii_encryption_key, _DEV_PII_KEY),
                "PII_HASH_KEY": (self.pii_hash_key, _DEV_HASH_KEY),
            }
            bad = [name for name, (value, dev) in dev_values.items() if value == dev]
            if bad:
                raise ValueError(f"Refusing to start in production with dev secrets: {bad}")
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


# --------------------------------------------------------------------------- YAML config


class ModelSpec(BaseModel):
    id: str
    input_usd_per_mtok: float
    output_usd_per_mtok: float


class EmbeddingSpec(BaseModel):
    model: str
    dimensions: int
    # Vector hits below this cosine similarity are not considered relevant.
    min_similarity: float = 0.2


class ModelsConfig(BaseModel):
    usd_to_inr: float
    models: dict[str, ModelSpec]
    tasks: dict[str, str]
    fallback_tier: str
    embeddings: EmbeddingSpec

    def spec_for_task(self, task: str) -> ModelSpec:
        tier = self.tasks.get(task, self.tasks["general"])
        return self.models[tier]


class Tier2Limits(BaseModel):
    max_reschedule_shift_hours: float
    max_price_change_fraction: float


class TokenBudgetConfig(BaseModel):
    monthly_tokens_per_user: int
    warn_fraction: float
    throttle_fraction: float


class MessagingConfig(BaseModel):
    timezone: str
    quiet_hours_start: str
    quiet_hours_end: str


class ReminderConfig(BaseModel):
    days_without_reply: int
    max_reminders: int


class BudgetConfig(BaseModel):
    alert_threshold_fraction: float


class OutboxConfig(BaseModel):
    max_attempts: int
    base_backoff_seconds: float
    max_backoff_seconds: float


class AgentsConfig(BaseModel):
    min_confidence: float


class PolicyConfig(BaseModel):
    tier2_limits: Tier2Limits
    token_budget: TokenBudgetConfig
    messaging: MessagingConfig
    reminders: ReminderConfig
    budget: BudgetConfig
    outbox: OutboxConfig
    agents: AgentsConfig


def _load_yaml(name: str) -> dict[str, Any]:
    path = Path(os.environ.get("CONFIG_DIR", get_settings().config_dir)) / name
    with path.open(encoding="utf-8") as fh:
        data: dict[str, Any] = yaml.safe_load(fh)
    return data


@lru_cache
def get_models_config() -> ModelsConfig:
    return ModelsConfig.model_validate(_load_yaml("models.yaml"))


@lru_cache
def get_policy_config() -> PolicyConfig:
    return PolicyConfig.model_validate(_load_yaml("policy.yaml"))


@lru_cache
def get_timeline_templates() -> dict[str, Any]:
    return _load_yaml("timeline_templates.yaml")


@lru_cache
def get_message_templates() -> dict[str, Any]:
    templates: dict[str, Any] = _load_yaml("message_templates.yaml")["templates"]
    return templates
