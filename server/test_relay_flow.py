"""
Real end-to-end integration test for relay_server.py.

Unlike FastAPI's TestClient (which runs each websocket connection on its
own isolated event loop/thread and therefore can't reliably test two
sockets that need to talk to *each other* through server-side state),
this test starts an actual uvicorn server on a local port and connects
to it with two genuine websocket clients on the same asyncio event loop.
This is what actually proves the cross-connection relay logic works.
"""
import asyncio
import json
import threading
import time

import uvicorn
import websockets

from relay_server import app

TEST_PORT = 8765
WS_BASE = f"ws://127.0.0.1:{TEST_PORT}/ws"


def run_server_in_thread():
    config = uvicorn.Config(app, host="127.0.0.1", port=TEST_PORT, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    return server


async def wait_for_server_ready(timeout=5):
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{TEST_PORT}/", timeout=0.5)
            return True
        except Exception:
            await asyncio.sleep(0.2)
    return False


async def main():
    print("Starting real uvicorn server in background thread...")
    run_server_in_thread()

    ready = await wait_for_server_ready()
    assert ready, "Server did not start in time"
    print("Server is up.\n")

    async with websockets.connect(f"{WS_BASE}/ALICE-ID") as alice_ws, \
               websockets.connect(f"{WS_BASE}/BOB-ID") as bob_ws:

        print("[1] Alice sends connect_request to Bob...")
        await alice_ws.send(json.dumps({
            "type": "connect_request",
            "to": "BOB-ID",
            "public_key": "QUxJQ0VfUFVCTElDX0tFWQ==",
        }))

        incoming = json.loads(await bob_ws.recv())
        print(f"    Bob received: {incoming}")
        assert incoming["type"] == "connect_request"
        assert incoming["from"] == "ALICE-ID"
        assert incoming["public_key"] == "QUxJQ0VfUFVCTElDX0tFWQ=="

        print("\n[2] Bob accepts...")
        await bob_ws.send(json.dumps({
            "type": "connect_response",
            "to": "ALICE-ID",
            "accepted": True,
            "public_key": "Qk9CX1BVQkxJQ19LRVk=",
        }))

        response = json.loads(await alice_ws.recv())
        print(f"    Alice received: {response}")
        assert response["type"] == "connect_response"
        assert response["accepted"] is True
        assert response["public_key"] == "Qk9CX1BVQkxJQ19LRVk="

        print("\n[3] Alice sends an encrypted message to Bob...")
        fake_encrypted_blob = "bm9uY2U=:Y2lwaGVydGV4dA=="
        await alice_ws.send(json.dumps({"type": "message", "to": "BOB-ID", "blob": fake_encrypted_blob}))

        received_msg = json.loads(await bob_ws.recv())
        print(f"    Bob received: {received_msg}")
        assert received_msg["type"] == "message"
        assert received_msg["from"] == "ALICE-ID"
        assert received_msg["blob"] == fake_encrypted_blob

    print("\n[4] Testing rejection path -- Carol tries to message Bob without an accepted pair...")
    async with websockets.connect(f"{WS_BASE}/CAROL-ID") as carol_ws, \
               websockets.connect(f"{WS_BASE}/BOB-ID") as bob_ws2:
        await carol_ws.send(json.dumps({"type": "message", "to": "BOB-ID", "blob": "should-be-blocked"}))
        error_resp = json.loads(await carol_ws.recv())
        print(f"    Carol received: {error_resp}")
        assert error_resp["type"] == "error"

    print("\nAll relay server integration tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
