"""
Test suite for LexAI API.
Run: cd apps/api && pytest tests/ -v
"""
import pytest
import asyncio
from httpx import AsyncClient
from app.main import app


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def client():
    async with AsyncClient(app=app, base_url="http://test") as c:
        yield c


@pytest.fixture(scope="session")
async def auth_token(client):
    """Register and login a test user, return token."""
    await client.post("/api/v1/auth/register", json={
        "email": "test@lexai.test",
        "full_name": "Test User",
        "password": "testpass123",
        "role": "legal_team",
    })
    res = await client.post(
        "/api/v1/auth/token",
        data={"username": "test@lexai.test", "password": "testpass123"},
    )
    return res.json()["access_token"]


@pytest.mark.asyncio
async def test_health(client):
    res = await client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_register(client):
    res = await client.post("/api/v1/auth/register", json={
        "email": "newuser@lexai.test",
        "full_name": "New User",
        "password": "newpass123",
        "role": "legal_team",
    })
    assert res.status_code in (201, 409)  # 409 if already exists


@pytest.mark.asyncio
async def test_login_valid(client):
    # Register first
    await client.post("/api/v1/auth/register", json={
        "email": "login_test@lexai.test",
        "full_name": "Login Test",
        "password": "loginpass123",
        "role": "legal_team",
    })
    res = await client.post(
        "/api/v1/auth/token",
        data={"username": "login_test@lexai.test", "password": "loginpass123"},
    )
    assert res.status_code == 200
    data = res.json()
    assert "access_token" in data
    assert data["user"]["email"] == "login_test@lexai.test"


@pytest.mark.asyncio
async def test_login_invalid(client):
    res = await client.post(
        "/api/v1/auth/token",
        data={"username": "nobody@lexai.test", "password": "wrong"},
    )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_me(client, auth_token):
    res = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {auth_token}"},
    )
    assert res.status_code == 200
    assert res.json()["email"] == "test@lexai.test"


@pytest.mark.asyncio
async def test_create_folder(client, auth_token):
    headers = {"Authorization": f"Bearer {auth_token}"}
    res = await client.post(
        "/api/v1/folders/",
        json={"name": "Test NDAs", "description": "Test folder"},
        headers=headers,
    )
    assert res.status_code == 201
    data = res.json()
    assert data["name"] == "Test NDAs"
    assert "id" in data
    return data["id"]


@pytest.mark.asyncio
async def test_list_folders(client, auth_token):
    headers = {"Authorization": f"Bearer {auth_token}"}
    res = await client.get("/api/v1/folders/", headers=headers)
    assert res.status_code == 200
    assert isinstance(res.json(), list)


@pytest.mark.asyncio
async def test_folder_not_found(client, auth_token):
    headers = {"Authorization": f"Bearer {auth_token}"}
    res = await client.get("/api/v1/folders/nonexistent-id", headers=headers)
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_create_chat_session(client, auth_token):
    headers = {"Authorization": f"Bearer {auth_token}"}
    # Create a folder first
    folder_res = await client.post(
        "/api/v1/folders/",
        json={"name": "Chat Test Folder"},
        headers=headers,
    )
    folder_id = folder_res.json()["id"]

    session_res = await client.post(
        "/api/v1/chat/sessions",
        json={
            "title": "Test Session",
            "scope_type": "single_folder",
            "scope_folder_ids": [folder_id],
            "scope_document_ids": [],
        },
        headers=headers,
    )
    assert session_res.status_code == 201
    assert "id" in session_res.json()


@pytest.mark.asyncio
async def test_list_drafts_empty(client, auth_token):
    headers = {"Authorization": f"Bearer {auth_token}"}
    res = await client.get("/api/v1/drafts/", headers=headers)
    assert res.status_code == 200
    assert isinstance(res.json(), list)


@pytest.mark.asyncio
async def test_audit_log(client, auth_token):
    headers = {"Authorization": f"Bearer {auth_token}"}
    res = await client.get("/api/v1/audit/", headers=headers)
    assert res.status_code == 200
    assert isinstance(res.json(), list)


@pytest.mark.asyncio
async def test_unauthorized_access(client):
    """All protected routes must reject requests without a token."""
    routes = [
        "/api/v1/folders/",
        "/api/v1/documents/folder/test-id",
        "/api/v1/chat/sessions",
        "/api/v1/drafts/",
        "/api/v1/audit/",
    ]
    for route in routes:
        res = await client.get(route)
        assert res.status_code == 401, f"Route {route} should return 401 without token"
