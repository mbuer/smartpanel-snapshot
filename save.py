#!/usr/bin/env python3

"""
SmartPanel Snapshot - Save Utility

Connects to one or more Riedel SmartPanels through the HTTP Live View
WebSocket interface and saves the current Listen and Call/Talk key states
as JSON snapshots.

The script accepts either:

    - A single IPv4 address
    - An IPv4 network in CIDR notation

Examples:

    python save.py 10.85.226.96
    python save.py 10.85.226.80/28

SmartPanels are identified through the Live View WebSocket interface.
Addresses that are unreachable, do not expose the expected WebSocket
interface, or do not respond within the configured timeout are skipped.

Snapshots are written to:

    snapshots/<SmartPanel-name>.json

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


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# SmartPanel panel ID used by the Live View API.
# Current implementation assumes the primary panel has ID 0.
PANEL_ID = 0

# Maximum time allowed to establish the WebSocket connection.
CONNECT_TIMEOUT = 3

# Maximum time allowed while waiting for a specific SmartPanel response.
RESPONSE_TIMEOUT = 3

# Maximum number of SmartPanels/IP addresses queried simultaneously.
# This prevents large CIDR scans from opening hundreds of connections
# at the same time.
MAX_CONCURRENT_CONNECTIONS = 20

# Directory where generated SmartPanel snapshots are stored.
SNAPSHOT_DIR = "snapshots"


# ---------------------------------------------------------------------------
# Target handling
# ---------------------------------------------------------------------------

def expand_target(target):
    """
    Convert a command-line target into a list of IPv4 addresses.

    The target may be either a single IPv4 address:

        10.85.226.96

    or a CIDR network:

        10.85.226.80/28

    For CIDR networks, network and broadcast addresses are excluded.

    Args:
        target: IP address or CIDR network supplied by the user.

    Returns:
        List of IP addresses represented as strings.

    Raises:
        ValueError: If the supplied value is neither a valid IPv4
                    address nor a valid IPv4 CIDR network.
    """

    # First attempt to interpret the target as a single IP address.
    try:
        address = ipaddress.ip_address(target)

        if address.version != 4:
            raise ValueError("Only IPv4 addresses are currently supported.")

        return [str(address)]

    except ValueError:
        pass

    # If it isn't a single address, try interpreting it as a network.
    try:
        network = ipaddress.ip_network(target, strict=False)

        if network.version != 4:
            raise ValueError("Only IPv4 networks are currently supported.")

        return [str(ip) for ip in network.hosts()]

    except ValueError as exc:
        raise ValueError(
            f"Invalid IPv4 address or CIDR network: {target}"
        ) from exc


# ---------------------------------------------------------------------------
# WebSocket helpers
# ---------------------------------------------------------------------------

async def receive_topic(ws, expected_topic):
    """
    Wait for a specific SmartPanel Live View response.

    SmartPanel Live View may send messages for several different topics.
    This helper ignores unrelated messages until the requested response
    arrives.

    A timeout prevents the application from waiting indefinitely if the
    target device is not a SmartPanel or does not provide the expected
    response.

    Args:
        ws: Active WebSocket connection.
        expected_topic: Live View topic that should be returned.

    Returns:
        Parsed JSON message containing the expected topic.

    Raises:
        TimeoutError: If the expected response is not received in time.
        json.JSONDecodeError: If the received message is invalid JSON.
    """

    while True:
        raw_message = await asyncio.wait_for(
            ws.recv(),
            timeout=RESPONSE_TIMEOUT
        )

        message = json.loads(raw_message)

        if message.get("topic") == expected_topic:
            return message


def safe_filename(name):
    """
    Convert a SmartPanel name into a filesystem-safe filename.

    SmartPanel custom names may contain spaces or characters that are
    undesirable in filenames.

    Example:

        "Studio / Panel 1"

    becomes:

        "Studio_-_Panel_1"
    """

    name = name.strip()

    # Replace whitespace with underscores.
    name = re.sub(r"\s+", "_", name)

    # Replace characters outside a conservative filename set.
    name = re.sub(r"[^A-Za-z0-9._-]", "-", name)

    return name or "unnamed-panel"


# ---------------------------------------------------------------------------
# SmartPanel snapshot logic
# ---------------------------------------------------------------------------

async def save_panel(panel_ip, semaphore):
    """
    Retrieve and save the current state of one SmartPanel.

    The function performs three major steps:

        1. Connect to /live-view using WebSocket.
        2. Retrieve the SmartPanel name and current key state.
        3. Save the resulting state as a JSON snapshot.

    Unreachable addresses and devices that do not behave like SmartPanels
    are skipped instead of stopping the entire scan.

    Args:
        panel_ip: IPv4 address of the potential SmartPanel.
        semaphore: Asyncio semaphore controlling scan concurrency.

    Returns:
        Snapshot dictionary when successful.
        None when the address could not be processed as a SmartPanel.
    """

    uri = f"ws://{panel_ip}/live-view"

    # Limit the number of simultaneous network connections.
    async with semaphore:

        try:
            print(f"[SCAN] {panel_ip}")

            # ---------------------------------------------------------------
            # Establish SmartPanel Live View WebSocket connection
            # ---------------------------------------------------------------

            async with websockets.connect(
                uri,
                open_timeout=CONNECT_TIMEOUT
            ) as ws:

                # -----------------------------------------------------------
                # Fetch panel information
                # -----------------------------------------------------------

                await ws.send(json.dumps({
                    "topic": "/LiveView/FetchPanelInfo",
                    "body": {}
                }))

                info_message = await receive_topic(
                    ws,
                    "/LiveView/FetchPanelInfoResponse"
                )

                panels = info_message["body"]["panels"]

                if not panels:
                    raise ValueError("Panel information response is empty.")

                panel_name = panels[0]["customName"]

                # -----------------------------------------------------------
                # Fetch current panel state
                # -----------------------------------------------------------

                await ws.send(json.dumps({
                    "topic": "/LiveView/FetchPanelState",
                    "body": {
                        "panelId": PANEL_ID
                    }
                }))

                state_message = await receive_topic(
                    ws,
                    "/LiveView/FetchPanelStateResponse"
                )

                listen_keys = []
                call_keys = []

                # leverKeysLedRing contains the current LED state of the
                # SmartPanel lever keys.
                #
                # Current interpretation:
                #
                #   Upper LED green == 255
                #       Key is currently in Listen state.
                #
                #   Lower LED red == 255
                #       Key is currently in Call/Talk state.
                #
                for key in state_message["body"]["leverKeysLedRing"]:

                    key_id = key["keyId"]

                    # Listen State
                    if key["upperColor"]["green"] == 255:
                        listen_keys.append(key_id)

                    # Call / Talk State
                    if key["lowerColor"]["red"] == 255:
                        call_keys.append(key_id)

                # -----------------------------------------------------------
                # Build snapshot
                # -----------------------------------------------------------

                snapshot = {
                    "panelName": panel_name,
                    "panelIp": panel_ip,
                    "panelId": PANEL_ID,
                    "listenKeys": sorted(listen_keys),
                    "callKeys": sorted(call_keys)
                }

                # Ensure the runtime snapshot directory exists.
                os.makedirs(SNAPSHOT_DIR, exist_ok=True)

		# NOTE:
		# Snapshot filenames are based on the SmartPanel customName.
		# SmartPanel names must therefore currently be unique.
		# Duplicate names will result in snapshot file collisions.
                filename = os.path.join(
                    SNAPSHOT_DIR,
                    f"{safe_filename(panel_name)}.json"
                )

                with open(filename, "w", encoding="utf-8") as file:
                    json.dump(snapshot, file, indent=2)

                print(
                    f"[OK]   {panel_ip:<15} "
                    f"{panel_name} -> {filename}"
                )

                return snapshot

        # -------------------------------------------------------------------
        # Expected network/device failures
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
            ValueError
        ) as exc:

            print(
                f"[SKIP] {panel_ip:<15} "
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

    Expands the supplied IP/CIDR target, scans all resulting addresses,
    saves discovered SmartPanel states, and prints a summary.
    """

    if len(sys.argv) != 2:

        print()
        print("SmartPanel Snapshot - Save")
        print()
        print("Usage:")
        print("  python save.py <panel-ip>")
        print("  python save.py <CIDR-network>")
        print()
        print("Examples:")
        print("  python save.py 10.85.226.96")
        print("  python save.py 10.85.226.80/28")
        print()

        return

    # -----------------------------------------------------------------------
    # Parse target
    # -----------------------------------------------------------------------

    try:
        targets = expand_target(sys.argv[1])

    except ValueError as exc:
        print()
        print(f"Error: {exc}")
        print()
        return

    print()
    print("SmartPanel Snapshot")
    print("-------------------")
    print(f"Target addresses: {len(targets)}")
    print()

    # Semaphore prevents excessive simultaneous connections.
    semaphore = asyncio.Semaphore(MAX_CONCURRENT_CONNECTIONS)

    # Create one asynchronous task per target address.
    tasks = [
        save_panel(panel_ip, semaphore)
        for panel_ip in targets
    ]

    # Run the scans concurrently.
    results = await asyncio.gather(*tasks)

    # Count successful SmartPanel snapshots.
    successful = [
        result
        for result in results
        if result is not None
    ]

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------

    print()
    print("-------------------")
    print(f"Addresses scanned : {len(targets)}")
    print(f"SmartPanels found : {len(successful)}")
    print(f"Addresses skipped : {len(targets) - len(successful)}")
    print()


# Standard Python entry point.
if __name__ == "__main__":
    asyncio.run(main())
