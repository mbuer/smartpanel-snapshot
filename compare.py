#!/usr/bin/env python3

import asyncio
import json
import os
import sys
from datetime import datetime, UTC

import websockets

PANEL_ID = 0


async def fetch_current_state(panel_ip):

    uri = f"ws://{panel_ip}/live-view"

    async with websockets.connect(uri) as ws:

        await ws.send(json.dumps({
            "topic": "/LiveView/FetchPanelState",
            "body": {
                "panelId": PANEL_ID
            }
        }))

        while True:

            raw = await ws.recv()

            if isinstance(raw, bytes):
                continue

            try:
                msg = json.loads(raw)
            except Exception:
                continue

            if msg.get("topic") != "/LiveView/FetchPanelStateResponse":
                continue

            listen_keys = []
            call_keys = []

            for key in msg["body"]["leverKeysLedRing"]:

                key_id = key["keyId"]

                if key["upperColor"]["green"] == 255:
                    listen_keys.append(key_id)

                if key["lowerColor"]["red"] == 255:
                    call_keys.append(key_id)

            return {
                "listenKeys": sorted(listen_keys),
                "callKeys": sorted(call_keys)
            }


async def main():

    if len(sys.argv) != 2:
        print("\nUsage:")
        print("python compare.py <snapshot-file>\n")
        return

    snapshot_file = sys.argv[1]

    with open(snapshot_file) as f:
        snapshot = json.load(f)

    current = await fetch_current_state(
        snapshot["panelIp"]
    )

    expected_listen = set(snapshot["listenKeys"])
    expected_call = set(snapshot["callKeys"])

    current_listen = set(current["listenKeys"])
    current_call = set(current["callKeys"])

    missing_listen = sorted(
        expected_listen - current_listen
    )

    extra_listen = sorted(
        current_listen - expected_listen
    )

    missing_call = sorted(
        expected_call - current_call
    )

    extra_call = sorted(
        current_call - expected_call
    )

    #
    # Compliance Calculation
    #

    correct_total = (
        len(
            expected_listen.intersection(
                current_listen
            )
        )
        +
        len(
            expected_call.intersection(
                current_call
            )
        )
    )

    missing_total = (
        len(missing_listen)
        +
        len(missing_call)
    )

    extra_total = (
        len(extra_listen)
        +
        len(extra_call)
    )

    denominator = (
        correct_total
        +
        missing_total
        +
        extra_total
    )

    if denominator == 0:
        compliance = 100.0
    else:
        compliance = round(
            (
                correct_total
                / denominator
            ) * 100,
            1
        )

    healthy = (
        len(missing_listen) == 0
        and len(extra_listen) == 0
        and len(missing_call) == 0
        and len(extra_call) == 0
    )

    total_differences = (
        len(missing_listen)
        +
        len(extra_listen)
        +
        len(missing_call)
        +
        len(extra_call)
    )

    result = {

        "timestamp": datetime.now(
            UTC
        ).isoformat(),

        "panelName": snapshot["panelName"],
        "panelIp": snapshot["panelIp"],

        #
        # Raw Data
        #

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

        "missingListen": missing_listen,
        "extraListen": extra_listen,

        "missingCall": missing_call,
        "extraCall": extra_call,

        #
        # Human-Friendly Fields
        #

        "savedListenString": ",".join(
            map(
                str,
                sorted(expected_listen)
            )
        ) or "-",

        "currentListenString": ",".join(
            map(
                str,
                sorted(current_listen)
            )
        ) or "-",

        "savedCallString": ",".join(
            map(
                str,
                sorted(expected_call)
            )
        ) or "-",

        "currentCallString": ",".join(
            map(
                str,
                sorted(current_call)
            )
        ) or "-",

        #
        # Counts
        #

        "totalListen": len(
            current_listen
        ),

        "totalCall": len(
            current_call
        ),

        "missingListenCount": len(
            missing_listen
        ),

        "extraListenCount": len(
            extra_listen
        ),

        "missingCallCount": len(
            missing_call
        ),

        "extraCallCount": len(
            extra_call
        ),

        "totalDifferences": (
            total_differences
        ),

        #
        # Status
        #

        "compliance": compliance,
        "healthy": healthy
    }

    print(
        json.dumps(
            result,
            indent=2
        )
    )

    #
    # Metrics
    #

    os.makedirs(
        "metrics",
        exist_ok=True
    )

    metrics_name = (
        snapshot["panelName"]
        .replace(" ", "_")
        .replace("/", "_")
    )

    metrics_file = (
        f"metrics/{metrics_name}.json"
    )

    with open(
        metrics_file,
        "w"
    ) as f:
        json.dump(
            result,
            f,
            indent=2
        )

    print(
        f"\nMetrics written to "
        f"{metrics_file}"
    )

    #
    # Loki / Alloy Feed
    #

    with open(
        "/var/log/smartpanel/panel-health.jsonl",
        "a"
    ) as f:

        f.write(
            json.dumps(result)
        )

        f.write("\n")


if __name__ == "__main__":
    asyncio.run(main())
