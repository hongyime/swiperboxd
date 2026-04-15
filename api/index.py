"""Vercel entrypoint - exports the FastAPI app."""
from src.api.app import app

__all__ = ["app"]
