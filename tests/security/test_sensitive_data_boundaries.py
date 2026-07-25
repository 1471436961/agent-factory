"""Cross-artifact checks for API credential and content redaction."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from agent_factory.application.controller import FactoryController
from agent_factory.application.queries import AuditQuery
from agent_factory.domain.models import AgentDefinition, AgentPrototype
from agent_factory.interfaces.api.app import create_app
from agent_factory.settings import Settings

SECRET = "M43-BEARER-SECRET-THAT-MUST-NEVER-LEAK"
PROMPT = "M43-SYSTEM-PROMPT-MUST-NEVER-LEAK"
KNOWLEDGE = "M43-KNOWLEDGE-BODY-MUST-NEVER-LEAK"
ARGUMENTS = "M43-TOOL-ARGUMENTS-MUST-NEVER-LEAK"


def _settings(tmp_path: Path, migrations_dir: Path) -> Settings:
    return Settings.model_validate(
        {
            "database_url": (
                f"sqlite+aiosqlite:///{(tmp_path / 'factory.db').as_posix()}"
            ),
            "migrations_dir": migrations_dir,
            "data_dir": tmp_path,
            "auth_token": SECRET,
            "auth_subject": "m43-redaction-owner",
            "auth_roles": ["admin"],
        }
    )


@asynccontextmanager
async def _running_app(
    settings: Settings,
) -> AsyncIterator[tuple[httpx.AsyncClient, FastAPI]]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            yield client, app


@pytest.mark.asyncio
async def test_unexpected_api_failure_does_not_leak_sensitive_content(
    tmp_path: Path,
    migrations_dir: Path,
    writer_definition: AgentDefinition,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    definition = writer_definition.model_copy(update={"system_prompt": PROMPT})
    body = {
        "prototype_id": "writer-agent",
        "version": "1.0.0",
        "definition": definition.model_dump(mode="json"),
    }
    exception_text = f"{SECRET} {PROMPT} {KNOWLEDGE} {ARGUMENTS}"

    async def explode(
        self: FactoryController,
        command: object,
    ) -> AgentPrototype:
        del self, command
        raise RuntimeError(exception_text)

    monkeypatch.setattr(FactoryController, "register_prototype", explode)
    caplog.set_level(logging.ERROR, logger="agent_factory.api")
    settings = _settings(tmp_path, migrations_dir)

    async with _running_app(settings) as (client, app):
        response = await client.post(
            "/api/v1/prototypes",
            json=body,
            headers={"Authorization": f"Bearer {SECRET}"},
        )
        audit = await app.state.container.controller.query_audit(
            AuditQuery(page_size=100)
        )
        artifacts = (
            response.text,
            repr(response),
            caplog.text,
            audit.model_dump_json(),
            repr(settings),
            repr(app.state.container.authenticator),
        )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_ERROR"
    for marker in (SECRET, PROMPT, KNOWLEDGE, ARGUMENTS):
        assert all(marker not in artifact for artifact in artifacts)
    records = [
        record for record in caplog.records if record.name == "agent_factory.api"
    ]
    assert len(records) == 1
    assert records[0].getMessage() == "unhandled_error"
    assert records[0].__dict__["exception_type"] == "RuntimeError"
    assert records[0].exc_info is None
    assert audit.total == 0
