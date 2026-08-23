#!/usr/bin/env python3

"""
SmartPanel Snapshot - Restore Utility

Restores the Listen and Call/Talk key state of a Riedel SmartPanel from
a previously saved SmartPanel Snapshot JSON file.

The restore process:

    1. Loads and validates the snapshot.
    2. Connects to the SmartPanel stored in the snapshot.
    3. Verifies the connected panel identity.
    4. Reads the current key state.
    5. Calculates configuration drift.
    6. Removes unwanted Listen/Call states.
    7. Restores missing Listen/Call states.
    8. Reads the panel again and verifies the final state.

Example:

    python restore.py snapshots/R3-1232.json

Optional NSA normalization:

    python restore.py snapshots/R3-1232.json --normalize

Compliance model:

    Compliance percentage represents how many expected key states are
    currently correct.

    Status is stricter:

        COMPLIANT
            No missing or extra Listen/Call states exist.

        NON-COMPLIANT
            At least one missing or extra state exists.

    Example:

        Expected Listen : [6, 16, 18]
        Current Listen  : [6, 16, 17, 20]

        Correct expected states : 2 of 3
        Compliance              : 66.7%
        Status                  : NON-COMPLIANT

IMPORTANT:
    Restore actively changes SmartPanel key states through the Live View
    WebSocket API.

    The --normalize option additionally simulates touchscreen operations
    using hard-coded normalized display coordinates. These coordinates are
    specific to the currently tested SmartPanel layout and should be reviewed
    before use with different layouts, firmware versions, or products.
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

import websockets


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Maximum time allowed to establish the WebSocket connection.
CONNECT_TIMEOUT = 3

# Maximum time allowed while waiting for a SmartPanel response.
RESPONSE_TIMEOUT = 3

# Delay after optional NSA normalization before reading panel state.
NORMALIZE_SETTLE_TIME = 3

# Timing used when simulating lever operations.
LEVER_HOLD_TIME = 0.1
LEVER_SETTLE_TIME = 0.2


# ---------------------------------------------------------------------------
# NSA normalization coordinates
# ---------------------------------------------------------------------------
#
# These coordinates were determined for the currently tested SmartPanel
# Live View layout.
#
# Each entry contains:
#
#   select:
#       Coordinates used to open the NSA key menu.
#
#   action:
#       Coordinates used to select the normalization action.
#
# IMPORTANT:
# These values are UI/layout dependent. They are NOT a generic SmartPanel
# protocol definition.
#

NORMALIZE_KEYS = [
    {
        "name": "NSA02-01",
        "select": (0.07758268397878876, 0.252314066134677),
        "action": (0.18610231308186967, 0.29316955261836136),
    },
    {
        "name": "NSA02-02",
        "select": (0.1840548029741338, 0.23188643978109633),
        "action": (0.3417155182239178, 0.252314066134677),
    },
    {
        "name": "NSA02-03",
        "select": (0.30281217007390343, 0.18081725700888315),
        "action": (0.4338548790071024, 0.19103095329741202),
    },
    {
        "name": "NSA02-04",
        "select": (0.4481877309482676, 0.22167250971604452),
        "action": (0.597658124580094, 0.252314066134677),
    },
    {
        "name": "NSA02-05",
        "select": (0.5669450043190325, 0.24210036984614816),
        "action": (0.31509741817832804, 0.26252799619972883),
    },
    {
        "name": "NSA02-06",
        "select": (0.6938926930367597, 0.252314066134677),
        "action": (0.43999750305931473, 0.22167250971604452),
    },
]


# ---------------------------------------------------------------------------
# Snapshot handling
# ---------------------------------------------------------------------------

def load_snapshot(snapshot_path):
    """
    Load and validate a SmartPanel snapshot file.

    Required snapshot fields:

        panelName
        panelIp
        panelId
        listenKeys
        callKeys

    Args:
        snapshot_path:
            Path to the JSON snapshot file.

    Returns:
        Validated snapshot dictionary.

    Raises:
        FileNotFoundError:
            Snapshot does not exist.

        ValueError:
            Snapshot structure is invalid.

        json.JSONDecodeError:
            Snapshot does not contain valid JSON.
    """

    path = Path(snapshot_path)

    if not path.is_file():
        raise FileNotFoundError(
            f"Snapshot file not found: {snapshot_path}"
        )

    with path.open("r", encoding="utf-8") as file:
        snapshot = json.load(file)

    required_fields = {
        "panelName",
        "panelIp",
        "panelId",
        "listenKeys",
        "callKeys",
    }

    missing_fields = required_fields - snapshot.keys()

    if missing_fields:
        raise ValueError(
            "Snapshot is missing required field(s): "
            + ", ".join(sorted(missing_fields))
        )

    if not isinstance(snapshot["listenKeys"], list):
        raise ValueError("listenKeys must be a list.")

    if not isinstance(snapshot["callKeys"], list):
        raise ValueError("callKeys must be a list.")

    if not all(
        isinstance(key, int)
        for key in snapshot["listenKeys"]
    ):
        raise ValueError(
            "All listenKeys entries must be integers."
        )

    if not all(
        isinstance(key, int)
        for key in snapshot["callKeys"]
    ):
        raise ValueError(
            "All callKeys entries must be integers."
        )

    return snapshot


# ---------------------------------------------------------------------------
# WebSocket helpers
# ---------------------------------------------------------------------------

async def receive_topic(ws, expected_topic):
    """
    Wait for a specific SmartPanel Live View response topic.

    SmartPanel Live View may send unrelated messages while a request is
    being processed. Those messages are ignored.

    A timeout prevents the restore process from waiting indefinitely.

    Args:
        ws:
            Active WebSocket connection.

        expected_topic:
            Live View topic expected from the SmartPanel.

    Returns:
        Parsed JSON response.

    Raises:
        TimeoutError:
            Expected response was not received in time.
    """

    while True:

        raw = await asyncio.wait_for(
            ws.recv(),
            timeout=RESPONSE_TIMEOUT,
        )

        # Some Live View messages may arrive as binary data.
        # Restore currently processes only JSON text messages.
        if isinstance(raw, bytes):
            continue

        try:
            message = json.loads(raw)

        except json.JSONDecodeError:
            continue

        if message.get("topic") == expected_topic:
            return message


async def get_panel_name(ws):
    """
    Retrieve the SmartPanel custom name through Live View.

    Returns:
        SmartPanel customName string.
    """

    await ws.send(json.dumps({
        "topic": "/LiveView/FetchPanelInfo",
        "body": {}
    }))

    message = await receive_topic(
        ws,
        "/LiveView/FetchPanelInfoResponse",
    )

    panels = message["body"]["panels"]

    if not panels:
        raise ValueError(
            "SmartPanel returned an empty panel information response."
        )

    return panels[0]["customName"]


async def get_current_state(ws, panel_id):
    """
    Retrieve the current Listen and Call/Talk state.

    Live View represents the key state through LED-ring color values.

    Current interpretation:

        upperColor.green == 255
            Listen is active.

        lowerColor.red == 255
            Call/Talk is active.

    Args:
        ws:
            Active SmartPanel WebSocket connection.

        panel_id:
            SmartPanel panel ID stored in the snapshot.

    Returns:
        Dictionary containing sorted listenKeys and callKeys lists.
    """

    await ws.send(json.dumps({
        "topic": "/LiveView/FetchPanelState",
        "body": {
            "panelId": panel_id
        }
    }))

    message = await receive_topic(
        ws,
        "/LiveView/FetchPanelStateResponse",
    )

    listen_keys = []
    call_keys = []

    for key in message["body"]["leverKeysLedRing"]:

        key_id = key["keyId"]

        if key["upperColor"]["green"] == 255:
            listen_keys.append(key_id)

        if key["lowerColor"]["red"] == 255:
            call_keys.append(key_id)

    return {
        "listenKeys": sorted(listen_keys),
        "callKeys": sorted(call_keys),
    }


# ---------------------------------------------------------------------------
# Drift and compliance calculation
# ---------------------------------------------------------------------------

def calculate_drift(
    desired_listen,
    desired_call,
    current_listen,
    current_call,
):
    """
    Compare desired snapshot state with current SmartPanel state.

    Returns:

        missing_listen
            Listen keys required by the snapshot but currently inactive.

        extra_listen
            Listen keys currently active but absent from the snapshot.

        missing_call
            Call/Talk keys required by the snapshot but currently inactive.

        extra_call
            Call/Talk keys currently active but absent from the snapshot.

        compliance
            Percentage of expected states currently correct.

        compliant
            Boolean indicating whether the current state exactly matches
            the snapshot.

    Compliance and compliance status intentionally represent two different
    concepts.

    Compliance percentage:
        Measures how many expected key states are currently correct.

    Compliant status:
        Requires an exact configuration match with no missing or extra
        Listen/Call states.

    Example:

        Expected Listen : [6, 16, 18]
        Current Listen  : [6, 16, 17, 20]

        2 of 3 expected states are correct.

        compliance = 66.7
        compliant  = False
    """

    desired_listen = set(desired_listen)
    desired_call = set(desired_call)

    current_listen = set(current_listen)
    current_call = set(current_call)

    # -----------------------------------------------------------------------
    # Determine configuration drift
    # -----------------------------------------------------------------------

    missing_listen = sorted(
        desired_listen - current_listen
    )

    extra_listen = sorted(
        current_listen - desired_listen
    )

    missing_call = sorted(
        desired_call - current_call
    )

    extra_call = sorted(
        current_call - desired_call
    )

    # -----------------------------------------------------------------------
    # Calculate compliance percentage
    # -----------------------------------------------------------------------

    expected_total = (
        len(desired_listen)
        + len(desired_call)
    )

    if expected_total == 0:

        # An empty desired state is 100% correct only if there are also
        # no unexpected active states.
        if current_listen or current_call:
            compliance = 0.0
        else:
            compliance = 100.0

    else:

        correct_listen = len(
            desired_listen.intersection(
                current_listen
            )
        )

        correct_call = len(
            desired_call.intersection(
                current_call
            )
        )

        correct_total = (
            correct_listen
            + correct_call
        )

        compliance = round(
            (correct_total / expected_total) * 100,
            1,
        )

    # -----------------------------------------------------------------------
    # Strict compliance state
    # -----------------------------------------------------------------------
    #
    # A panel is only considered fully compliant if there is no drift.
    #

    compliant = not any([
        missing_listen,
        extra_listen,
        missing_call,
        extra_call,
    ])

    return {
        "missing_listen": missing_listen,
        "extra_listen": extra_listen,
        "missing_call": missing_call,
        "extra_call": extra_call,
        "compliance": compliance,
        "compliant": compliant,
    }


# ---------------------------------------------------------------------------
# SmartPanel control operations
# ---------------------------------------------------------------------------

async def toggle_listen_key(
    ws,
    panel_id,
    key_id,
):
    """
    Toggle a SmartPanel Listen state.

    Listen is controlled by simulating an upward lever movement followed
    by release.
    """

    await ws.send(json.dumps({
        "topic": "/LiveView/SimulateLever",
        "body": {
            "panelId": panel_id,
            "keyId": key_id,
            "leverState": "Up"
        }
    }))

    await asyncio.sleep(
        LEVER_HOLD_TIME
    )

    await ws.send(json.dumps({
        "topic": "/LiveView/SimulateLever",
        "body": {
            "panelId": panel_id,
            "keyId": key_id,
            "leverState": "Released"
        }
    }))

    await asyncio.sleep(
        LEVER_SETTLE_TIME
    )


async def toggle_call_key(
    ws,
    panel_id,
    key_id,
):
    """
    Toggle a SmartPanel Call/Talk state.

    Call/Talk is controlled by simulating a downward lever movement
    followed by release.
    """

    await ws.send(json.dumps({
        "topic": "/LiveView/SimulateLever",
        "body": {
            "panelId": panel_id,
            "keyId": key_id,
            "leverState": "Down"
        }
    }))

    await asyncio.sleep(
        LEVER_HOLD_TIME
    )

    await ws.send(json.dumps({
        "topic": "/LiveView/SimulateLever",
        "body": {
            "panelId": panel_id,
            "keyId": key_id,
            "leverState": "Released"
        }
    }))

    await asyncio.sleep(
        LEVER_SETTLE_TIME
    )


# ---------------------------------------------------------------------------
# NSA normalization
# ---------------------------------------------------------------------------

async def touch(
    ws,
    panel_id,
    x,
    y,
    hold_time=0.25,
):
    """
    Simulate one touchscreen press and release.

    Coordinates use the normalized Live View display coordinate system.
    """

    await ws.send(json.dumps({
        "topic": "/LiveView/SimulateTouch",
        "body": {
            "panelId": panel_id,
            "displayId": 1,
            "x": x,
            "y": y,
            "touchState": "Pressed"
        }
    }))

    await asyncio.sleep(
        hold_time
    )

    await ws.send(json.dumps({
        "topic": "/LiveView/SimulateTouch",
        "body": {
            "panelId": panel_id,
            "displayId": 1,
            "x": x,
            "y": y,
            "touchState": "Released"
        }
    }))


async def normalize_all(
    ws,
    panel_id,
):
    """
    Normalize the configured NSA keys.

    WARNING:
        This function relies on hard-coded UI coordinates defined in
        NORMALIZE_KEYS. It should be considered layout-dependent.
    """

    print()
    print("Normalizing NSA keys...")
    print()

    for key in NORMALIZE_KEYS:

        print(
            f"[{key['name']}] Opening menu"
        )

        await touch(
            ws,
            panel_id,
            key["select"][0],
            key["select"][1],
            hold_time=0.8,
        )

        await asyncio.sleep(
            0.2
        )

        print(
            f"[{key['name']}] Normalize"
        )

        await touch(
            ws,
            panel_id,
            key["action"][0],
            key["action"][1],
            hold_time=0.15,
        )

        await asyncio.sleep(
            0.3
        )

    print()
    print("Normalization complete")


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_drift_report(
    drift,
    title="Drift Analysis",
):
    """
    Print human-readable drift, compliance score, and compliance status.
    """

    print()
    print(
        f"=== {title} ==="
    )

    print(
        f"Missing Listen : "
        f"{drift['missing_listen']}"
    )

    print(
        f"Extra Listen   : "
        f"{drift['extra_listen']}"
    )

    print(
        f"Missing Call   : "
        f"{drift['missing_call']}"
    )

    print(
        f"Extra Call     : "
        f"{drift['extra_call']}"
    )

    print(
        f"Compliance     : "
        f"{drift['compliance']}%"
    )

    status = (
        "COMPLIANT"
        if drift["compliant"]
        else "NON-COMPLIANT"
    )

    print(
        f"Status         : {status}"
    )


# ---------------------------------------------------------------------------
# Restore process
# ---------------------------------------------------------------------------

async def restore_snapshot(
    snapshot,
    normalize=False,
):
    """
    Restore one SmartPanel from a validated snapshot.

    This function performs all active SmartPanel modifications.
    """

    panel_name = snapshot["panelName"]
    panel_ip = snapshot["panelIp"]
    panel_id = snapshot["panelId"]

    desired_listen = snapshot["listenKeys"]
    desired_call = snapshot["callKeys"]

    uri = f"ws://{panel_ip}/live-view"

    print()
    print("====================================")
    print(f"Panel    : {panel_name}")
    print(f"IP       : {panel_ip}")
    print(f"Panel ID : {panel_id}")
    print("====================================")

    print()
    print(
        f"Connecting to {uri}"
    )

    async with websockets.connect(
        uri,
        open_timeout=CONNECT_TIMEOUT,
    ) as ws:

        # -------------------------------------------------------------------
        # Safety check:
        # Verify the connected SmartPanel before changing anything.
        # -------------------------------------------------------------------

        connected_name = await get_panel_name(
            ws
        )

        print(
            f"Connected SmartPanel: "
            f"{connected_name}"
        )

        if connected_name != panel_name:

            raise RuntimeError(
                "SmartPanel identity mismatch. "
                f"Snapshot expects '{panel_name}', "
                f"but device reports '{connected_name}'. "
                "Restore aborted."
            )

        print(
            "Panel identity verified."
        )

        # -------------------------------------------------------------------
        # Optional NSA normalization
        # -------------------------------------------------------------------

        if normalize:

            await normalize_all(
                ws,
                panel_id,
            )

            print()
            print(
                "Waiting for panel to settle..."
            )

            await asyncio.sleep(
                NORMALIZE_SETTLE_TIME
            )

        # -------------------------------------------------------------------
        # Read current SmartPanel state
        # -------------------------------------------------------------------

        start = time.monotonic()

        current = await get_current_state(
            ws,
            panel_id,
        )

        elapsed = (
            time.monotonic()
            - start
        )

        print()
        print(
            f"Current state fetched in "
            f"{elapsed:.2f}s"
        )

        # -------------------------------------------------------------------
        # Calculate initial drift
        # -------------------------------------------------------------------

        drift = calculate_drift(
            desired_listen,
            desired_call,
            current["listenKeys"],
            current["callKeys"],
        )

        print_drift_report(
            drift,
            title="Drift Analysis",
        )

        removed_listen = 0
        removed_call = 0

        added_listen = 0
        added_call = 0

        # -------------------------------------------------------------------
        # Remove states that are not present in the saved snapshot.
        # -------------------------------------------------------------------

        for key in drift["extra_listen"]:

            print(
                f"Removing LISTEN {key}"
            )

            await toggle_listen_key(
                ws,
                panel_id,
                key,
            )

            removed_listen += 1

        for key in drift["extra_call"]:

            print(
                f"Removing CALL {key}"
            )

            await toggle_call_key(
                ws,
                panel_id,
                key,
            )

            removed_call += 1

        # -------------------------------------------------------------------
        # Restore states missing from the current SmartPanel.
        # -------------------------------------------------------------------

        for key in drift["missing_listen"]:

            print(
                f"Restoring LISTEN {key}"
            )

            await toggle_listen_key(
                ws,
                panel_id,
                key,
            )

            added_listen += 1

        for key in drift["missing_call"]:

            print(
                f"Restoring CALL {key}"
            )

            await toggle_call_key(
                ws,
                panel_id,
                key,
            )

            added_call += 1

        # -------------------------------------------------------------------
        # Verify the resulting SmartPanel state.
        # -------------------------------------------------------------------

        print()
        print(
            "Verifying..."
        )

        final = await get_current_state(
            ws,
            panel_id,
        )

    # -----------------------------------------------------------------------
    # Calculate final compliance
    # -----------------------------------------------------------------------

    final_drift = calculate_drift(
        desired_listen,
        desired_call,
        final["listenKeys"],
        final["callKeys"],
    )

    # -----------------------------------------------------------------------
    # Final report
    # -----------------------------------------------------------------------

    print()
    print("====================================")

    print(
        f"Removed Listen : {removed_listen}"
    )

    print(
        f"Removed Call   : {removed_call}"
    )

    print(
        f"Added Listen   : {added_listen}"
    )

    print(
        f"Added Call     : {added_call}"
    )

    print("------------------------------------")

    print(
        f"Compliance     : "
        f"{final_drift['compliance']}%"
    )

    final_status = (
        "COMPLIANT"
        if final_drift["compliant"]
        else "NON-COMPLIANT"
    )

    print(
        f"Status         : "
        f"{final_status}"
    )

    print("------------------------------------")

    print(
        f"Missing Listen : "
        f"{final_drift['missing_listen']}"
    )

    print(
        f"Extra Listen   : "
        f"{final_drift['extra_listen']}"
    )

    print(
        f"Missing Call   : "
        f"{final_drift['missing_call']}"
    )

    print(
        f"Extra Call     : "
        f"{final_drift['extra_call']}"
    )

    if final_drift["compliant"]:

        print()
        print("SUCCESS ✅")

    else:

        print()
        print("WARNING ⚠️")

    print(
        "===================================="
    )

    return final_drift


# ---------------------------------------------------------------------------
# Command-line entry point
# ---------------------------------------------------------------------------

async def main():
    """
    Parse command-line arguments and execute the restore operation.
    """

    parser = argparse.ArgumentParser(
        description=(
            "Restore SmartPanel Listen and Call/Talk "
            "state from a saved snapshot."
        )
    )

    parser.add_argument(
        "snapshot",
        help=(
            "Path to SmartPanel snapshot JSON file."
        ),
    )

    parser.add_argument(
        "--normalize",
        action="store_true",
        help=(
            "Normalize configured NSA keys before "
            "restoring the snapshot."
        ),
    )

    args = parser.parse_args()

    # -----------------------------------------------------------------------
    # Load and validate the snapshot before making any network connection.
    # -----------------------------------------------------------------------

    try:

        snapshot = load_snapshot(
            args.snapshot
        )

    except (
        FileNotFoundError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:

        print()
        print(
            f"Snapshot error: {exc}"
        )
        print()

        return

    # -----------------------------------------------------------------------
    # Perform restore with controlled error handling.
    # -----------------------------------------------------------------------

    try:

        await restore_snapshot(
            snapshot,
            normalize=args.normalize,
        )

    except RuntimeError as exc:

        print()
        print(
            f"RESTORE ABORTED: {exc}"
        )
        print()

    except (
        TimeoutError,
        asyncio.TimeoutError,
        ConnectionRefusedError,
        OSError,
        websockets.WebSocketException,
        KeyError,
        IndexError,
        TypeError,
        ValueError,
    ) as exc:

        print()
        print(
            "RESTORE FAILED: "
            f"{type(exc).__name__}: {exc}"
        )
        print()


if __name__ == "__main__":
    asyncio.run(main())
