"""
LLM Usage Tracker — wraps every LLM call and persists a cost/token record.

Never raises: tracking failures are swallowed silently so they can never
break the primary AI feature.
"""
from typing import Optional

from app.core.logging import get_logger

logger = get_logger("usage_tracker")

# Set to True once the table has been confirmed to exist this process lifetime.
_table_ready: bool = False

# ── Pricing tables (USD per 1 Million tokens) ───────────────────────────────
# Flat-rate models (no context-window tier).
PRICING: dict[str, dict[str, float]] = {
    # Gemini 2.5 Flash — paid Tier-1
    # Input (text/image/video): $0.30 | Audio: $1.00 (tracked as text rate here)
    # Output (incl. thinking): $2.50
    "gemini-2.5-flash":                  {"input": 0.30,   "output": 2.50},
    "gemini-2.5-flash-preview-04-17":    {"input": 0.30,   "output": 2.50},
    # Gemini 1.x (legacy)
    "gemini-1.5-flash":                  {"input": 0.075,  "output": 0.30},
    "gemini-1.5-pro":                    {"input": 1.25,   "output": 5.00},
    # OpenAI (future)
    "gpt-4o":                            {"input": 2.50,   "output": 10.00},
    "gpt-4o-mini":                       {"input": 0.15,   "output": 0.60},
    # Claude (future)
    "claude-3-5-sonnet-20241022":        {"input": 3.00,   "output": 15.00},
    "claude-3-haiku-20240307":           {"input": 0.25,   "output": 1.25},
}

# Tiered pricing: cost differs based on whether input_tokens <= threshold.
# Gemini 2.5 Pro — paid Tier-1
#   Prompt ≤ 200k: input $1.25, output $10.00  (incl. thinking tokens)
#   Prompt > 200k: input $2.50, output $15.00  (incl. thinking tokens)
_PRO_25_TIER = {
    "threshold": 200_000,
    "short": {"input": 1.25,  "output": 10.00},
    "long":  {"input": 2.50,  "output": 15.00},
}
TIERED_PRICING: dict[str, dict] = {
    "gemini-2.5-pro":                _PRO_25_TIER,
    "gemini-2.5-pro-exp-03-25":      _PRO_25_TIER,
    "gemini-2.5-pro-preview-03-25":  _PRO_25_TIER,
}


def normalise_model_name(raw: str) -> str:
    """Strip 'models/' prefix that the Gemini SDK may prepend."""
    return raw.removeprefix("models/").strip()


def get_provider(model: str) -> str:
    m = model.lower()
    if "gemini" in m:
        return "gemini"
    if "gpt" in m or "openai" in m:
        return "openai"
    if "claude" in m or "anthropic" in m:
        return "claude"
    return "unknown"


def _resolve_rates(model: str, input_tokens: int) -> dict[str, float]:
    """Return the applicable {input, output} rates (USD/1M) for a model + prompt size."""
    if model in TIERED_PRICING:
        tier_cfg = TIERED_PRICING[model]
        return tier_cfg["long"] if input_tokens > tier_cfg["threshold"] else tier_cfg["short"]
    return PRICING.get(model, {"input": 0.0, "output": 0.0})


def compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Returns USD cost, rounded to 8 decimal places.

    Gemini 2.5 Pro uses tiered pricing: if the prompt (input_tokens) exceeds
    200,000 tokens the higher rate applies to *both* input and output.
    When the bucket is unknown/untracked, the ≤200k (cheaper) rate is used.
    """
    rates = _resolve_rates(model, input_tokens)
    cost = (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000
    return round(cost, 8)


def get_cost_breakdown(model: str, input_tokens: int, output_tokens: int) -> dict:
    """Return a full cost breakdown dict mirroring the dashboard analytics format.

    Fields returned:
        model, input_tokens, output_tokens, total_tokens,
        input_rate_per_1m, output_rate_per_1m,
        input_cost_usd, output_cost_usd, total_cost_usd,
        tier_note  – human-readable tier explanation.
    """
    rates = _resolve_rates(model, input_tokens)
    input_cost  = round(input_tokens  * rates["input"]  / 1_000_000, 8)
    output_cost = round(output_tokens * rates["output"] / 1_000_000, 8)
    total_cost  = round(input_cost + output_cost, 8)

    # Build a short human-readable tier note.
    if model in TIERED_PRICING:
        tier_cfg = TIERED_PRICING[model]
        if input_tokens > tier_cfg["threshold"]:
            tier_note = f"Prompt >200k tokens — long-context rate applied (${rates['input']}/1M in, ${rates['output']}/1M out)."
        else:
            tier_note = f"Prompt ≤200k tokens — standard rate applied (${rates['input']}/1M in, ${rates['output']}/1M out)."
    elif rates["input"] == 0.0:
        tier_note = "Model not in pricing table — cost recorded as $0.00."
    else:
        tier_note = f"Flat rate: ${rates['input']}/1M input tokens, ${rates['output']}/1M output tokens (incl. thinking tokens)."

    return {
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "input_rate_per_1m": rates["input"],
        "output_rate_per_1m": rates["output"],
        "input_cost_usd": input_cost,
        "output_cost_usd": output_cost,
        "total_cost_usd": total_cost,
        "tier_note": tier_note,
    }


async def log_usage(
    model: str,
    feature_name: str,
    input_tokens: int,
    output_tokens: int,
    user_id: Optional[str] = None,
) -> None:
    """
    Persist one LLM usage record.  Fire-and-forget from callers — never raises.
    Uses NullPool so connections are never shared across event-loop boundaries.
    """
    global _table_ready
    try:
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from sqlalchemy.pool import NullPool
        from sqlmodel.ext.asyncio.session import AsyncSession
        from sqlmodel import SQLModel

        from app.core.config import settings
        from app.models.llm_usage import LLMUsageLog  # ensure model is registered

        model = normalise_model_name(model)
        total_tokens = input_tokens + output_tokens
        cost = compute_cost(model, input_tokens, output_tokens)
        provider = get_provider(model)

        record = LLMUsageLog(
            provider=provider,
            model=model,
            feature_name=feature_name,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cost=cost,
            user_id=user_id,
        )

        engine = create_async_engine(settings.POSTGRES_URL, poolclass=NullPool, echo=False)
        try:
            # Ensure the table exists — only runs once per process lifetime.
            if not _table_ready:
                async with engine.begin() as conn:
                    await conn.run_sync(
                        lambda sync_conn: LLMUsageLog.__table__.create(sync_conn, checkfirst=True)
                    )
                _table_ready = True

            Session = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
            async with Session() as session:
                session.add(record)
                await session.commit()
        finally:
            await engine.dispose()

        logger.debug(
            "usage_tracker.logged",
            model=model,
            feature=feature_name,
            tokens=total_tokens,
            cost_usd=cost,
        )

    except Exception as exc:
        # NEVER let tracking failures surface to callers.
        logger.warning("usage_tracker.log_failed", error=str(exc))
