#!/usr/bin/env python3

import argparse
import asyncio
import json
import time

import websockets

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


def calculate_drift(
    desired_listen,
    desired_call,
    current_listen,
    current_call,
):
    desired_listen = set(desired_listen)
    desired_call = set(desired_call)

    current_listen = set(current_listen)
    current_call = set(current_call)

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

    if (
        not desired_listen and
        not desired_call
    ):
        compliance = 100.0

        if (
            current_listen or
            current_call
        ):
            compliance = 0.0

    else:
        expected_total = (
            len(desired_listen) +
            len(desired_call)
        )

        correct_total = (
            len(desired_listen.intersection(current_listen)) +
            len(desired_call.intersection(current_call))
        )

        compliance = round(
            (correct_total / expected_total) * 100,
            1
        )

        if extra_listen or extra_call:
            compliance = 0.0

    return {
        "missing_listen": missing_listen,
        "extra_listen": extra_listen,
        "missing_call": missing_call,
        "extra_call": extra_call,
        "compliance": compliance,
    }


async def touch(ws, panel_id, x, y, hold_time=0.25):

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

    await asyncio.sleep(hold_time)

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


async def normalize_all(ws, panel_id):

    print("\nNormalizing NSA keys...\n")

    for key in NORMALIZE_KEYS:

        print(f"[{key['name']}] Opening menu")

        await touch(
            ws,
            panel_id,
            key["select"][0],
            key["select"][1],
            hold_time=0.8
        )

        await asyncio.sleep(0.2)

        print(f"[{key['name']}] Normalize")

        await touch(
            ws,
            panel_id,
            key["action"][0],
            key["action"][1],
            hold_time=0.15
        )

        await asyncio.sleep(0.3)

    print("\nNormalization complete")


async def get_current_state(ws, panel_id):

    await ws.send(json.dumps({
        "topic": "/LiveView/FetchPanelState",
        "body": {
            "panelId": panel_id
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


async def toggle_listen_key(ws, panel_id, key_id):

    await ws.send(json.dumps({
        "topic": "/LiveView/SimulateLever",
        "body": {
            "panelId": panel_id,
            "keyId": key_id,
            "leverState": "Up"
        }
    }))

    await asyncio.sleep(0.1)

    await ws.send(json.dumps({
        "topic": "/LiveView/SimulateLever",
        "body": {
            "panelId": panel_id,
            "keyId": key_id,
            "leverState": "Released"
        }
    }))

    await asyncio.sleep(0.2)


async def toggle_call_key(ws, panel_id, key_id):

    await ws.send(json.dumps({
        "topic": "/LiveView/SimulateLever",
        "body": {
            "panelId": panel_id,
            "keyId": key_id,
            "leverState": "Down"
        }
    }))

    await asyncio.sleep(0.1)

    await ws.send(json.dumps({
        "topic": "/LiveView/SimulateLever",
        "body": {
            "panelId": panel_id,
            "keyId": key_id,
            "leverState": "Released"
        }
    }))

    await asyncio.sleep(0.2)


async def main():

    parser = argparse.ArgumentParser()

    parser.add_argument("snapshot")

    parser.add_argument(
        "--normalize",
        action="store_true"
    )

    args = parser.parse_args()

    with open(args.snapshot) as f:
        snapshot = json.load(f)

    panel_name = snapshot["panelName"]
    panel_ip = snapshot["panelIp"]
    panel_id = snapshot["panelId"]

    desired_listen = snapshot["listenKeys"]
    desired_call = snapshot["callKeys"]

    print("\n====================================")
    print(f"Panel : {panel_name}")
    print("====================================")

    uri = f"ws://{panel_ip}/live-view"

    async with websockets.connect(uri) as ws:

        if args.normalize:

            await normalize_all(ws, panel_id)

            print("\nWaiting for panel to settle...")
            await asyncio.sleep(3)

        start = time.time()

        current = await get_current_state(
            ws,
            panel_id
        )

        print(
            f"\nCurrent state fetched in "
            f"{time.time() - start:.2f}s"
        )

        drift = calculate_drift(
            desired_listen,
            desired_call,
            current["listenKeys"],
            current["callKeys"]
        )

        print("\n=== Drift Analysis ===")

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

        removed_listen = 0
        removed_call = 0

        added_listen = 0
        added_call = 0

        #
        # Remove extras
        #

        for key in drift["extra_listen"]:
            print(f"Removing LISTEN {key}")
            await toggle_listen_key(
                ws,
                panel_id,
                key
            )
            removed_listen += 1

        for key in drift["extra_call"]:
            print(f"Removing CALL {key}")
            await toggle_call_key(
                ws,
                panel_id,
                key
            )
            removed_call += 1

        #
        # Restore missing
        #

        for key in drift["missing_listen"]:
            print(f"Restoring LISTEN {key}")
            await toggle_listen_key(
                ws,
                panel_id,
                key
            )
            added_listen += 1

        for key in drift["missing_call"]:
            print(f"Restoring CALL {key}")
            await toggle_call_key(
                ws,
                panel_id,
                key
            )
            added_call += 1

        print("\nVerifying...")

        final = await get_current_state(
            ws,
            panel_id
        )

    final_drift = calculate_drift(
        desired_listen,
        desired_call,
        final["listenKeys"],
        final["callKeys"]
    )

    print("\n====================================")

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

    if (
        not final_drift["missing_listen"]
        and not final_drift["extra_listen"]
        and not final_drift["missing_call"]
        and not final_drift["extra_call"]
    ):
        print("\nSUCCESS ✅")
    else:
        print("\nWARNING ⚠️")

    print("====================================")


if __name__ == "__main__":
    asyncio.run(main())
