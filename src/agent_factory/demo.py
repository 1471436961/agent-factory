"""Local-only composition root for the optional M3.6 Gradio demo."""

from __future__ import annotations

import asyncio
from typing import Self
from urllib.parse import urlparse

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from agent_factory.container import build_container
from agent_factory.interfaces.demo.gradio_app import (
    DEMO_CSS,
    create_demo_app,
    create_demo_theme,
)
from agent_factory.interfaces.demo.workflow import DemoWorkflow
from agent_factory.sdk import AgentFactoryClient
from agent_factory.settings import Settings


class DemoSettings(BaseSettings):
    """Local presentation settings kept separate from core server settings."""

    model_config = SettingsConfigDict(
        env_prefix="AGENT_FACTORY_DEMO_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    api_base_url: str = "http://127.0.0.1:8000"
    api_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    server_port: int = Field(default=7860, ge=1_024, le=65_535)

    @model_validator(mode="after")
    def require_loopback_api(self) -> Self:
        parsed = urlparse(self.api_base_url)
        if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError("demo API base URL must use a loopback host")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("demo API base URL cannot contain credentials")
        return self


def main() -> None:
    """Run the local UI while the REST API is hosted in another process."""

    settings = Settings()
    demo_settings = DemoSettings()
    if settings.auth_token is None:
        raise RuntimeError("AGENT_FACTORY_AUTH_TOKEN is required for the demo")

    token = settings.auth_token.get_secret_value()
    container = build_container(settings)
    asyncio.run(container.start())
    try:
        workflow = DemoWorkflow(
            client_factory=lambda: AgentFactoryClient(
                base_url=demo_settings.api_base_url,
                api_prefix=settings.api_prefix,
                token=token,
                timeout=demo_settings.api_timeout_seconds,
            ),
            runtime=container.demo_runtime,
        )
        app = create_demo_app(workflow)
        app.launch(
            server_name="127.0.0.1",
            server_port=demo_settings.server_port,
            share=False,
            show_error=False,
            enable_monitoring=False,
            strict_cors=True,
            inbrowser=False,
            quiet=False,
            footer_links=[],
            theme=create_demo_theme(),
            css=DEMO_CSS,
        )
    finally:
        asyncio.run(container.close())


if __name__ == "__main__":
    main()
