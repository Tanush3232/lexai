"""
Seed script: creates demo user, folders, and clause library entries.
Run once after migrate: python scripts/seed.py
"""
import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import AsyncSessionLocal, init_db
from app.core.auth import hash_password
from app.models.user import User
from app.models.folder import Folder
from sqlmodel import select


DEMO_USERS = [
    {"email": "admin@lexai.law", "full_name": "Admin User", "password": "lexai2024!", "role": "ops_admin"},
    {"email": "legal@lexai.law", "full_name": "Legal Counsel", "password": "lexai2024!", "role": "legal_team"},
    {"email": "reviewer@lexai.law", "full_name": "Contract Reviewer", "password": "lexai2024!", "role": "reviewer"},
]

DEMO_FOLDERS = [
    {"name": "NDAs", "description": "Non-disclosure agreements"},
    {"name": "Vendor Contracts", "description": "Vendor and supplier contracts"},
    {"name": "IP & Licensing", "description": "Intellectual property and license agreements"},
    {"name": "Policy Documents", "description": "Internal legal policies"},
]


async def seed():
    await init_db()
    async with AsyncSessionLocal() as session:

        # Create users
        for u in DEMO_USERS:
            existing_result = await session.execute(select(User).where(User.email == u["email"]))
            if not existing_result.scalars().first():
                user = User(
                    email=u["email"],
                    full_name=u["full_name"],
                    hashed_password=hash_password(u["password"]),
                    role=u["role"],
                )
                session.add(user)
                print(f"  ✓ Created user: {u['email']}")

        await session.commit()

        # Get admin user for folder ownership
        admin_res = await session.execute(select(User).where(User.email == "admin@lexai.law"))
        admin = admin_res.scalars().first()

        # Create folders
        for f in DEMO_FOLDERS:
            existing_result = await session.execute(select(Folder).where(Folder.name == f["name"]))
            if not existing_result.scalars().first():
                folder = Folder(
                    name=f["name"],
                    description=f["description"],
                    owner_id=admin.id,
                )
                session.add(folder)
                print(f"  ✓ Created folder: {f['name']}")

        await session.commit()
        print("\n✅ Seed complete!")
        print("\nDemo credentials:")
        for u in DEMO_USERS:
            print(f"  {u['email']:35} password: {u['password']}  role: {u['role']}")


if __name__ == "__main__":
    asyncio.run(seed())
