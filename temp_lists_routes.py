"""Create a refresh endpoint for lists that can handle rate limiting."""

import os
from pathlib import Path
from fastapi import APIRouter, HTTPException, Header
from typing import Literal

from dotenv import load_dotenv
load_dotenv()

from .store import Store
from .providers.letterboxd import HttpLetterboxdScraper, MockLetterboxdScraper
from .security import verify_session

router = APIRouter()

SCRAPER_BACKEND = os.getenv("SCRAPER_BACKEND", "http").lower()

router = APIRouter()

router = APIRouter()

SCRAPER_BACKEND = os.getenv("SCRAPER_BACKEND", "http").lower()

router = APIRouter()

SCRAPER_BACKEND = os.getenv("SCRAPER_BACKEND", "http").lower()

router = APIRouter()

SCRAPER_BACKEND = os.getenv("SCRAPER_BACKEND", "http").lower()

router = APIRouter()

SCRAPER_BACKEND = os.getenv("SCRAPER_BACKEND", "http").lower()

router = APIRouter()

SCRAPER_BACKEND = os.getenv("SCRAPER_BACKEND", "http").lower()

router = APIRouter()

SCRAPER_BACKEND = os.getenv("SCRAPER_BACKEND", "http").lower()

router = APIRouter()

SCRAPER_BACKEND = os.getenv("SCRAPER_BACKEND", "http").lower()

router = APIRouter()

SCRAPER_BACKEND = os.getenv("SCRAPER_BACKEND", "http").lower()

router = APIRouter()

SCRAPER_BACKEND = os.getenv("SCRAPER_BACKEND", "http").lower()

router = APIRouter()

SCRAPER_BACKEND = os.getenv("SCRAPER_BACKEND", "http").lower()

router = APIRouter()

SCRAPER_BACKEND = os.getenv("SCRAPER_BACKEND", "http").lower()

router = APIRouter()

SCRAPER_BACKEND = os.getenv("SCRAPER_BACKEND", "http").lower()

router = APIRouter()

SCRAPER_BACKEND = os.getenv("SCRAPER_BACKEND", "http").lower()

router = APIRouter()

SCRAPER_BACKEND = os.getenv("SCRAPER_BACKEND", "http").lower()

router = APIRouter()

SCRAPER_BACKEND = os.getenv("SCRAPER_BACKEND", "http").lower()

router = APIRouter()

SCRAPER_BACKEND = os.getenv("SCRAPER_BACKEND", "http").lower()

router = APIRouter()

SCRAPER_BACKEND = os.getenv("SCRAPER_BACKEND", "http").lower()

router = APIRouter()
