"""
relay_server.py
----------------
Phase 3: Relay server.

Responsibilities:
  1. Accept WebSocket connections from clients, identified by their
     User ID (derived from their public key -- see client/identity.py).
  2. Handle "connect request" / "accept" / "reject" handshake between
     two users who want to start a conversation.
  3. Relay already-encrypted message blobs between connected users.
     The server NEVER sees plaintext and NEVER holds private keys --
     it only ever touches ciphertext plus routing metadata
     (sender_id, recipient_id).
  4. Queue messages for a short time if the recipient is offline, then
     expire them (per the "few hours only" retention decision).

Design notes:
  - Fully anonymous: there is no account database, no registration, no
    password check at the server level. A User ID is just a public
    identifier -- proving you legitimately "own" it happens implicitly
    because only the real private-key holder can ever produce messages
    that decrypt correctly on the other end. The server doesn't need to
    verify identity beyond "something connected and claims this ID."
  - This file is meant to run via: `uvicorn relay_server:app --host 0.0.0.0 --port 8000`
  - No message content is ever written to a database. Everything lives
    in memory only, and is dropped when it expires or is delivered.
"""

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

app = FastAPI(title="USB-Locked Secure Messenger Relay")

# ---- Tunable constants -----------------------------------------------

OFFLINE_MESSAGE_TTL_SECONDS = 3 * 60 * 60  # "a few hours" per the finalized design decision
CLEANUP_INTERVAL_SECONDS = 60 * 5          # how often we sweep for expired messages


# ---- In-memory state ---------------------------------------------------
# Everything here is RAM-only. Restarting the server wipes all of it,
# which is intentional -- there is no persistent server-side storage
# of any conversation data.

@dataclass
class QueuedMessage:
    sender_id: str
    encrypted_blob: str
    timestamp: float = field(default_factory=time.time)


class ConnectionManager:
    def __init__(self):
        # user_id -> active WebSocket connection (None if offline)
        self.active_connections: dict[str, WebSocket] = {}
        # recipient_id -> list of messages waiting for them to come online
        self.pending_messages: dict[str, list[QueuedMessage]] = {}
        # Pending connect requests: recipient_id -> list of requester_ids
        self.pending_requests: dict[str, list[str]] = {}
        # Accepted conversation pairs (both directions stored for simplicity)
        self.accepted_pairs: set[tuple[str, str]] = set()

    def is_pair_accepted(self, user_a: str, user_b: str) -> bool:
        return (user_a, user_b) in self.accepted_pairs or (user_b, user_a) in self.accepted_pairs

    async def connect(self, user_id: str, websocket: WebSocket):
        await websocket.accept()
        self.active_connections[user_id] = websocket
        await self._flush_pending_messages(user_id)

    def disconnect(self, user_id: str):
        self.active_connections.pop(user_id, None)

    async def _flush_pending_messages(self, user_id: str):
        """Deliver any messages that arrived while this user was offline."""
        queued = self.pending_messages.pop(user_id, [])
        websocket = self.active_connections.get(user_id)
        if not websocket:
            return
        now = time.time()
        for msg in queued:
            if now - msg.timestamp > OFFLINE_MESSAGE_TTL_SECONDS:
                continue  # expired, drop silently
            await websocket.send_json({
                "type": "message",
                "from": msg.sender_id,
                "blob": msg.encrypted_blob,
            })

    async def send_connect_request(self, from_id: str, to_id: str, from_public_key_b64: str):
        target_ws = self.active_connections.get(to_id)
        self.pending_requests.setdefault(to_id, [])
        if from_id not in self.pending_requests[to_id]:
            self.pending_requests[to_id].append(from_id)

        if target_ws:
            await target_ws.send_json({
                "type": "connect_request",
                "from": from_id,
                "public_key": from_public_key_b64,
            })
        # If target is offline, the request just waits in pending_requests
        # until they connect and query it -- kept simple for this version.

    async def respond_to_request(self, responder_id: str, requester_id: str, accepted: bool, responder_public_key_b64: str):
        # Clean up the pending request regardless of outcome
        if responder_id in self.pending_requests:
            self.pending_requests[responder_id] = [
                r for r in self.pending_requests[responder_id] if r != requester_id
            ]

        if accepted:
            self.accepted_pairs.add((requester_id, responder_id))

        requester_ws = self.active_connections.get(requester_id)
        if requester_ws:
            await requester_ws.send_json({
                "type": "connect_response",
                "from": responder_id,
                "accepted": accepted,
                "public_key": responder_public_key_b64 if accepted else None,
            })

    async def relay_message(self, sender_id: str, recipient_id: str, encrypted_blob: str):
        if not self.is_pair_accepted(sender_id, recipient_id):
            # Refuse to relay between users who haven't mutually accepted --
            # this is the enforcement point for "no open free-for-all messaging."
            sender_ws = self.active_connections.get(sender_id)
            if sender_ws:
                await sender_ws.send_json({
                    "type": "error",
                    "message": "Cannot send -- no accepted conversation with this user.",
                })
            return

        recipient_ws = self.active_connections.get(recipient_id)
        if recipient_ws:
            await recipient_ws.send_json({
                "type": "message",
                "from": sender_id,
                "blob": encrypted_blob,
            })
        else:
            # Recipient offline -- queue it, subject to TTL expiry
            self.pending_messages.setdefault(recipient_id, []).append(
                QueuedMessage(sender_id=sender_id, encrypted_blob=encrypted_blob)
            )

    def purge_expired_messages(self):
        """Called periodically to drop anything older than the TTL, even if
        it was never delivered. Keeps memory bounded and enforces the
        'few hours only' retention decision even for recipients who never
        come back online."""
        now = time.time()
        for recipient_id in list(self.pending_messages.keys()):
            fresh = [
                m for m in self.pending_messages[recipient_id]
                if now - m.timestamp <= OFFLINE_MESSAGE_TTL_SECONDS
            ]
            if fresh:
                self.pending_messages[recipient_id] = fresh
            else:
                del self.pending_messages[recipient_id]


manager = ConnectionManager()

_cleanup_task: Optional[asyncio.Task] = None


@app.on_event("startup")
async def start_background_cleanup():
    global _cleanup_task

    async def cleanup_loop():
        while True:
            await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
            manager.purge_expired_messages()

    _cleanup_task = asyncio.create_task(cleanup_loop())


@app.on_event("shutdown")
async def stop_background_cleanup():
    if _cleanup_task is not None:
        _cleanup_task.cancel()


@app.get("/")
async def health_check():
    """Simple endpoint to confirm the relay is up -- returns no user data."""
    return {"status": "online", "active_users": len(manager.active_connections)}


@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    """
    Main entry point for a client session. `user_id` is the short ID
    derived from the client's public key (see identity.derive_user_id).

    Expected incoming JSON message shapes from the client:
      {"type": "connect_request", "to": "<user_id>", "public_key": "<base64>"}
      {"type": "connect_response", "to": "<user_id>", "accepted": true/false, "public_key": "<base64>"}
      {"type": "message", "to": "<user_id>", "blob": "<encrypted_blob>"}
    """
    await manager.connect(user_id, websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON."})
                continue

            msg_type = data.get("type")

            if msg_type == "connect_request":
                to_id = data.get("to")
                public_key = data.get("public_key")
                if to_id and public_key:
                    await manager.send_connect_request(user_id, to_id, public_key)

            elif msg_type == "connect_response":
                to_id = data.get("to")
                accepted = bool(data.get("accepted"))
                public_key = data.get("public_key")
                if to_id:
                    await manager.respond_to_request(user_id, to_id, accepted, public_key)

            elif msg_type == "message":
                to_id = data.get("to")
                blob = data.get("blob")
                if to_id and blob:
                    await manager.relay_message(user_id, to_id, blob)

            else:
                await websocket.send_json({"type": "error", "message": f"Unknown message type: {msg_type}"})

    except WebSocketDisconnect:
        manager.disconnect(user_id)


# ---- Run directly for local/manual testing ------------------------------
if __name__ == "__main__":
    import uvicorn
    print("Starting relay server on http://0.0.0.0:8000")
    print("WebSocket endpoint: ws://<host>:8000/ws/<your_user_id>")
    uvicorn.run(app, host="0.0.0.0", port=8000)
