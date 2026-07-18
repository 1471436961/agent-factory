"""Agent Factory package."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("agent-factory")
except PackageNotFoundError:  # pragma: no cover - only for unpackaged source use
    __version__ = "0+unknown"

__all__ = ["__version__"]
