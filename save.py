#!/usr/bin/env python3

"""
SmartPanel Snapshot - Save Utility

Connects to one or more Riedel SmartPanels through the HTTP Live View
WebSocket interface and saves the current Listen and Call/Talk key states
as JSON snapshots.

The script accepts either:

    - No argument:
        Uses the SmartPanel network defined in config.yaml.

    - A single IPv4 address:
        python save.py 10.85.226.96

    - An IPv4 network in CIDR notation:
        python save.py 10.85.226.64/26

SmartPanels are identified through the Live View WebSocket interface.

Addresses that are unreachable, do not expose the expected WebSocket
interface, or do not respond within the configured timeout are skipped.

Snapshots are written to the directory configured in config.yaml.

Example:

    snapshots/R3-1232.json

Generated snapshots are runtime data and are intentionally excluded
from Git through .gitignore.
"""

import asyncio
import ipaddress
import json
import os
import re
import sys

import websockets

from config import CONFIG


# ---------------------------------------------------------------------------
# SmartPanel configuration
# ---------------------------------------------------------------------------

# SmartPanel panel ID used by the Live View API.
#
# Current implementation assumes the primary panel has ID 0.
PANEL_ID = 0


# ---------------------------------------------------------------------------
# Deployment configuration loaded from config.yaml
# ---------------------------------------------------------------------------

# Default network used when save.py is run without a command-line target.
SMARTPANEL_NETWORK = CONFIG["smartpanels"]["network"]

# Maximum time allowed to establish a WebSocket connection.
CONNECT_TIMEOUT = CONFIG["smartpanels"]["connect_timeout"]

# Maximum number of IP addresses queried simultaneously.
MAX_CONCURRENT_CONNECTIONS = CONFIG["smartpanels"]["scan_concurrency"]

# Directory where generated SmartPanel snapshots are stored.
SNAPSHOT_DIR = CONFIG["paths"]["snapshots"]

# Maximum time allowed while waiting for a specific SmartPanel response.
#
# This remains an application-level setting for now rather than a
# deployment-specific config.yaml value.
RESPONSE_TIMEOUT = 3


# ---------------------------------------------------------------------------
# Target handling
# ---------------------------------------------------------------------------

def expand_target(target):
    """
    Convert a command-line target into a list of IPv4 addresses.

    The target may be either:

        10.85.226.96

    or:

        10.85.226.64/26

    For CIDR networks, the network and broadcast addresses are excluded.

    Args:
        target:
            IPv4 address or IPv4 CIDR network.

    Returns:
        List of IPv4 addresses represented as strings.

    Raises:
        ValueError:
            Target is not a valid IPv4 address or IPv4 CIDR network.
    """

    # -----------------------------------------------------------------------
    # First try interpreting the value as a single IPv4 address.
    # -----------------------------------------------------------------------

    try:

        address = ipaddress.ip_address(
            target
        )

        if address.version != 4:
            raise ValueError(
                "Only IPv4 addresses are currently supported."
            )

        return [
            str(address)
        ]

    except ValueError:
        pass

    # -----------------------------------------------------------------------
    # Otherwise try interpreting the value as an IPv4 network.
    # -----------------------------------------------------------------------

    try:

        network = ipaddress.ip_network(
            target,
            strict=False,
        )

        if network.version != 4:
            raise ValueError(
                "Only IPv4 networks are currently supported."
            )

        return [
            str(ip)
            for ip in network.hosts()
        ]

    except ValueError as exc:

        raise ValueError(
            f"Invalid IPv4 address or CIDR network: {target}"
        ) from exc


# ---------------------------------------------------------------------------
# WebSocket helpers
# ---------------------------------------------------------------------------

async def receive_topic(
    ws,
    expected_topic,
):
    """
    Wait for a specific SmartPanel Live View response topic.

    SmartPanel Live View may send messages for multiple topics.
    Unrelated messages are ignored until the requested response arrives.

    A timeout prevents the program from waiting indefinitely.

    Args:
        ws:
            Active WebSocket connection.

        expected_topic:
            Live View response topic expected from the SmartPanel.

    Returns:
        Parsed JSON response.

    Raises:
        TimeoutError:
            Expected response was not received within RESPONSE_TIMEOUT.
    """

    while True:

        raw_message = await asyncio.wait_for(
            ws.recv(),
            timeout=RESPONSE_TIMEOUT,
        )

        # Binary messages are currently not required for snapshot saving.
        if isinstance(
            raw_message,
            bytes,
        ):
            continue

        try:

            message = json.loads(
                raw_message
            )

        except json.JSONDecodeError:
            continue

        if message.get(
            "topic"
        ) == expected_topic:

            return message


def safe_filename(name):
    """
    Convert a SmartPanel custom name into a filesystem-safe filename.

    Example:

        Studio / Panel 1

    becomes:

        Studio_-_Panel_1

    Args:
        name:
            SmartPanel customName.

    Returns:
        Filesystem-safe filename component.
    """

    name = name.strip()

    # Replace whitespace with underscores.
    name = re.sub(
        r"\s+",
        "_",
        name,
    )

    # Replace characters outside a conservative filename set.
    name = re.sub(
        r"[^A-Za-z0-9._-]",
        "-",
        name,
    )

    return (
        name
        or "unnamed-panel"
    )


# ---------------------------------------------------------------------------
# SmartPanel snapshot logic
# ---------------------------------------------------------------------------

async def save_panel(
    panel_ip,
    semaphore,
):
    """
    Retrieve and save the current state of one SmartPanel.

    The function:

        1. Connects to /live-view.
        2. Retrieves SmartPanel information.
        3. Retrieves the current key state.
        4. Saves the resulting state as JSON.

    Unreachable addresses and devices that do not behave like compatible
    SmartPanels are skipped instead of stopping the entire scan.

    Args:
        panel_ip:
            IPv4 address of the potential SmartPanel.

        semaphore:
            Asyncio semaphore controlling scan concurrency.

    Returns:
        Snapshot dictionary when successful.

        None when the target could not be processed as a SmartPanel.
    """

    uri = (
        f"ws://{panel_ip}/live-view"
    )

    # Limit simultaneous network connections.
    async with semaphore:

        try:

            print(
                f"[SCAN] {panel_ip}"
            )

            # ---------------------------------------------------------------
            # Establish SmartPanel Live View WebSocket connection
            # ---------------------------------------------------------------

            async with websockets.connect(
                uri,
                open_timeout=CONNECT_TIMEOUT,
            ) as ws:

                # -----------------------------------------------------------
                # Fetch panel information
                # -----------------------------------------------------------

                await ws.send(
                    json.dumps({
                        "topic": "/LiveView/FetchPanelInfo",
                        "body": {},
                    })
                )

                info_message = await receive_topic(
                    ws,
                    "/LiveView/FetchPanelInfoResponse",
                )

                panels = (
                    info_message[
                        "body"
                    ][
                        "panels"
                    ]
                )

                if not panels:

                    raise ValueError(
                        "Panel information response is empty."
                    )

                panel_name = (
                    panels[0][
                        "customName"
                    ]
                )

                # -----------------------------------------------------------
                # Fetch current panel state
                # -----------------------------------------------------------

                await ws.send(
                    json.dumps({
                        "topic": "/LiveView/FetchPanelState",
                        "body": {
                            "panelId": PANEL_ID,
                        },
                    })
                )

                state_message = await receive_topic(
                    ws,
                    "/LiveView/FetchPanelStateResponse",
                )

                listen_keys = []
                call_keys = []

                # -----------------------------------------------------------
                # Interpret lever key LED state
                # -----------------------------------------------------------
                #
                # Current Live View interpretation:
                #
                #   upperColor.green == 255
                #       Listen is active.
                #
                #   lowerColor.red == 255
                #       Call/Talk is active.
                #
                # -----------------------------------------------------------

                for key in (
                    state_message[
                        "body"
                    ][
                        "leverKeysLedRing"
                    ]
                ):

                    key_id = (
                        key["keyId"]
                    )

                    # Listen state
                    if (
                        key[
                            "upperColor"
                        ][
                            "green"
                        ]
                        == 255
                    ):

                        listen_keys.append(
                            key_id
                        )

                    # Call / Talk state
                    if (
                        key[
                            "lowerColor"
                        ][
                            "red"
                        ]
                        == 255
                    ):

                        call_keys.append(
                            key_id
                        )

                # -----------------------------------------------------------
                # Build snapshot
                # -----------------------------------------------------------

                snapshot = {
                    "panelName": panel_name,
                    "panelIp": panel_ip,
                    "panelId": PANEL_ID,
                    "listenKeys": sorted(
                        listen_keys
                    ),
                    "callKeys": sorted(
                        call_keys
                    ),
                }

                # Ensure the runtime snapshot directory exists.
                os.makedirs(
                    SNAPSHOT_DIR,
                    exist_ok=True,
                )

                # NOTE:
                # Snapshot filenames are based on the SmartPanel customName.
                # SmartPanel names must therefore currently be unique.
                # Duplicate names will result in snapshot file collisions.
                filename = os.path.join(
                    SNAPSHOT_DIR,
                    f"{safe_filename(panel_name)}.json",
                )

                with open(
                    filename,
                    "w",
                    encoding="utf-8",
                ) as file:

                    json.dump(
                        snapshot,
                        file,
                        indent=2,
                    )

                print(
                    f"[OK]   "
                    f"{panel_ip:<15} "
                    f"{panel_name} "
                    f"-> {filename}"
                )

                return snapshot

        # -------------------------------------------------------------------
        # Expected network / device failures
        # -------------------------------------------------------------------

        except (
            TimeoutError,
            asyncio.TimeoutError,
            ConnectionRefusedError,
            OSError,
            websockets.WebSocketException,
            json.JSONDecodeError,
            KeyError,
            IndexError,
            TypeError,
            ValueError,
        ) as exc:

            print(
                f"[SKIP] "
                f"{panel_ip:<15} "
                f"No compatible SmartPanel response "
                f"({type(exc).__name__})"
            )

            return None


# ---------------------------------------------------------------------------
# Main application
# ---------------------------------------------------------------------------

async def main():
    """
    Command-line entry point.

    When no argument is supplied, the SmartPanel network from config.yaml
    is scanned.

    A single IP address or CIDR network may also be supplied as a temporary
    command-line override.
    """

    # -----------------------------------------------------------------------
    # Validate command-line arguments
    # -----------------------------------------------------------------------

    if len(sys.argv) > 2:

        print()
        print(
            "SmartPanel Snapshot - Save"
        )
        print()
        print("Usage:")
        print(
            "  python save.py"
        )
        print(
            "  python save.py <panel-ip>"
        )
        print(
            "  python save.py <CIDR-network>"
        )
        print()
        print("Examples:")
        print(
            "  python save.py"
        )
        print(
            "  python save.py 10.85.226.96"
        )
        print(
            "  python save.py 10.85.226.64/26"
        )
        print()

        return

    # -----------------------------------------------------------------------
    # Determine target
    # -----------------------------------------------------------------------

    if len(sys.argv) == 2:

        # Explicit command-line target overrides config.yaml.
        target = sys.argv[1]

        target_source = (
            "command line"
        )

    else:

        # No target supplied: use deployment configuration.
        target = SMARTPANEL_NETWORK

        target_source = (
            "config.yaml"
        )

    # -----------------------------------------------------------------------
    # Expand target into individual host addresses
    # -----------------------------------------------------------------------

    try:

        targets = expand_target(
            target
        )

    except ValueError as exc:

        print()
        print(
            f"Error: {exc}"
        )
        print()

        return

    # -----------------------------------------------------------------------
    # Scan summary
    # -----------------------------------------------------------------------

    print()
    print(
        "SmartPanel Snapshot"
    )
    print(
        "-------------------"
    )
    print(
        f"Target          : {target}"
    )
    print(
        f"Target source   : {target_source}"
    )
    print(
        f"Target addresses: {len(targets)}"
    )
    print(
        f"Concurrency     : {MAX_CONCURRENT_CONNECTIONS}"
    )
    print()

    # -----------------------------------------------------------------------
    # Run SmartPanel scans
    # -----------------------------------------------------------------------

    semaphore = asyncio.Semaphore(
        MAX_CONCURRENT_CONNECTIONS
    )

    tasks = [
        save_panel(
            panel_ip,
            semaphore,
        )
        for panel_ip in targets
    ]

    results = await asyncio.gather(
        *tasks
    )

    # Count successful SmartPanel snapshots.
    successful = [
        result
        for result in results
        if result is not None
    ]

    # -----------------------------------------------------------------------
    # Final summary
    # -----------------------------------------------------------------------

    print()
    print(
        "-------------------"
    )
    print(
        f"Addresses scanned : {len(targets)}"
    )
    print(
        f"SmartPanels found : {len(successful)}"
    )
    print(
        f"Addresses skipped : "
        f"{len(targets) - len(successful)}"
    )
    print()


# ---------------------------------------------------------------------------
# Standard Python entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    asyncio.run(main())
