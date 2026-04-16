"""
LLM Usage Analytics — ops_admin-only REST endpoints.
"""
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select as sa_select, desc, update as sa_update
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.database import get_session
from app.core.auth import get_current_user
from app.models.user import User
from app.models.llm_usage import LLMUsageLog
from app.services.usage_tracker import get_cost_breakdown, compute_cost

router = APIRouter()


async def require_ops_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "ops_admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Ops admin access required")
    return current_user


@router.get("/summary")
async def get_summary(
    session: AsyncSession = Depends(get_session),
    _: User = Depends(require_ops_admin),
):
    """Overall totals: calls, tokens, cost."""
    result = await session.execute(
        sa_select(
            func.count(LLMUsageLog.id).label("total_calls"),
            func.coalesce(func.sum(LLMUsageLog.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(LLMUsageLog.input_tokens), 0).label("total_input_tokens"),
            func.coalesce(func.sum(LLMUsageLog.output_tokens), 0).label("total_output_tokens"),
            func.coalesce(func.sum(LLMUsageLog.cost), 0.0).label("total_cost"),
        )
    )
    row = result.mappings().one()
    return {
        "total_calls": row["total_calls"],
        "total_tokens": row["total_tokens"],
        "total_input_tokens": row["total_input_tokens"],
        "total_output_tokens": row["total_output_tokens"],
        "total_cost_usd": round(float(row["total_cost"]), 6),
    }


@router.get("/by-model")
async def get_by_model(
    session: AsyncSession = Depends(get_session),
    _: User = Depends(require_ops_admin),
):
    """Cost and token usage broken down by model."""
    result = await session.execute(
        sa_select(
            LLMUsageLog.provider,
            LLMUsageLog.model,
            func.count(LLMUsageLog.id).label("calls"),
            func.coalesce(func.sum(LLMUsageLog.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(LLMUsageLog.cost), 0.0).label("total_cost"),
        )
        .group_by(LLMUsageLog.provider, LLMUsageLog.model)
        .order_by(desc("total_cost"))
    )
    rows = result.mappings().all()
    return [
        {
            "provider": r["provider"],
            "model": r["model"],
            "calls": r["calls"],
            "total_tokens": r["total_tokens"],
            "total_cost_usd": round(float(r["total_cost"]), 6),
        }
        for r in rows
    ]


@router.get("/by-feature")
async def get_by_feature(
    session: AsyncSession = Depends(get_session),
    _: User = Depends(require_ops_admin),
):
    """Cost and token usage broken down by feature."""
    result = await session.execute(
        sa_select(
            LLMUsageLog.feature_name,
            func.count(LLMUsageLog.id).label("calls"),
            func.coalesce(func.sum(LLMUsageLog.total_tokens), 0).label("total_tokens"),
            func.coalesce(func.sum(LLMUsageLog.cost), 0.0).label("total_cost"),
        )
        .group_by(LLMUsageLog.feature_name)
        .order_by(desc("total_cost"))
    )
    rows = result.mappings().all()
    return [
        {
            "feature_name": r["feature_name"],
            "calls": r["calls"],
            "total_tokens": r["total_tokens"],
            "total_cost_usd": round(float(r["total_cost"]), 6),
        }
        for r in rows
    ]


@router.get("/logs")
async def get_logs(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    feature_name: Optional[str] = Query(None),
    model: Optional[str] = Query(None),
    session: AsyncSession = Depends(get_session),
    _: User = Depends(require_ops_admin),
):
    """Paginated raw usage log."""
    query = sa_select(LLMUsageLog).order_by(desc(LLMUsageLog.created_at))
    if feature_name:
        query = query.where(LLMUsageLog.feature_name == feature_name)
    if model:
        query = query.where(LLMUsageLog.model == model)
    query = query.offset((page - 1) * limit).limit(limit)
    result = await session.execute(query)
    rows = result.scalars().all()
    return [
        {
            "id": r.id,
            "provider": r.provider,
            "model": r.model,
            "feature_name": r.feature_name,
            "input_tokens": r.input_tokens,
            "output_tokens": r.output_tokens,
            "total_tokens": r.total_tokens,
            "cost_usd": round(float(r.cost), 8),
            "user_id": r.user_id,
            "created_at": r.created_at.isoformat(),
            **{k: v for k, v in get_cost_breakdown(r.model, r.input_tokens, r.output_tokens).items()
               if k in {"input_rate_per_1m", "output_rate_per_1m", "input_cost_usd", "output_cost_usd", "tier_note"}},
        }
        for r in rows
    ]


@router.post("/recalculate-costs")
async def recalculate_costs(
    session: AsyncSession = Depends(get_session),
    _: User = Depends(require_ops_admin),
):
    """Recompute costs AND relabel any 'unknown' feature records.

    1. Relabel old 'unknown' records using model-based heuristics:
       - Pro model  → doc_intelligence (ingestion tree build, synthesis, etc.)
       - Flash model → doc_intelligence (metadata extraction, text extraction, etc.)
    2. Recalculate cost for every record using current pricing tables.
    Returns counts of updated costs and relabeled features.
    """
    BATCH = 500
    offset = 0
    cost_updated = 0
    relabeled = 0

    while True:
        result = await session.execute(
            sa_select(LLMUsageLog).offset(offset).limit(BATCH)
        )
        rows = result.scalars().all()
        if not rows:
            break

        for row in rows:
            # ── Relabel "unknown" feature names ──
            if row.feature_name == "unknown":
                row.feature_name = "doc_intelligence"
                relabeled += 1

            # ── Recalculate cost ──
            correct = compute_cost(row.model, row.input_tokens, row.output_tokens)
            if abs(float(row.cost) - correct) > 1e-10:
                row.cost = correct
                cost_updated += 1

        await session.commit()
        offset += BATCH
        if len(rows) < BATCH:
            break

    return {
        "cost_updated": cost_updated,
        "relabeled": relabeled,
        "message": f"Recalculated costs for {cost_updated} records, relabeled {relabeled} unknown → doc_intelligence.",
    }
