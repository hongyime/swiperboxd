"""Vercel entrypoint for ASGI app."""

from api.app import app

__all__ = ["app"]
