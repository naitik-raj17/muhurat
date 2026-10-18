"""Wires port interfaces to implementations. Mocks are the default; real providers switch on
only when their credentials are present in the environment."""

from __future__ import annotations

from functools import lru_cache

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.embeddings.hash_embedder import HashEmbedder
from app.adapters.llm.mock import MockLLM
from app.adapters.llm.mock_handlers import register_all
from app.adapters.messaging.mock import MockChannel
from app.adapters.payments.mock import MockPayments
from app.adapters.storage.memory import MemoryStorage
from app.adapters.travel.mock import MockTravelTime
from app.adapters.vector.pgvector_store import PgVectorStore
from app.core.config import get_models_config, get_settings
from app.core.logging import get_logger
from app.ports.embeddings import Embedder
from app.ports.llm import LLMClient
from app.ports.messaging import MessagingChannel
from app.ports.payments import PaymentProvider
from app.ports.storage import ObjectStorage
from app.ports.travel import TravelTimeProvider
from app.ports.vector import VectorStore

log = get_logger(__name__)
register_all()


@lru_cache
def get_storage() -> ObjectStorage:
    s = get_settings()
    if s.storage_backend == "memory":
        return MemoryStorage()
    from app.adapters.storage.minio_storage import MinioStorage

    return MinioStorage(
        s.minio_endpoint, s.minio_access_key, s.minio_secret_key, s.minio_bucket, s.minio_secure
    )


@lru_cache
def get_llm() -> LLMClient:
    s = get_settings()
    if s.openrouter_api_key:
        from app.adapters.llm.openrouter import OpenRouterLLM

        log.info("llm_provider", provider="openrouter")
        return OpenRouterLLM(s.openrouter_api_key, s.openrouter_base_url)
    return MockLLM()


@lru_cache
def get_embedder() -> Embedder:
    s = get_settings()
    spec = get_models_config().embeddings
    if s.embedding_provider == "hash":
        return HashEmbedder(spec.dimensions)
    from app.adapters.embeddings.sentence_transformer import SentenceTransformerEmbedder

    return SentenceTransformerEmbedder(spec.model, spec.dimensions)


def get_vector_store(session: AsyncSession) -> VectorStore:
    s = get_settings()
    if s.pinecone_api_key:
        return _pinecone()
    return PgVectorStore(session)


@lru_cache
def _pinecone() -> VectorStore:
    from app.adapters.vector.pinecone_store import PineconeStore

    s = get_settings()
    return PineconeStore(s.pinecone_api_key, s.pinecone_index)


@lru_cache
def get_channels() -> dict[str, MessagingChannel]:
    s = get_settings()
    channels: dict[str, MessagingChannel] = {
        "whatsapp": MockChannel("whatsapp"),
        "email": MockChannel("email"),
    }
    if s.whatsapp_access_token and s.whatsapp_phone_number_id:
        from app.adapters.messaging.whatsapp import WhatsAppCloudChannel

        channels["whatsapp"] = WhatsAppCloudChannel(
            s.whatsapp_access_token, s.whatsapp_phone_number_id
        )
    if s.gmail_smtp_user and s.gmail_smtp_app_password:
        from app.adapters.messaging.gmail_smtp import GmailSmtpChannel

        channels["email"] = GmailSmtpChannel(s.gmail_smtp_user, s.gmail_smtp_app_password)
    return channels


@lru_cache
def get_payments() -> PaymentProvider:
    # Razorpay is not wired in this build: payments are Tier 3 (humans only) and mocked.
    return MockPayments()


@lru_cache
def get_travel() -> TravelTimeProvider:
    return MockTravelTime()


@lru_cache
def get_redis() -> Redis:
    return Redis.from_url(get_settings().redis_url, decode_responses=True)


def provider_summary() -> dict[str, str]:
    """Which implementation backs each port (shown in health/ready and the README)."""
    s = get_settings()
    return {
        "llm": "openrouter" if s.openrouter_api_key else "mock",
        "embeddings": s.embedding_provider,
        "vector_store": "pinecone" if s.pinecone_api_key else "pgvector",
        "whatsapp": "cloud_api" if s.whatsapp_access_token else "mock",
        "email": "gmail_smtp" if s.gmail_smtp_user else "mock",
        "storage": s.storage_backend,
        "payments": "mock",
    }
