#!/usr/bin/env python3

import asyncio
import json
import sys
import websockets


PANEL_ID = 0


async def main():

    if len(sys.argv) != 2:
        print("")
        print("Usage:")
        print("python save.py <panel-ip>")
        print("")
        print("Example:")
        print("python save.py 10.85.226.96")
        print("")
        return

    panel_ip = sys.argv[1]

    uri = f"ws://{panel_ip}/live-view"

    print(f"Connecting to {uri}")

    async with websockets.connect(uri, open_timeout=3) as ws:

        #
        # Get panel information
        #
        await ws.send(json.dumps({
            "topic": "/LiveView/FetchPanelInfo",
            "body": {}
        }))

        panel_name = panel_ip

        while True:

            msg = json.loads(await ws.recv())

            if msg.get("topic") == "/LiveView/FetchPanelInfoResponse":

                panel_name = msg["body"]["panels"][0]["customName"]
                filename = f"snapshots/{panel_name}.json"
	
                break

        #
        # Get current panel state
        #
        await ws.send(json.dumps({
            "topic": "/LiveView/FetchPanelState",
            "body": {
                "panelId": PANEL_ID
            }
        }))

        while True:

            msg = json.loads(await ws.recv())

            if msg.get("topic") != "/LiveView/FetchPanelStateResponse":
                continue

            listen_keys = []
            call_keys = []

            for key in msg["body"]["leverKeysLedRing"]:

                key_id = key["keyId"]

                #
                # Listen State
                #
                if key["upperColor"]["green"] == 255:
                    listen_keys.append(key_id)

                #
                # Call/Talk State
                #
                if key["lowerColor"]["red"] == 255:
                    call_keys.append(key_id)

            snapshot = {
                "panelName": panel_name,
                "panelIp": panel_ip,
                "panelId": PANEL_ID,
                "listenKeys": sorted(listen_keys),
                "callKeys": sorted(call_keys)
            }

            with open(filename, "w") as f:
                json.dump(snapshot, f, indent=2)

            print("")
            print(f"Saved snapshot: {filename}")
            print(json.dumps(snapshot, indent=2))
            print("")

            return


asyncio.run(main())
