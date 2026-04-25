"""
evals/conftest.py
==================
Injects environment variables BEFORE any app module is imported.
This means pytest never needs a real .env file or real API keys.
All external calls (LLM, DB, scraper) are mocked inside the tests themselves.
"""

import os

os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("SECRET_KEY", "test-secret-key-minimum-32-characters-here")
os.environ.setdefault("JWT_SECRET_KEY", "test-jwt-secret-16ch")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://integrateai:integrateai_secret@localhost:5432/integrateai_db",
)
os.environ.setdefault("LLM_PROVIDER", "groq")
os.environ.setdefault("GROQ_API_KEY", "test-key-mocked-not-called")
os.environ.setdefault("OPENAI_API_KEY", "test-key-mocked-not-called")
os.environ.setdefault("LOG_FORMAT", "console")
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("PROMETHEUS_ENABLED", "false")