"""
Pytest configuration and shared fixtures.
"""
import asyncio
import pytest


@pytest.fixture(scope="session")
def event_loop():
    """Create a session-scoped event loop."""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()
