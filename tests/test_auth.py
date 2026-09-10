"""Account hashing, session service, and HTTP contract tests."""

import asyncio

from httpx import ASGITransport, AsyncClient

from app.api.main import create_app
from app.auth.memory import InMemoryAccountRepository
from app.auth.passwords import hash_password, verify_password
from app.auth.service import AuthService
from tests.api_helpers import research_service
from tests.test_research_agent import SuccessfulGraph

PASSWORD = "correct horse battery staple"


def test_scrypt_password_hashes_are_salted_and_verifiable() -> None:
    """Passwords use unique salts and reject incorrect candidates."""
    first = hash_password(PASSWORD)
    second = hash_password(PASSWORD)

    assert first != second
    assert first.startswith("scrypt$")
    assert verify_password(PASSWORD, first) is True
    assert verify_password("incorrect password value", first) is False
    assert verify_password(PASSWORD, "malformed") is False


def test_auth_api_registers_resolves_and_revokes_a_session() -> None:
    """A browser can use the complete cookie-session lifecycle."""

    async def exercise() -> None:
        auth = AuthService(InMemoryAccountRepository(), session_ttl_seconds=3600)
        app = create_app(research_service(SuccessfulGraph()), auth_service=auth)
        transport = ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                registered = await client.post(
                    "/api/v1/auth/register",
                    json={
                        "email": "  Analyst@Example.com ",
                        "display_name": "  Research   Analyst  ",
                        "password": PASSWORD,
                    },
                )
                current = await client.get("/api/v1/auth/me")
                logged_out = await client.post("/api/v1/auth/logout")
                anonymous = await client.get("/api/v1/auth/me")

        assert registered.status_code == 201
        assert registered.json()["email"] == "analyst@example.com"
        assert registered.json()["display_name"] == "Research Analyst"
        cookie = registered.headers["set-cookie"]
        assert "HttpOnly" in cookie
        assert "SameSite=lax" in cookie
        assert current.status_code == 200
        assert logged_out.status_code == 204
        assert anonymous.status_code == 401
        assert anonymous.json()["error"]["code"] == "authentication_required"
        assert anonymous.headers["www-authenticate"] == "Session"

    asyncio.run(exercise())


def test_auth_api_uses_stable_duplicate_and_credential_failures() -> None:
    """Registration conflicts and login failures expose safe envelopes."""

    async def exercise() -> None:
        auth = AuthService(InMemoryAccountRepository(), session_ttl_seconds=3600)
        app = create_app(research_service(SuccessfulGraph()), auth_service=auth)
        transport = ASGITransport(app=app)
        payload = {
            "email": "analyst@example.com",
            "display_name": "Analyst",
            "password": PASSWORD,
        }
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                registered = await client.post("/api/v1/auth/register", json=payload)
                assert registered.status_code == 201
                duplicate = await client.post("/api/v1/auth/register", json=payload)
                wrong = await client.post(
                    "/api/v1/auth/login",
                    json={
                        "email": payload["email"],
                        "password": "wrong password value",
                    },
                )
                missing = await client.post(
                    "/api/v1/auth/login",
                    json={"email": "missing@example.com", "password": PASSWORD},
                )

        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "email_already_registered"
        assert wrong.status_code == 401
        assert missing.status_code == 401
        assert wrong.json() == missing.json()
        assert wrong.json()["error"]["code"] == "invalid_credentials"

    asyncio.run(exercise())


def test_auth_api_validates_credentials_before_hashing() -> None:
    """Malformed email and short passwords are rejected as client input."""
    auth = AuthService(InMemoryAccountRepository(), session_ttl_seconds=3600)
    app = create_app(research_service(SuccessfulGraph()), auth_service=auth)

    async def exercise() -> None:
        transport = ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                response = await client.post(
                    "/api/v1/auth/register",
                    json={
                        "email": "invalid",
                        "display_name": "Analyst",
                        "password": "short",
                    },
                )
        assert response.status_code == 422

    asyncio.run(exercise())
