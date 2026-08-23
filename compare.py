#!/usr/bin/env python3

"""
SmartPanel Snapshot - Compare Utility

Compares the current state of a Riedel SmartPanel against a previously
saved SmartPanel Snapshot.

The comparison is read-only. It does not modify SmartPanel state.

The process:

    1. Loads and validates a snapshot JSON file.
    2. Connects to the SmartPanel stored in the snapshot.
    3. Verifies the connected SmartPanel identity.
    4. Retrieves the current Listen and Call/Talk state.
    5. Calculates configuration drift and compliance.
    6. Writes structured JSON metrics.
    7. Appends the result to the Alloy/Loki JSONL feed.

Example:

    python compare.py snapshots/R3-1232.json

Compliance model:

    Compliance percentage represents how many expected key states are
    currently correct.

    Status is strict:

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

Generated files:

    metrics/<SmartPanel-name>.json

The result is also appended as one JSON object per line to:

    /var/log/smartpanel/panel-health.jsonl

That JSONL file is intended to be consumed by Grafana Alloy and forwarded
to Loki/Grafana.
"""

import asyncio
import json
import os
import re
import sys
from datetime import datetime, UTC
from pathlib import Path

import websockets


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Maximum time allowed to establish the SmartPanel WebSocket connection.
CONNECT_TIMEOUT = 3

# Maximum time allowed while waiting for a SmartPanel response.
RESPONSE_TIMEOUT = 3

# Directory used for generated comparison metrics.
METRICS_DIR = "metrics"

# Runtime JSONL file consumed by Grafana Alloy.
#
# NOTE:
# The user running compare.py must have permission to write to this path.
ALLOY_LOG_FILE = "/var/log/smartpanel/panel-health.jsonl"


# ---------------------------------------------------------------------------
# Snapshot handling
# ---------------------------------------------------------------------------

def load_snapshot(snapshot_path):
    """
    Load and validate a SmartPanel snapshot file.

    Required fields:

        panelName
        panelIp
        panelId
        listenKeys
        callKeys

    Args:
        snapshot_path:
            Path to the snapshot JSON file.

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
    Wait for a specific SmartPanel Live View response topic.

    SmartPanel Live View can send unrelated messages while a request is
    being processed. Those messages are ignored.

    A timeout prevents compare.py from waiting indefinitely.

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

        # Binary messages are currently not relevant to state comparison.
        if isinstance(raw, bytes):
            continue

        try:
            message = json.loads(raw)

        except json.JSONDecodeError:
            continue

        if message.get(
            "topic"
        ) == expected_topic:
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
            "SmartPanel returned an empty "
            "panel information response."
        )

    return panels[0]["customName"]


async def fetch_current_state(
    ws,
    panel_id,
):
    """
    Retrieve current Listen and Call/Talk state from a SmartPanel.

    Current Live View interpretation:

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
        Dictionary containing sorted listenKeys and callKeys.
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

    for key in (
        message["body"]["leverKeysLedRing"]
    ):

        key_id = key["keyId"]

        # Listen state
        if (
            key["upperColor"]["green"]
            == 255
        ):
            listen_keys.append(
                key_id
            )

        # Call / Talk state
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
# Drift and compliance
# ---------------------------------------------------------------------------

def calculate_drift(
    desired_listen,
    desired_call,
    current_listen,
    current_call,
):
    """
    Compare saved SmartPanel state with current SmartPanel state.

    Compliance percentage measures how many expected states are correct.

    Strict compliance requires:

        - no missing Listen keys
        - no extra Listen keys
        - no missing Call keys
        - no extra Call keys

    Returns:
        Dictionary containing drift lists, compliance percentage,
        and a strict compliant Boolean.
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

    # -----------------------------------------------------------------------
    # Determine configuration drift
    # -----------------------------------------------------------------------

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

    # -----------------------------------------------------------------------
    # Compliance score
    # -----------------------------------------------------------------------

    expected_total = (
        len(desired_listen)
        + len(desired_call)
    )

    if expected_total == 0:

        # If the expected state is completely empty, the panel is
        # 100% correct only when the current state is also empty.
        if (
            current_listen
            or current_call
        ):
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
            (
                correct_total
                / expected_total
            ) * 100,
            1,
        )

    # -----------------------------------------------------------------------
    # Strict compliance state
    # -----------------------------------------------------------------------

    compliant = not any([
        missing_listen,
        extra_listen,
        missing_call,
        extra_call,
    ])

    total_differences = (
        len(missing_listen)
        + len(extra_listen)
        + len(missing_call)
        + len(extra_call)
    )

    return {
        "missing_listen": missing_listen,
        "extra_listen": extra_listen,
        "missing_call": missing_call,
        "extra_call": extra_call,
        "total_differences": total_differences,
        "compliance": compliance,
        "compliant": compliant,
    }


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def keys_to_string(keys):
    """
    Convert a list/set of key IDs into a human-readable string.

    Example:

        [6, 16, 18]

    becomes:

        "6,16,18"

    An empty collection becomes "-".
    """

    return (
        ",".join(
            map(
                str,
                sorted(keys),
            )
        )
        or "-"
    )


def safe_filename(name):
    """
    Convert a SmartPanel custom name into a filesystem-safe filename.

    NOTE:
    As with snapshots, SmartPanel custom names are currently expected
    to be unique. Duplicate names will produce metrics file collisions.
    """

    name = name.strip()

    name = re.sub(
        r"\s+",
        "_",
        name,
    )

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
# Metrics generation
# ---------------------------------------------------------------------------

def build_result(
    snapshot,
    current,
    drift,
):
    """
    Build the structured comparison result.

    This structure is written both to:

        metrics/<panel>.json

    and:

        /var/log/smartpanel/panel-health.jsonl

    Field naming intentionally remains compatible with the existing
    Grafana/Alloy integration where possible.
    """

    expected_listen = set(
        snapshot["listenKeys"]
    )

    expected_call = set(
        snapshot["callKeys"]
    )

    current_listen = set(
        current["listenKeys"]
    )

    current_call = set(
        current["callKeys"]
    )

    # Keep the existing "healthy" field for compatibility with
    # current dashboards/integrations.
    #
    # "compliant" is also included because it better describes what
    # the value represents.
    healthy = drift["compliant"]

    status = (
        "COMPLIANT"
        if drift["compliant"]
        else "NON-COMPLIANT"
    )

    return {

        # -------------------------------------------------------------------
        # Identity / timestamp
        # -------------------------------------------------------------------

        "timestamp": datetime.now(
            UTC
        ).isoformat(),

        "panelName": snapshot[
            "panelName"
        ],

        "panelIp": snapshot[
            "panelIp"
        ],

        "panelId": snapshot[
            "panelId"
        ],

        # -------------------------------------------------------------------
        # Raw state data
        # -------------------------------------------------------------------

        "expectedListen": sorted(
            expected_listen
        ),

        "currentListen": sorted(
            current_listen
        ),

        "expectedCall": sorted(
            expected_call
        ),

        "currentCall": sorted(
            current_call
        ),

        # -------------------------------------------------------------------
        # Configuration drift
        # -------------------------------------------------------------------

        "missingListen": drift[
            "missing_listen"
        ],

        "extraListen": drift[
            "extra_listen"
        ],

        "missingCall": drift[
            "missing_call"
        ],

        "extraCall": drift[
            "extra_call"
        ],

        # -------------------------------------------------------------------
        # Human-readable representations
        # -------------------------------------------------------------------

        "savedListenString":
            keys_to_string(
                expected_listen
            ),

        "currentListenString":
            keys_to_string(
                current_listen
            ),

        "savedCallString":
            keys_to_string(
                expected_call
            ),

        "currentCallString":
            keys_to_string(
                current_call
            ),

        # -------------------------------------------------------------------
        # Counts
        # -------------------------------------------------------------------

        "totalListen": len(
            current_listen
        ),

        "totalCall": len(
            current_call
        ),

        "missingListenCount": len(
            drift["missing_listen"]
        ),

        "extraListenCount": len(
            drift["extra_listen"]
        ),

        "missingCallCount": len(
            drift["missing_call"]
        ),

        "extraCallCount": len(
            drift["extra_call"]
        ),

        "totalDifferences": drift[
            "total_differences"
        ],

        # -------------------------------------------------------------------
        # Compliance / status
        # -------------------------------------------------------------------

        "compliance": drift[
            "compliance"
        ],

        # Existing field retained for Grafana compatibility.
        "healthy": healthy,

        # New, clearer fields for future consumers.
        "compliant": drift[
            "compliant"
        ],

        "status": status,
    }


def write_metrics(result):
    """
    Write the latest comparison result to the local metrics directory.

    One file is maintained per SmartPanel.
    """

    os.makedirs(
        METRICS_DIR,
        exist_ok=True,
    )

    panel_name = safe_filename(
        result["panelName"]
    )

    metrics_file = os.path.join(
        METRICS_DIR,
        f"{panel_name}.json",
    )

    with open(
        metrics_file,
        "w",
        encoding="utf-8",
    ) as file:

        json.dump(
            result,
            file,
            indent=2,
        )

    return metrics_file


def write_alloy_log(result):
    """
    Append one comparison result to the Alloy/Loki JSONL feed.

    Each line contains one complete JSON object.

    The parent log directory must already exist and the current user
    must have write permissions.
    """

    log_path = Path(
        ALLOY_LOG_FILE
    )

    # Ensure the runtime directory exists where permissions allow it.
    log_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with log_path.open(
        "a",
        encoding="utf-8",
    ) as file:

        file.write(
            json.dumps(result)
        )

        file.write("\n")


# ---------------------------------------------------------------------------
# Main comparison operation
# ---------------------------------------------------------------------------

async def compare_snapshot(snapshot):
    """
    Compare one SmartPanel against its saved snapshot.

    The connected SmartPanel name is verified before comparison so an
    IP reassignment cannot silently compare the wrong device.
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

    uri = (
        f"ws://{panel_ip}/live-view"
    )

    print()
    print(
        f"Connecting to {uri}"
    )

    async with websockets.connect(
        uri,
        open_timeout=CONNECT_TIMEOUT,
    ) as ws:

        # -------------------------------------------------------------------
        # Verify panel identity
        # -------------------------------------------------------------------

        connected_name = (
            await get_panel_name(ws)
        )

        if connected_name != panel_name:

            raise RuntimeError(
                "SmartPanel identity mismatch. "
                f"Snapshot expects '{panel_name}', "
                f"but device reports "
                f"'{connected_name}'."
            )

        # -------------------------------------------------------------------
        # Retrieve current panel state
        # -------------------------------------------------------------------

        current = (
            await fetch_current_state(
                ws,
                panel_id,
            )
        )

    drift = calculate_drift(
        snapshot["listenKeys"],
        snapshot["callKeys"],
        current["listenKeys"],
        current["callKeys"],
    )

    return (
        current,
        drift,
    )


# ---------------------------------------------------------------------------
# Command-line entry point
# ---------------------------------------------------------------------------

async def main():
    """
    Load a snapshot, compare it to the live SmartPanel, and generate
    metrics/log output.
    """

    if len(sys.argv) != 2:

        print()
        print(
            "SmartPanel Snapshot - Compare"
        )
        print()
        print("Usage:")
        print(
            "  python compare.py "
            "<snapshot-file>"
        )
        print()
        print("Example:")
        print(
            "  python compare.py "
            "snapshots/R3-1232.json"
        )
        print()

        return

    snapshot_file = sys.argv[1]

    # -----------------------------------------------------------------------
    # Load snapshot before making a network connection.
    # -----------------------------------------------------------------------

    try:

        snapshot = load_snapshot(
            snapshot_file
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
    # Perform live comparison.
    # -----------------------------------------------------------------------

    try:

        current, drift = (
            await compare_snapshot(
                snapshot
            )
        )

    except RuntimeError as exc:

        print()
        print(
            f"COMPARE ABORTED: {exc}"
        )
        print()

        return

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
            "COMPARE FAILED: "
            f"{type(exc).__name__}: "
            f"{exc}"
        )
        print()

        return

    # -----------------------------------------------------------------------
    # Build result
    # -----------------------------------------------------------------------

    result = build_result(
        snapshot,
        current,
        drift,
    )

    # Print complete structured result to stdout.
    print()
    print(
        json.dumps(
            result,
            indent=2,
        )
    )

    # -----------------------------------------------------------------------
    # Write local metrics file.
    # -----------------------------------------------------------------------

    try:

        metrics_file = (
            write_metrics(result)
        )

        print()
        print(
            f"Metrics written to "
            f"{metrics_file}"
        )

    except OSError as exc:

        print()
        print(
            "WARNING: Could not write "
            f"metrics file: {exc}"
        )

    # -----------------------------------------------------------------------
    # Append Alloy / Loki feed.
    # -----------------------------------------------------------------------

    try:

        write_alloy_log(
            result
        )

        print(
            f"Alloy log updated: "
            f"{ALLOY_LOG_FILE}"
        )

    except OSError as exc:

        # A logging permission problem should not invalidate the actual
        # SmartPanel comparison.
        print()
        print(
            "WARNING: Comparison succeeded, "
            "but the Alloy log could not "
            f"be written: {exc}"
        )


if __name__ == "__main__":
    asyncio.run(main())
