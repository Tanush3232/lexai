"""
seed.py — LexAI Production Database Seeder
==========================================
Creates the initial admin users and default folders needed to run the app.

This script is IDEMPOTENT — safe to run multiple times. It will SKIP any
users or folders that already exist.

Usage (from inside the Docker API container):
    docker compose exec api python scripts/seed.py

Usage (from apps/api/ directory locally with .env loaded):
    python scripts/seed.py
"""
import asyncio
import os
import sys

# ── Make 'app' package importable regardless of where script is called from ──
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
API_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, API_DIR)

from app.core.database import AsyncSessionLocal, init_db
from app.core.auth import hash_password
from app.models.user import User
from app.models.folder import Folder
from sqlmodel import select

# ── Configuration ─────────────────────────────────────────────────────────────
# NOTE: Change these passwords before going live in production!
USERS = [
    {
        "email": "admin@lexai.law",
        "full_name": "Admin User",
        "password": "lexai2024!",
        "role": "ops_admin",
    },
    {
        "email": "legal@lexai.law",
        "full_name": "Legal Counsel",
        "password": "lexai2024!",
        "role": "legal_team",
    },
    {
        "email": "reviewer@lexai.law",
        "full_name": "Contract Reviewer",
        "password": "lexai2024!",
        "role": "reviewer",
    },
]

FOLDERS = [
    {"name": "NDAs", "description": "Non-disclosure agreements"},
    {"name": "Vendor Contracts", "description": "Vendor and supplier contracts"},
    {"name": "IP & Licensing", "description": "Intellectual property and license agreements"},
    {"name": "Policy Documents", "description": "Internal legal policies"},
    {"name": "Legal Acts", "description": "Indian legal acts and statutes"},
]


# ── Seed logic ─────────────────────────────────────────────────────────────────
async def seed():
    print("=" * 60)
    print("  LexAI — Production Seed")
    print("=" * 60)

    # Ensure all DB tables exist (idempotent via checkfirst)
    print("\n[1/3] Initializing database tables…")
    await init_db()
    print("      ✓ Tables OK")

    async with AsyncSessionLocal() as session:

        # ── Step 2: Create users ──────────────────────────────────────────────
        print("\n[2/3] Creating users…")
        for u in USERS:
            result = await session.execute(select(User).where(User.email == u["email"]))
            existing = result.scalars().first()
            if existing:
                print(f"      ~ Skipped (already exists): {u['email']}")
            else:
                user = User(
                    email=u["email"],
                    full_name=u["full_name"],
                    hashed_password=hash_password(u["password"]),
                    role=u["role"],
                    is_active=True,
                )
                session.add(user)
                print(f"      ✓ Created: {u['email']}  [{u['role']}]")

        await session.commit()

        # ── Step 3: Create default folders (owned by admin) ───────────────────
        print("\n[3/3] Creating default folders…")
        admin_result = await session.execute(
            select(User).where(User.email == "admin@lexai.law")
        )
        admin = admin_result.scalars().first()

        if not admin:
            print("      ✗ ERROR: admin@lexai.law not found — cannot create folders.")
            print("        Something went wrong in step 2. Check DB connection.")
            sys.exit(1)

        for f in FOLDERS:
            result = await session.execute(
                select(Folder).where(Folder.name == f["name"])
            )
            existing = result.scalars().first()
            if existing:
                print(f"      ~ Skipped (already exists): {f['name']}")
            else:
                folder = Folder(
                    name=f["name"],
                    description=f["description"],
                    owner_id=admin.id,
                )
                session.add(folder)
                print(f"      ✓ Created: {f['name']}")

        await session.commit()

    # ── Final summary ──────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  ✅ Seed complete!")
    print("=" * 60)
    print("\nDemo credentials:")
    print(f"  {'Email':<35} {'Password':<20} Role")
    print(f"  {'-'*35} {'-'*20} ----")
    for u in USERS:
        print(f"  {u['email']:<35} {u['password']:<20} {u['role']}")
    print()
    print("  Next steps:")
    print("  1. Run:  docker compose exec api python batch_download.py")
    print("  2. Run:  docker compose exec api python bulk_ingest.py")
    print()


if __name__ == "__main__":
    asyncio.run(seed())
