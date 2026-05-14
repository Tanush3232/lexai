import asyncio
from sqlalchemy import text
from app.core.database import engine

async def run():
    async with engine.begin() as conn:
        await conn.execute(text('ALTER TABLE web_search_citations ADD COLUMN IF NOT EXISTS turn_index INTEGER DEFAULT 0'))

asyncio.run(run())
