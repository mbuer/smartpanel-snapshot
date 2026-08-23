#!/usr/bin/env python3

"""
SmartPanel Snapshot - REST API

Provides an HTTP API for operating the SmartPanel Snapshot application.

Current capabilities:

    GET  /
        Basic service information.

    GET  /health
        API health check.

    GET  /status
        Return the latest generated SmartPanel metrics.

    POST /save
        Save snapshots for all configured/discovered SmartPanels.

    POST /check
        Compare all saved SmartPanels against their current state.

    POST /restore
        Restore all SmartPanels from their saved snapshots.

    POST /restore-normalize
        Normalize configured NSA keys before restoring all snapshots.

The API acts as an orchestration layer around the existing SmartPanel
Snapshot scripts.

FastAPI automatically provides interactive API documentation at:

    /docs

and the generated OpenAPI schema at:

    /openapi.json

Example:

    uvicorn smartpanel_api:app --host 0.0.0.0 --port 8081

SECURITY NOTE:
    Restore operations actively modify SmartPanel state.

    Authentication and authorization should be added before exposing this
    service beyond a trusted internal network.
"""

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, HTTPException


# ---------------------------------------------------------------------------
# Application configuration
# ---------------------------------------------------------------------------

APP_NAME = "SmartPanel Snapshot API"
APP_VERSION = "1.1.0"

# Resolve paths relative to this Python file rather than the shell's
# current working directory.
#
# This makes the API more reliable when started through systemd,
# Docker, another shell location, or a future deployment mechanism.
PROJECT_DIR = Path(__file__).resolve().parent

METRICS_DIR = PROJECT_DIR / "metrics"

SAVE_SCRIPT = PROJECT_DIR / "save_all.sh"
CHECK_SCRIPT = PROJECT_DIR / "check_all.sh"
RESTORE_SCRIPT = PROJECT_DIR / "restore_all.sh"


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description=(
        "REST API for saving, comparing, restoring, "
        "and monitoring Riedel SmartPanel snapshots."
    ),
)


# ---------------------------------------------------------------------------
# Process execution
# ---------------------------------------------------------------------------

async def run_command(command):
    """
    Execute a SmartPanel Snapshot command asynchronously.

    Using asyncio subprocesses prevents a long-running SmartPanel operation
    from blocking the FastAPI server itself.

    Args:
        command:
            List containing executable and command-line arguments.

    Returns:
        Dictionary containing command result information.

    Example result:

        {
            "success": True,
            "returncode": 0,
            "stdout": "...",
            "stderr": ""
        }
    """

    try:

        process = await asyncio.create_subprocess_exec(
            *map(str, command),
            cwd=PROJECT_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stdout, stderr = await process.communicate()

        return {
            "success": process.returncode == 0,
            "returncode": process.returncode,
            "stdout": stdout.decode(
                errors="replace"
            ),
            "stderr": stderr.decode(
                errors="replace"
            ),
        }

    except FileNotFoundError as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                "Required SmartPanel Snapshot command "
                f"could not be found: {exc}"
            ),
        ) from exc

    except OSError as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                "Failed to execute SmartPanel Snapshot "
                f"command: {exc}"
            ),
        ) from exc


# ---------------------------------------------------------------------------
# Service information
# ---------------------------------------------------------------------------

@app.get(
    "/",
    summary="Service information",
)
async def root():
    """
    Return basic information about the SmartPanel Snapshot API.
    """

    return {
        "service": APP_NAME,
        "version": APP_VERSION,
        "documentation": "/docs",
        "health": "/health",
        "status": "/status",
    }


@app.get(
    "/health",
    summary="API health check",
)
async def health():
    """
    Confirm that the FastAPI service itself is running.

    This does not verify SmartPanel connectivity.
    """

    return {
        "status": "healthy",
        "service": APP_NAME,
        "version": APP_VERSION,
    }


# ---------------------------------------------------------------------------
# SmartPanel operations
# ---------------------------------------------------------------------------

@app.post(
    "/save",
    summary="Save SmartPanel snapshots",
)
async def save():
    """
    Execute the configured save-all operation.
    """

    return await run_command([
        SAVE_SCRIPT
    ])


@app.post(
    "/check",
    summary="Check SmartPanel compliance",
)
async def check():
    """
    Compare saved SmartPanel snapshots against current panel state.
    """

    return await run_command([
        CHECK_SCRIPT
    ])


@app.post(
    "/restore",
    summary="Restore SmartPanel snapshots",
)
async def restore():
    """
    Restore SmartPanels to their saved snapshot state.

    WARNING:
        This endpoint actively changes SmartPanel state.
    """

    return await run_command([
        RESTORE_SCRIPT
    ])


@app.post(
    "/restore-normalize",
    summary="Normalize and restore SmartPanels",
)
async def restore_normalize():
    """
    Normalize configured NSA keys and restore SmartPanel snapshots.

    WARNING:
        This endpoint actively changes SmartPanel state and uses
        layout-dependent touchscreen coordinates.
    """

    return await run_command([
        RESTORE_SCRIPT,
        "--normalize",
    ])


# ---------------------------------------------------------------------------
# Metrics / status
# ---------------------------------------------------------------------------

@app.get(
    "/status",
    summary="Return latest SmartPanel status",
)
async def status():
    """
    Return the latest comparison metrics for all SmartPanels.

    Metrics are read from:

        metrics/*.json

    Invalid or unreadable metric files are reported separately rather
    than causing the entire endpoint to fail.
    """

    panels = []
    errors = []

    if not METRICS_DIR.exists():

        return {
            "panelCount": 0,
            "panels": [],
            "errors": [],
        }

    for metrics_file in sorted(
        METRICS_DIR.glob("*.json")
    ):

        try:

            with metrics_file.open(
                "r",
                encoding="utf-8",
            ) as file:

                panels.append(
                    json.load(file)
                )

        except (
            OSError,
            json.JSONDecodeError,
        ) as exc:

            errors.append({
                "file": metrics_file.name,
                "error": str(exc),
            })

    return {
        "panelCount": len(panels),
        "panels": panels,
        "errors": errors,
    }
