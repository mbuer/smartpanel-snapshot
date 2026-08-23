#!/usr/bin/env python3

"""
SmartPanel Snapshot - Dynamic Restore Utility

Restores Listen and Call/Talk state from a saved SmartPanel snapshot.

Optional key normalization:

    python restore_dynamic.py snapshots/R3-1232.json --normalize

Dynamic normalization currently supports:

    RSP-1232:
        2 displays
        16 key positions per display
        32 total positions

    1216-family panels:
        Assumed to use the same 16-key geometry on displayId 0.
        This has not yet been validated on an online 1216 panel.

IMPORTANT:
    Restore and normalization actively manipulate SmartPanel state through
    the Live View WebSocket API.
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

import websockets

from config import CONFIG


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

CONNECT_TIMEOUT = CONFIG["smartpanels"]["connect_timeout"]
RESPONSE_TIMEOUT = CONFIG["smartpanels"]["response_timeout"]

NORMALIZE_SETTLE_TIME = 3

LEVER_HOLD_TIME = 0.1
LEVER_SETTLE_TIME = 0.2


# ---------------------------------------------------------------------------
# Dynamic key-normalization geometry
# ---------------------------------------------------------------------------
#
# Each SmartPanel display contains:
#
#     8 columns x 2 rows = 16 physical key positions
#
# Coordinates were measured from an RSP-1232 Live View session.
#
# The key drawer behaves as follows:
#
#     columns 0-3 -> drawer opens to the right
#     columns 4-7 -> drawer opens to the left
#

DISPLAY_GEOMETRY = {
    0: {
        "top": {
            "left": (0.0652228897, 0.2665194764),
            "right": (0.9393297502, 0.2463333859),
        },
        "bottom": {
            "left": (0.0591527031, 0.7610786942),
            "right": (0.9352829592, 0.7610786942),
        },
    },

    1: {
        "top": {
            "left": (0.0646221941, 0.2665194764),
            "right": (0.9346822637, 0.2463333859),
        },
        "bottom": {
            "left": (0.0605754031, 0.7711717395),
            "right": (0.9346822637, 0.7913578300),
        },
    },
}


# Exact measured Normalize-action positions for corner keys.
#
# For all other positions the neighboring-key center is used as an
# approximation.
#
# Key:
#
#     (display_id, row, column): (x, y)
#
NORMALIZE_ACTION_OVERRIDES = {
    # Display 0
    (0, 0, 0): (0.1866266203, 0.2564264312),
    (0, 0, 7): (0.8078090420, 0.2362403406),
    (0, 1, 0): (0.1866266203, 0.7207065132),
    (0, 1, 1): (0.3141005375, 0.7509856490),
    (0, 1, 7): (0.8078090420, 0.7711717395),

    # Display 1
    (1, 0, 0): (0.1880493203, 0.2564264312),
    (1, 0, 7): (0.8051849510, 0.2766125217),
    (1, 1, 0): (0.1880493203, 0.7812647847),
    (1, 1, 7): (0.8173253241, 0.7913578300),
}


# ---------------------------------------------------------------------------
# Snapshot handling
# ---------------------------------------------------------------------------

def load_snapshot(snapshot_path):
    """
    Load and validate one SmartPanel snapshot.
    """

    path = Path(snapshot_path)

    if not path.is_file():
        raise FileNotFoundError(
            f"Snapshot file not found: {snapshot_path}"
        )

    with path.open(
        "r",
        encoding="utf-8",
    ) as file:
        snapshot = json.load(file)

    required_fields = {
        "panelName",
        "panelIp",
        "panelId",
        "listenKeys",
        "callKeys",
    }

    missing_fields = (
        required_fields
        - snapshot.keys()
    )

    if missing_fields:
        raise ValueError(
            "Snapshot is missing required field(s): "
            + ", ".join(
                sorted(missing_fields)
            )
        )

    if not isinstance(
        snapshot["listenKeys"],
        list,
    ):
        raise ValueError(
            "listenKeys must be a list."
        )

    if not isinstance(
        snapshot["callKeys"],
        list,
    ):
        raise ValueError(
            "callKeys must be a list."
        )

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

async def receive_topic(
    ws,
    expected_topic,
):
    """
    Wait for a specific Live View JSON response.

    Binary display frames and unrelated messages are ignored.
    """

    while True:

        raw = await asyncio.wait_for(
            ws.recv(),
            timeout=RESPONSE_TIMEOUT,
        )

        if isinstance(
            raw,
            bytes,
        ):
            continue

        try:
            message = json.loads(
                raw
            )

        except json.JSONDecodeError:
            continue

        if (
            message.get("topic")
            == expected_topic
        ):
            return message


async def get_panel_info(ws):
    """
    Return SmartPanel identity information.
    """

    await ws.send(
        json.dumps({
            "topic": "/LiveView/FetchPanelInfo",
            "body": {},
        })
    )

    message = await receive_topic(
        ws,
        "/LiveView/FetchPanelInfoResponse",
    )

    panels = (
        message
        ["body"]
        ["panels"]
    )

    if not panels:
        raise ValueError(
            "SmartPanel returned an empty panel information response."
        )

    panel = panels[0]

    return {
        "panelName": panel.get(
            "customName"
        ),
        "panelType": panel.get(
            "panelType",
            "",
        ),
        "firmwareVersion": panel.get(
            "firmwareVersion",
            "",
        ),
        "panelId": panel.get(
            "panelId"
        ),
    }


async def get_current_state(
    ws,
    panel_id,
):
    """
    Read current Listen and Call/Talk state.

    Current interpretation:

        upperColor.green == 255
            Listen active

        lowerColor.red == 255
            Call/Talk active
    """

    await ws.send(
        json.dumps({
            "topic": "/LiveView/FetchPanelState",
            "body": {
                "panelId": panel_id,
            },
        })
    )

    message = await receive_topic(
        ws,
        "/LiveView/FetchPanelStateResponse",
    )

    listen_keys = []
    call_keys = []

    keys = (
        message
        ["body"]
        ["leverKeysLedRing"]
    )

    for key in keys:

        key_id = key["keyId"]

        if (
            key["upperColor"]["green"]
            == 255
        ):
            listen_keys.append(
                key_id
            )

        if (
            key["lowerColor"]["red"]
            == 255
        ):
            call_keys.append(
                key_id
            )

    return {
        "listenKeys": sorted(
            listen_keys
        ),
        "callKeys": sorted(
            call_keys
        ),
    }


# ---------------------------------------------------------------------------
# Drift / compliance
# ---------------------------------------------------------------------------

def calculate_drift(
    desired_listen,
    desired_call,
    current_listen,
    current_call,
):
    """
    Compare desired snapshot state with current SmartPanel state.
    """

    desired_listen = set(
        desired_listen
    )

    desired_call = set(
        desired_call
    )

    current_listen = set(
        current_listen
    )

    current_call = set(
        current_call
    )

    missing_listen = sorted(
        desired_listen
        - current_listen
    )

    extra_listen = sorted(
        current_listen
        - desired_listen
    )

    missing_call = sorted(
        desired_call
        - current_call
    )

    extra_call = sorted(
        current_call
        - desired_call
    )

    expected_total = (
        len(desired_listen)
        + len(desired_call)
    )

    if expected_total == 0:

        compliance = (
            0.0
            if current_listen
            or current_call
            else 100.0
        )

    else:

        correct_total = (
            len(
                desired_listen.intersection(
                    current_listen
                )
            )
            +
            len(
                desired_call.intersection(
                    current_call
                )
            )
        )

        compliance = round(
            (
                correct_total
                / expected_total
            ) * 100,
            1,
        )

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
# Listen / Call control
# ---------------------------------------------------------------------------

async def toggle_listen_key(
    ws,
    panel_id,
    key_id,
):
    """
    Toggle Listen by simulating an upward lever movement.
    """

    await ws.send(
        json.dumps({
            "topic": "/LiveView/SimulateLever",
            "body": {
                "panelId": panel_id,
                "keyId": key_id,
                "leverState": "Up",
            },
        })
    )

    await asyncio.sleep(
        LEVER_HOLD_TIME
    )

    await ws.send(
        json.dumps({
            "topic": "/LiveView/SimulateLever",
            "body": {
                "panelId": panel_id,
                "keyId": key_id,
                "leverState": "Released",
            },
        })
    )

    await asyncio.sleep(
        LEVER_SETTLE_TIME
    )


async def toggle_call_key(
    ws,
    panel_id,
    key_id,
):
    """
    Toggle Call/Talk by simulating a downward lever movement.
    """

    await ws.send(
        json.dumps({
            "topic": "/LiveView/SimulateLever",
            "body": {
                "panelId": panel_id,
                "keyId": key_id,
                "leverState": "Down",
            },
        })
    )

    await asyncio.sleep(
        LEVER_HOLD_TIME
    )

    await ws.send(
        json.dumps({
            "topic": "/LiveView/SimulateLever",
            "body": {
                "panelId": panel_id,
                "keyId": key_id,
                "leverState": "Released",
            },
        })
    )

    await asyncio.sleep(
        LEVER_SETTLE_TIME
    )


# ---------------------------------------------------------------------------
# Dynamic key normalization
# ---------------------------------------------------------------------------

def interpolate_position(
    left,
    right,
    column,
):
    """
    Calculate one key position in an eight-column row.
    """

    if not 0 <= column <= 7:
        raise ValueError(
            f"Invalid column: {column}"
        )

    fraction = (
        column / 7
    )

    x = (
        left[0]
        + (
            right[0]
            - left[0]
        )
        * fraction
    )

    y = (
        left[1]
        + (
            right[1]
            - left[1]
        )
        * fraction
    )

    return x, y


def get_key_position(
    display_id,
    row,
    column,
):
    """
    Calculate the touchscreen center of one physical key.
    """

    if (
        display_id
        not in DISPLAY_GEOMETRY
    ):
        raise ValueError(
            f"Unsupported display ID: {display_id}"
        )

    if row not in (
        0,
        1,
    ):
        raise ValueError(
            f"Invalid row: {row}"
        )

    row_name = (
        "top"
        if row == 0
        else "bottom"
    )

    geometry = (
        DISPLAY_GEOMETRY
        [display_id]
        [row_name]
    )

    return interpolate_position(
        geometry["left"],
        geometry["right"],
        column,
    )


def get_normalize_position(
    display_id,
    row,
    column,
):
    """
    Determine the Normalize-action position.

    Exact captured coordinates are used for the four corners of each
    display. Other keys use the neighboring key center.

    Columns 0-3:
        drawer opens right

    Columns 4-7:
        drawer opens left
    """

    override = (
        NORMALIZE_ACTION_OVERRIDES
        .get(
            (
                display_id,
                row,
                column,
            )
        )
    )

    if override is not None:
        return override

    if column < 4:
        action_column = (
            column + 1
        )

    else:
        action_column = (
            column - 1
        )

    return get_key_position(
        display_id,
        row,
        action_column,
    )


def get_normalization_displays(
    panel_type,
):
    """
    Determine how many displays should be normalized.

    1232:
        display 0 + display 1

    1216:
        display 0 only

    Unknown panel types are rejected rather than guessed.
    """

    panel_type_upper = (
        panel_type.upper()
    )

    if "1232" in panel_type_upper:
        return (
            0,
            1,
        )

    if "1216" in panel_type_upper:
        print(
            "WARNING: 1216 key-normalization geometry "
            "has not yet been validated."
        )

        return (
            0,
        )

    raise RuntimeError(
        "Dynamic key normalization is not defined "
        f"for panel type '{panel_type}'."
    )


async def touch(
    ws,
    panel_id,
    display_id,
    x,
    y,
    hold_time=0.25,
):
    """
    Simulate one touchscreen press and release.
    """

    await ws.send(
        json.dumps({
            "topic": "/LiveView/SimulateTouch",
            "body": {
                "panelId": panel_id,
                "displayId": display_id,
                "x": x,
                "y": y,
                "touchState": "Pressed",
            },
        })
    )

    await asyncio.sleep(
        hold_time
    )

    await ws.send(
        json.dumps({
            "topic": "/LiveView/SimulateTouch",
            "body": {
                "panelId": panel_id,
                "displayId": display_id,
                "x": x,
                "y": y,
                "touchState": "Released",
            },
        })
    )


async def normalize_key(
    ws,
    panel_id,
    display_id,
    row,
    column,
):
    """
    Normalize one physical key position.
    """

    select_x, select_y = (
        get_key_position(
            display_id,
            row,
            column,
        )
    )

    action_x, action_y = (
        get_normalize_position(
            display_id,
            row,
            column,
        )
    )

    print(
        f"[Display {display_id} "
        f"Row {row} "
        f"Column {column}]"
    )

    # Open the key drawer.
    await touch(
        ws,
        panel_id,
        display_id,
        select_x,
        select_y,
        hold_time=0.65,
    )

    await asyncio.sleep(
        0.15
    )

    # Hit Normalize.
    await touch(
        ws,
        panel_id,
        display_id,
        action_x,
        action_y,
        hold_time=0.12,
    )

    await asyncio.sleep(
        0.25
    )



async def drain_liveview(ws):
    """
    Continuously consume unsolicited Live View traffic.

    Touch operations generate binary display frames. If these frames are
    not consumed, the WebSocket receive queue may eventually fill and
    stall further communication.
    """

    try:
        while True:
            await ws.recv()

    except asyncio.CancelledError:
        pass


async def normalize_all(
    ws,
    panel_id,
    panel_type,
):
    """
    EXPERIMENTAL key normalization.

    Limitations:
        - Simulates Live View touchscreen operations.
        - Does not use a dedicated normalization API.
        - Panel-type behavior is not fully validated.
    """

    displays = get_normalization_displays(
        panel_type
    )

    print()
    print("EXPERIMENTAL KEY NORMALIZATION")
    print("--------------------------------")
    print("Simulating Live View touchscreen operations.")
    print("Panel-type behavior is not fully validated.")
    print()

    drain_task = asyncio.create_task(
        drain_liveview(ws)
    )

    try:
        for display_id in displays:
            for row in (0, 1):
                for column in range(8):

                    await normalize_key(
                        ws,
                        panel_id,
                        display_id,
                        row,
                        column,
                    )

    finally:
        drain_task.cancel()

        try:
            await drain_task

        except asyncio.CancelledError:
            pass

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
    Print a human-readable drift report.
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
# Restore
# ---------------------------------------------------------------------------

async def restore_snapshot(
    snapshot,
    normalize=False,
):
    """
    Restore one SmartPanel from a snapshot.
    """

    panel_name = snapshot[
        "panelName"
    ]

    panel_ip = snapshot[
        "panelIp"
    ]

    panel_id = snapshot[
        "panelId"
    ]

    desired_listen = snapshot[
        "listenKeys"
    ]

    desired_call = snapshot[
        "callKeys"
    ]

    uri = (
        f"ws://{panel_ip}/live-view"
    )

    print()
    print(
        "===================================="
    )
    print(
        f"Panel    : {panel_name}"
    )
    print(
        f"IP       : {panel_ip}"
    )
    print(
        f"Panel ID : {panel_id}"
    )
    print(
        "===================================="
    )

    print()
    print(
        f"Connecting to {uri}"
    )

    async with websockets.connect(
        uri,
        open_timeout=CONNECT_TIMEOUT,
    ) as ws:

        # ---------------------------------------------------------------
        # Verify panel identity
        # ---------------------------------------------------------------

        panel_info = (
            await get_panel_info(ws)
        )

        connected_name = (
            panel_info["panelName"]
        )

        panel_type = (
            panel_info["panelType"]
        )

        print(
            f"Connected SmartPanel: "
            f"{connected_name}"
        )

        print(
            f"Panel type          : "
            f"{panel_type}"
        )

        if (
            connected_name
            != panel_name
        ):
            raise RuntimeError(
                "SmartPanel identity mismatch. "
                f"Snapshot expects '{panel_name}', "
                f"but device reports '{connected_name}'. "
                "Restore aborted."
            )

        print(
            "Panel identity verified."
        )

        # ---------------------------------------------------------------
        # Optional key normalization
        # ---------------------------------------------------------------

        if normalize:

            await normalize_all(
                ws,
                panel_id,
                panel_type,
            )

            print()
            print(
                "Waiting for panel to settle..."
            )

            await asyncio.sleep(
                NORMALIZE_SETTLE_TIME
            )

        # ---------------------------------------------------------------
        # Read current state
        # ---------------------------------------------------------------

        start = (
            time.monotonic()
        )

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

        drift = calculate_drift(
            desired_listen,
            desired_call,
            current["listenKeys"],
            current["callKeys"],
        )

        print_drift_report(
            drift
        )

        removed_listen = 0
        removed_call = 0
        added_listen = 0
        added_call = 0

        # ---------------------------------------------------------------
        # Remove unexpected state
        # ---------------------------------------------------------------

        for key in drift[
            "extra_listen"
        ]:

            print(
                f"Removing LISTEN {key}"
            )

            await toggle_listen_key(
                ws,
                panel_id,
                key,
            )

            removed_listen += 1

        for key in drift[
            "extra_call"
        ]:

            print(
                f"Removing CALL {key}"
            )

            await toggle_call_key(
                ws,
                panel_id,
                key,
            )

            removed_call += 1

        # ---------------------------------------------------------------
        # Restore missing state
        # ---------------------------------------------------------------

        for key in drift[
            "missing_listen"
        ]:

            print(
                f"Restoring LISTEN {key}"
            )

            await toggle_listen_key(
                ws,
                panel_id,
                key,
            )

            added_listen += 1

        for key in drift[
            "missing_call"
        ]:

            print(
                f"Restoring CALL {key}"
            )

            await toggle_call_key(
                ws,
                panel_id,
                key,
            )

            added_call += 1

        # ---------------------------------------------------------------
        # Verify
        # ---------------------------------------------------------------

        print()
        print(
            "Verifying..."
        )

        final = await get_current_state(
            ws,
            panel_id,
        )

    final_drift = calculate_drift(
        desired_listen,
        desired_call,
        final["listenKeys"],
        final["callKeys"],
    )

    print()
    print(
        "===================================="
    )

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

    print(
        "------------------------------------"
    )

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
        f"Status         : {final_status}"
    )

    print(
        "------------------------------------"
    )

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

    print()

    if final_drift[
        "compliant"
    ]:
        print(
            "SUCCESS ✅"
        )

    else:
        print(
            "WARNING ⚠️"
        )

    print(
        "===================================="
    )

    return final_drift


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

async def main():

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
            "Normalize SmartPanel keys before "
            "restoring the snapshot."
        ),
    )

    args = (
        parser.parse_args()
    )

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
