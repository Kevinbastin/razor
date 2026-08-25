"""
Agent Transaction Risk Layer — FastAPI Entry Point

Usage:
    uvicorn main:app --reload
"""

import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import structlog

from logging_config import setup_logging

# Load environment variables from .env (if present)
load_dotenv()

# Initialize structured logging
setup_logging(log_level=os.getenv("LOG_LEVEL", "INFO"))

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — startup and shutdown hooks."""
    logger.info(
        "server_startup",
        service="agent-transaction-risk-layer",
        env=os.getenv("APP_ENV", "development"),
    )
    yield
    logger.info("server_shutdown")


app = FastAPI(
    title="Agent Transaction Risk Layer",
    description=(
        "Four-layer risk system for AI-agent-initiated payments. "
        "Built for the Razorpay AI Builder Internship — Track 02: AI Risk Manager."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# CORS for console frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    """Basic health check endpoint."""
    return {
        "status": "healthy",
        "service": "agent-transaction-risk-layer",
        "version": "0.1.0",
    }
