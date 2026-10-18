"""Vendor discovery: search with explanations, shortlist, human-only final selection."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import cast, func, literal, select
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.factory import get_embedder, get_vector_store
from app.core.config import get_models_config
from app.db.models import Vendor, VendorAvailability, VendorShortlist
from app.db.session import session_org
from app.domain.enums import AvailabilityStatus, ShortlistStatus, VendorCategory
from app.domain.errors import Conflict, Forbidden, ValidationFailed
from app.domain.pricing import benchmark, typical_price
from app.domain.rbac import Permission, Principal
from app.domain.schemas import (
    MatchExplanation,
    PriceBenchmarkOut,
    ShortlistAdd,
    ShortlistDecision,
    ShortlistOut,
    VendorCreate,
    VendorMatch,
    VendorOut,
    VendorSearchQuery,
    VendorSearchResult,
)
from app.domain.vendor_matching import (
    Candidate,
    Constraints,
    hard_filter_reason,
    normalise,
    reciprocal_rank_fusion,
    score_candidate,
    tokenize,
)
from app.ports.vector import VectorQuery, VectorRecord
from app.repositories.repos import (
    ShortlistRepository,
    VendorAvailabilityRepository,
    VendorRepository,
    WeddingRepository,
)

UNAVAILABLE = (AvailabilityStatus.BOOKED.value, AvailabilityStatus.BLOCKED.value)


def build_search_text(v: Vendor) -> str:
    return " ".join(
        [v.name, v.category.replace("_", " "), v.city, " ".join(v.styles), v.description]
    )


def _candidate(v: Vendor) -> Candidate:
    return Candidate(
        id=str(v.id),
        name=v.name,
        category=v.category,
        city=v.city,
        price_min=v.price_min,
        price_max=v.price_max,
        styles=v.styles,
        languages=v.languages,
        rating=v.rating,
        review_count=v.review_count,
        search_text=v.search_text,
    )


async def keyword_ranking(
    session: AsyncSession, repo: VendorRepository, query_tokens: list[str], prefilter: list[Any]
) -> dict[str, float]:
    """Postgres full-text (BM25-like ts_rank_cd) over the 'simple' config. Terms are OR-ed so a
    query like "royal rajasthani decor" still matches vendors that have only some of the words."""
    if not query_tokens:
        return {}
    terms = " | ".join(sorted(set(query_tokens)))  # tokens are \w-only, so this is safe syntax
    tsquery = func.to_tsquery(cast(literal("simple"), REGCONFIG), literal(terms))
    rank = func.ts_rank_cd(Vendor.search_vector, tsquery)
    stmt = (
        select(Vendor.id, rank)
        .where(repo.scope_filter(), Vendor.search_vector.op("@@")(tsquery), *prefilter)
        .order_by(rank.desc())
        .limit(200)
    )
    rows = (await session.execute(stmt)).all()
    return {str(vid): float(score) for vid, score in rows}


async def unavailable_vendor_ids(
    session: AsyncSession, vendor_ids: list[uuid.UUID], day: Any
) -> frozenset[str]:
    if day is None or not vendor_ids:
        return frozenset()
    repo = VendorAvailabilityRepository(session)
    rows = await repo.list(
        VendorAvailability.vendor_id.in_(vendor_ids),
        VendorAvailability.day == day,
        VendorAvailability.status.in_(UNAVAILABLE),
    )
    return frozenset(str(r.vendor_id) for r in rows)


async def vector_ranking(
    session: AsyncSession, text: str, category: str | None, city: str | None
) -> dict[str, float]:
    """Dense retrieval: cosine similarity of multilingual sentence embeddings."""
    if not text.strip():
        return {}
    org_id = session_org(session)
    assert org_id is not None
    [vector] = await asyncio.to_thread(get_embedder().embed, [text])
    hits = await get_vector_store(session).query(
        VectorQuery(vector=vector, organization_id=org_id, top_k=100, category=category, city=city)
    )
    floor = get_models_config().embeddings.min_similarity
    return {str(vid): sim for vid, sim in hits if sim >= floor}


async def embed_vendors(session: AsyncSession, vendors: list[Vendor]) -> None:
    """(Re)compute embeddings. pgvector keeps them in the vendors table; an external store
    (Pinecone) additionally receives an upsert."""
    if not vendors:
        return
    vectors = await asyncio.to_thread(get_embedder().embed, [v.search_text for v in vendors])
    for v, vec in zip(vendors, vectors, strict=True):
        v.embedding = vec
    store = get_vector_store(session)
    if store.name != "pgvector":
        await store.upsert(
            [
                VectorRecord(v.id, vec, v.organization_id, v.category, v.city)
                for v, vec in zip(vendors, vectors, strict=True)
            ]
        )


async def search(session: AsyncSession, query: VendorSearchQuery) -> VendorSearchResult:
    """Hybrid retrieval -> hard filters (category, city, budget, date) -> score -> explain.

    Retrieval: Postgres full-text (keyword) and dense vectors are ranked separately and fused
    with Reciprocal Rank Fusion, so a vendor described in Hindi can match an English query
    (vectors) and exact terms like "mandap" still count (keywords). Category and city are
    applied in SQL to keep the candidate set small; the matcher re-checks them so the
    explanation and `filtered_out` counts stay honest.
    """
    repo = VendorRepository(session)
    prefilter: list[Any] = []
    if query.category:
        prefilter.append(Vendor.category == query.category.value)
    if query.city:
        prefilter.append(func.lower(Vendor.city) == query.city.strip().lower())

    vendors = await repo.list(*prefilter)
    q_tokens = tokenize(query.q)
    keyword = await keyword_ranking(session, repo, q_tokens, prefilter)
    vector = await vector_ranking(
        session, query.q, query.category.value if query.category else None, query.city
    )
    keyword_order = list(keyword)  # already best-first
    vector_order = sorted(vector, key=lambda k: -vector[k])
    fused = reciprocal_rank_fusion([keyword_order, vector_order])
    relevance = normalise(fused)
    keyword_rank = {vid: i for i, vid in enumerate(keyword_order, start=1)}
    vector_rank = {vid: i for i, vid in enumerate(vector_order, start=1)}

    cons = Constraints(
        category=query.category.value if query.category else None,
        city=query.city,
        budget_max=query.budget_max,
        event_date=query.event_date,
        styles=[s.lower() for s in query.styles],
        language=query.language,
    )
    unavailable = await unavailable_vendor_ids(session, [v.id for v in vendors], query.event_date)

    has_query = bool(query.q.strip())
    filtered_out: dict[str, int] = {}
    matches: list[VendorMatch] = []
    for vendor in vendors:
        vid = str(vendor.id)
        cand = _candidate(vendor)
        reason = hard_filter_reason(cand, cons, unavailable)
        if reason:
            filtered_out[reason] = filtered_out.get(reason, 0) + 1
            continue
        # With a text query, vendors that neither retriever found are not relevant results.
        if has_query and vid not in fused:
            filtered_out["not_relevant"] = filtered_out.get("not_relevant", 0) + 1
            continue
        retrieval: dict[str, float | int | None] = {
            "keyword_rank": keyword_rank.get(vid),
            "vector_rank": vector_rank.get(vid),
            "vector_similarity": round(vector[vid], 4) if vid in vector else None,
            "rrf_score": round(fused.get(vid, 0.0), 5),
        }
        scored = score_candidate(
            cand, cons, q_tokens, relevance.get(vid, 0.0 if has_query else 0.5), retrieval
        )
        matches.append(
            VendorMatch(
                vendor=VendorOut.model_validate(vendor),
                score=scored.score,
                explanation=MatchExplanation(
                    matched_terms=scored.matched_terms,
                    matched_styles=scored.matched_styles,
                    price_fit=scored.price_fit,
                    price_vs_budget_pct=scored.price_vs_budget_pct,
                    availability=scored.availability,
                    rating_note=scored.rating_note,
                    language_match=scored.language_match,
                    retrieval=scored.retrieval,
                    summary=scored.summary,
                ),
            )
        )
    matches.sort(key=lambda m: (-m.score, m.vendor.name))
    return VendorSearchResult(
        query=query,
        mode="hybrid" if has_query else "filters",
        total_candidates=len(vendors),
        filtered_out=filtered_out,
        results=matches[: query.limit],
    )


async def price_benchmark(
    session: AsyncSession, category: VendorCategory, city: str, quote: Decimal | None
) -> PriceBenchmarkOut:
    """p25 / median / p75 of typical prices for a category in a city, and where a quote sits."""
    repo = VendorRepository(session)
    rows = await repo.list(
        Vendor.category == category.value, func.lower(Vendor.city) == city.strip().lower()
    )
    method = "midpoint of each vendor's published price range"
    if len(rows) < 3:
        rows = await repo.list(Vendor.category == category.value)
        method += f" (too few vendors in {city}: all cities used)"
    if not rows:
        raise ValidationFailed(f"No {category.value} vendors to benchmark against")
    b = benchmark(
        [typical_price(v.price_min, v.price_max) for v in rows],
        float(quote) if quote is not None else None,
    )
    return PriceBenchmarkOut(
        category=category,
        city=city,
        sample_size=b.sample_size,
        p25=b.p25,
        median=b.median,
        p75=b.p75,
        quote=b.quote,
        quote_percentile=b.quote_percentile,
        position=b.position,
        vs_median_pct=b.vs_median_pct,
        method=method,
    )


async def get_vendor(session: AsyncSession, vendor_id: uuid.UUID) -> VendorOut:
    return VendorOut.model_validate(await VendorRepository(session).get(vendor_id))


async def create_vendor(session: AsyncSession, data: VendorCreate) -> VendorOut:
    """Org-private vendor (the global catalogue is read-only for organizations)."""
    vendor = Vendor(
        name=data.name,
        category=data.category.value,
        city=data.city,
        price_min=data.price_min,
        price_max=data.price_max,
        price_unit=data.price_unit,
        styles=[s.lower() for s in data.styles],
        languages=data.languages,
        rating=data.rating,
        review_count=0,
        description=data.description,
        description_lang=data.description_lang,
    )
    vendor.search_text = build_search_text(vendor)
    VendorRepository(session).add(vendor)
    await session.flush()
    await embed_vendors(session, [vendor])
    await session.flush()
    return VendorOut.model_validate(vendor)


# --------------------------------------------------------------------------- shortlist


async def _shortlist_out(session: AsyncSession, entry: VendorShortlist) -> ShortlistOut:
    out = ShortlistOut.model_validate(entry)
    out.vendor = VendorOut.model_validate(await VendorRepository(session).get(entry.vendor_id))
    return out


async def list_shortlist(session: AsyncSession, wedding_id: uuid.UUID) -> list[ShortlistOut]:
    await WeddingRepository(session).get(wedding_id)
    entries = await ShortlistRepository(session).list(
        VendorShortlist.wedding_id == wedding_id, order_by=[VendorShortlist.created_at]
    )
    vendors = {
        v.id: v
        for v in await VendorRepository(session).list(Vendor.id.in_([e.vendor_id for e in entries]))
    }
    result = []
    for e in entries:
        out = ShortlistOut.model_validate(e)
        if e.vendor_id in vendors:
            out.vendor = VendorOut.model_validate(vendors[e.vendor_id])
        result.append(out)
    return result


async def add_to_shortlist(
    session: AsyncSession, principal: Principal, wedding_id: uuid.UUID, data: ShortlistAdd
) -> ShortlistOut:
    await WeddingRepository(session).get(wedding_id)
    await VendorRepository(session).get(data.vendor_id)  # visible to this org
    entry = VendorShortlist(
        wedding_id=wedding_id,
        vendor_id=data.vendor_id,
        status=(
            ShortlistStatus.SUGGESTED.value
            if principal.is_ai
            else ShortlistStatus.SHORTLISTED.value
        ),
        score=data.score,
        explanation=data.explanation,
        quoted_price=data.quoted_price,
        notes=data.notes,
        added_by_actor=principal.actor_type.value,
    )
    ShortlistRepository(session).add(entry)
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError as exc:
        raise Conflict("Vendor is already on this wedding's shortlist") from exc
    return await _shortlist_out(session, entry)


async def decide_shortlist(
    session: AsyncSession,
    principal: Principal,
    wedding_id: uuid.UUID,
    entry_id: uuid.UUID,
    data: ShortlistDecision,
) -> ShortlistOut:
    """Final selection is always a human click: agents can suggest, never select."""
    if data.status == ShortlistStatus.SELECTED:
        if principal.is_ai:
            raise Forbidden("Final vendor selection requires a human")
        if not principal.can(Permission.VENDOR_SELECT):
            raise Forbidden("Your role may not select vendors")
    entry = await ShortlistRepository(session).get(entry_id)
    if entry.wedding_id != wedding_id:
        raise Forbidden("Shortlist entry belongs to another wedding")
    entry.status = data.status.value
    if data.notes is not None:
        entry.notes = data.notes
    entry.decided_by = principal.user_id
    entry.decided_at = datetime.now(UTC)
    await session.flush()
    return await _shortlist_out(session, entry)


async def selected_vendor_ids(session: AsyncSession, wedding_id: uuid.UUID) -> list[uuid.UUID]:
    entries = await ShortlistRepository(session).list(
        VendorShortlist.wedding_id == wedding_id,
        VendorShortlist.status == ShortlistStatus.SELECTED.value,
    )
    return [e.vendor_id for e in entries]
