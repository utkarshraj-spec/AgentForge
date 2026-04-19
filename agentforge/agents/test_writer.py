"""Test-writing sub-agent.

Produces a small but meaningful integration-test suite for the generated
project. The tests exercise the happy path and at least one auth failure
path, which is the baseline acceptance criterion for the ``tests`` module.
"""

from __future__ import annotations

from agentforge.logging import get_logger
from agentforge.memory import SharedContext
from agentforge.models import EngineeringSpec, ModuleArtifact, ModuleSpec

logger = get_logger("test_writer")


def _api_tests(spec: EngineeringSpec) -> dict[str, str]:
    return {
        "tests/__init__.py": "",
        "tests/conftest.py": f'''"""Pytest fixtures for {spec.project_name}."""

from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("APP_SECRET", "test-secret-do-not-use-in-prod")


@pytest.fixture()
def client():
    # Force a fresh SQLite database for every test run.
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["DATABASE_URL"] = f"sqlite:///{{path}}"

    # Import lazily so the DATABASE_URL env var is honored.
    from {spec.project_name}.database import Base, engine
    from {spec.project_name}.main import app

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    with TestClient(app) as c:
        yield c

    try:
        os.remove(path)
    except OSError:
        pass
''',
        "tests/test_api.py": f'''"""Integration tests for the generated API."""

from __future__ import annotations


def _signup(client, email="alice@example.com", password="password123"):
    response = client.post(
        "/auth/signup",
        json={{"email": email, "password": password}},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _login(client, email="alice@example.com", password="password123") -> str:
    response = client.post(
        "/auth/login",
        data={{"username": email, "password": password}},
    )
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_signup_and_login(client):
    user = _signup(client)
    assert user["email"] == "alice@example.com"
    token = _login(client)
    me = client.get("/auth/me", headers={{"Authorization": f"Bearer {{token}}"}})
    assert me.status_code == 200
    assert me.json()["email"] == "alice@example.com"


def test_login_rejects_wrong_password(client):
    _signup(client)
    response = client.post(
        "/auth/login",
        data={{"username": "alice@example.com", "password": "wrong"}},
    )
    assert response.status_code == 401


def test_todos_require_authentication(client):
    response = client.get("/todos")
    assert response.status_code == 401


def test_todo_lifecycle(client):
    _signup(client)
    token = _login(client)
    headers = {{"Authorization": f"Bearer {{token}}"}}

    created = client.post(
        "/todos",
        json={{"title": "write tests", "description": "for {spec.project_name}"}},
        headers=headers,
    )
    assert created.status_code == 201
    todo = created.json()
    assert todo["done"] is False

    updated = client.patch(
        f"/todos/{{todo['id']}}",
        json={{"done": True}},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["done"] is True

    listed = client.get("/todos", headers=headers)
    assert listed.status_code == 200
    assert any(t["id"] == todo["id"] for t in listed.json())

    deleted = client.delete(f"/todos/{{todo['id']}}", headers=headers)
    assert deleted.status_code == 204


def test_cannot_read_other_users_todos(client):
    _signup(client, email="alice@example.com")
    token_a = _login(client, email="alice@example.com")
    client.post(
        "/todos",
        json={{"title": "alice private"}},
        headers={{"Authorization": f"Bearer {{token_a}}"}},
    )

    _signup(client, email="bob@example.com", password="password456")
    token_b = _login(client, email="bob@example.com", password="password456")
    response = client.get("/todos", headers={{"Authorization": f"Bearer {{token_b}}"}})
    assert response.status_code == 200
    assert response.json() == []
''',
    }


def run(spec: EngineeringSpec, module: ModuleSpec, context: SharedContext) -> ModuleArtifact:
    """Produce tests for *module*."""

    files: dict[str, str] = {}
    if module.kind == "tests":
        files.update(_api_tests(spec))
    elif module.kind == "api":
        # Nothing to add here — the tests module covers the API surface.
        pass

    artifact = ModuleArtifact(
        module=module.name,
        agent="test_writer",
        files=files,
        notes=f"Generated {len(files)} test files for {module.name}.",
    )
    context.audit(
        "test_writer",
        "tests_generated",
        {"module": module.name, "files": list(files.keys())},
    )
    return artifact
