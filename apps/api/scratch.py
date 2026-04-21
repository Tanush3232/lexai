import asyncio
from app.core.database import get_session
from app.models.translation import TranslationJob
from sqlalchemy.future import select
import json
import re

async def main():
    async for session in get_session():
        result = await session.execute(select(TranslationJob).where(TranslationJob.id == 'df57afcb-a5b6-4af1-b4c2-b2edfa45de0e'))
        job = result.scalar_one_or_none()
        if job:
            flags = json.loads(job.uncertainty_flags) if job.uncertainty_flags else {}
            blocks = flags.get('translated_blocks', [])
            for b in blocks:
                content = b.get('translated_content', '')
                if re.search(r'[\u0900-\u097f]', content):
                    print(f"Block {b['index']}: {content[:100]}")
            # Also show some table structure if it's there
            print("Table/List Blocks:", len([b for b in blocks if b.get('type') in ('table', 'list')]))
        break
asyncio.run(main())
