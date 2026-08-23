#!/usr/bin/env python3

"""
SmartPanel Snapshot - REST API

Provides an HTTP API for operating and configuring the SmartPanel Snapshot
application.

Current capabilities:

    GET  /
        Basic service information.

    GET  /health
        API health check.

    GET  /status
        Return the latest generated SmartPanel metrics.

    GET  /config
        Return the current SmartPanel Snapshot configuration.

    PUT  /config/network
        Update the SmartPanel discovery network.

    POST /save
        Save snapshots for all SmartPanels discovered in the configured
        network.

    POST /check
        Compare all saved SmartPanels against their current state.

    POST /restore
        Restore all SmartPanels from their saved snapshots.

    POST /restore-normalize
        Normalize configured NSA keys before restoring all snapshots.

FastAPI automatically provides interactive documentation at:

    /docs

SECURITY NOTE:
    Restore operations actively modify SmartPanel state.

    Configuration endpoints also modify application behavior.

    Authentication and authorization should be added before exposing this
    service beyond a trusted internal network.
"""

import asyncio
import ipaddress
import json

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from config import (
    CONFIG,
    PROJECT_DIR,
    load_config,
    save_config,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

APP_NAME = "SmartPanel Snapshot API"
APP_VERSION = CONFIG["api"]["version"]

METRICS_DIR = (
    PROJECT_DIR
    / CONFIG["paths"]["metrics"]
)

SAVE_SCRIPT = (
    PROJECT_DIR
    / "save_all.sh"
)

CHECK_SCRIPT = (
    PROJECT_DIR
    / "check_all.sh"
)

RESTORE_SCRIPT = (
    PROJECT_DIR
    / "restore_all.sh"
)


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------

app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description=(
        "REST API for saving, comparing, restoring, "
        "configuring, and monitoring Riedel SmartPanel snapshots."
    ),
)


# ---------------------------------------------------------------------------
# API request models
# ---------------------------------------------------------------------------

class NetworkConfigUpdate(BaseModel):
    """
    Request body used to update the SmartPanel discovery network.

    Example:

        {
            "network": "10.85.226.64/26"
        }
    """

    network: str


# ---------------------------------------------------------------------------
# Process execution
# ---------------------------------------------------------------------------

async def run_command(command):
    """
    Execute a SmartPanel Snapshot command asynchronously.
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
            "success": (
                process.returncode == 0
            ),
            "returncode": (
                process.returncode
            ),
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
                "Required SmartPanel Snapshot "
                f"command could not be found: {exc}"
            ),
        ) from exc

    except OSError as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                "Failed to execute SmartPanel "
                f"Snapshot command: {exc}"
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
    Return basic service information.
    """

    current_config = load_config()

    return {
        "service": APP_NAME,
        "version": APP_VERSION,
        "documentation": "/docs",
        "health": "/health",
        "status": "/status",
        "config": "/config",
        "network": current_config[
            "smartpanels"
        ][
            "network"
        ],
    }


@app.get(
    "/health",
    summary="API health check",
)
async def health():
    """
    Confirm that the FastAPI service itself is running.
    """

    return {
        "status": "healthy",
        "service": APP_NAME,
        "version": APP_VERSION,
    }


# ---------------------------------------------------------------------------
# Runtime configuration
# ---------------------------------------------------------------------------

@app.get(
    "/config",
    summary="Return application configuration",
)
async def get_config():
    """
    Return the current configuration directly from config.yaml.

    The file is reloaded for every request so this endpoint always reflects
    the current on-disk configuration.
    """

    try:

        return load_config()

    except (
        FileNotFoundError,
        ValueError,
    ) as exc:

        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc


@app.put(
    "/config/network",
    summary="Update SmartPanel discovery network",
)
async def update_network(
    update: NetworkConfigUpdate,
):
    """
    Update the IPv4 CIDR network used for SmartPanel discovery.

    Example request:

        {
            "network": "10.85.226.64/26"
        }

    The supplied value is validated before config.yaml is modified.
    """

    # -----------------------------------------------------------------------
    # Validate CIDR
    # -----------------------------------------------------------------------

    try:

        network = ipaddress.ip_network(
            update.network,
            strict=False,
        )

    except ValueError as exc:

        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid IP network or CIDR notation."
            ),
        ) from exc

    if network.version != 4:

        raise HTTPException(
            status_code=400,
            detail=(
                "Only IPv4 networks are currently supported."
            ),
        )

    # Normalize the supplied network.
    #
    # Example:
    #
    #   10.85.226.70/26
    #
    # becomes:
    #
    #   10.85.226.64/26
    #
    normalized_network = str(
        network
    )

    # -----------------------------------------------------------------------
    # Load current configuration
    # -----------------------------------------------------------------------

    try:

        config = load_config()

    except (
        FileNotFoundError,
        ValueError,
    ) as exc:

        raise HTTPException(
            status_code=500,
            detail=str(exc),
        ) from exc

    previous_network = config[
        "smartpanels"
    ][
        "network"
    ]

    # -----------------------------------------------------------------------
    # Update and save
    # -----------------------------------------------------------------------

    config[
        "smartpanels"
    ][
        "network"
    ] = normalized_network

    try:

        save_config(
            config
        )

    except OSError as exc:

        raise HTTPException(
            status_code=500,
            detail=(
                f"Could not write config.yaml: {exc}"
            ),
        ) from exc

    return {
        "success": True,
        "previousNetwork": previous_network,
        "network": normalized_network,
        "message": (
            "SmartPanel discovery network updated."
        ),
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
    Scan the configured SmartPanel network and save discovered panels.

    save.py reloads config.yaml when the subprocess starts, so network
    changes made through PUT /config/network automatically apply here.
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
    Compare all existing snapshots against their live SmartPanels.
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
    Restore all SmartPanels to their saved snapshot state.

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
    Normalize configured NSA keys and restore all SmartPanels.

    WARNING:
        This endpoint actively changes SmartPanel state.
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
                "file": (
                    metrics_file.name
                ),
                "error": (
                    str(exc)
                ),
            })

    return {
        "panelCount": len(panels),
        "panels": panels,
        "errors": errors,
    }


if __name__ == "__main__":
    pass
