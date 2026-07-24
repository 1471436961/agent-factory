"""ASGI entry point for Agent Factory."""

from agent_factory.interfaces.api.app import create_app

__all__ = ["app", "create_app"]


app = create_app()
