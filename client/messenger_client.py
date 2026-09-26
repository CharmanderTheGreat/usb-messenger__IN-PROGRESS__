"""
messenger_client.py
--------------------
Main client application. Ties together every module built so far:

  identity.py     -> USB-bound keypair, password-encrypted, User ID
  face_auth.py     -> biometric factor on top of the password
  crypto_utils.py  -> per-conversation E2E encryption (ECDH + AES-GCM)
  cleanup.py       -> no-trace session teardown

This is a CLI (command-line) chat client for now -- functional, not
pretty. A GUI (PyQt/Tkinter) can be layered on top of this same logic
later without changing anything below; the login/session/messaging
logic here is UI-agnostic on purpose.

Run this from the same folder as identity.py, face_auth.py, etc.
(i.e. from the client/ directory), with the relay server already
running (see server/relay_server.py).

Usage:
    python messenger_client.py
"""

import asyncio
import getpass
import json
import os
import sys

import websockets

import identity
import crypto_utils
import cleanup

IDENTITY_FILE = "identity.key"  # lives on the USB alongside this script
DEFAULT_SERVER_URL = "ws://127.0.0.1:8000/ws"


class MessengerSession:
    def __init__(self):
        self.private_key = None
        self.public_key_bytes = None
        self.user_id = None
        self.cleanup_manager = cleanup.SessionCleanupManager()
        self.websocket = None
        # peer_user_id -> shared AES key (bytearray, wiped on exit)
        self.conversation_keys: dict[str, bytearray] = {}
        # peer_user_id -> their raw public key bytes (needed to derive shared key)
        self.peer_public_keys: dict[str, bytes] = {}
        self.pending_outgoing_requests: set[str] = set()

    # ---- Setup / Login ---------------------------------------------

    def first_time_setup(self) -> None:
        print("No identity found on this USB. Let's set one up.\n")
        password = getpass.getpass("Choose a password: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Passwords did not match. Exiting.")
            sys.exit(1)

        print("\nEnrolling your face (this requires a webcam)...")
        try:
            import face_auth
            face_model_bytes = face_auth.enroll_face()
            print("Face enrolled successfully.")
        except Exception as e:
            print(f"[Warning] Face enrollment failed or no camera available: {e}")
            print("Continuing WITHOUT face enrollment for this session -- ")
            print("in a real deployment, this should block setup instead.")
            face_model_bytes = None

        record = identity.create_identity(password)
        identity.save_identity(record, IDENTITY_FILE)

        if face_model_bytes is not None:
            self._save_face_template(face_model_bytes, password)

        print(f"\nSetup complete. Your User ID is: {record['user_id']}")
        print("Share this ID with people who want to message you.\n")

    def _save_face_template(self, model_bytes: bytes, password: str) -> None:
        """Encrypt and save the face model using the same password-based
        scheme as the identity key, kept in a sibling file."""
        import base64
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        salt = os.urandom(16)
        nonce = os.urandom(12)
        aes_key = identity._derive_key_from_password(password, salt)
        encrypted = AESGCM(aes_key).encrypt(nonce, model_bytes, None)

        with open("face_template.dat", "w") as f:
            json.dump({
                "salt": base64.b64encode(salt).decode(),
                "nonce": base64.b64encode(nonce).decode(),
                "encrypted_model": base64.b64encode(encrypted).decode(),
            }, f)

    def _load_face_template(self, password: str) -> bytes:
        import base64
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        with open("face_template.dat", "r") as f:
            record = json.load(f)

        salt = base64.b64decode(record["salt"])
        nonce = base64.b64decode(record["nonce"])
        encrypted = base64.b64decode(record["encrypted_model"])

        aes_key = identity._derive_key_from_password(password, salt)
        return AESGCM(aes_key).decrypt(nonce, encrypted, None)

    def login(self) -> bool:
        """Full multi-factor login: password -> face -> key unlock.
        Returns True on success."""
        record = identity.load_identity(IDENTITY_FILE)
        password = getpass.getpass("Password: ")

        # Factor 2: face verification (if a face template exists)
        if os.path.exists("face_template.dat"):
            try:
                face_model_bytes = self._load_face_template(password)
            except Exception:
                print("Incorrect password (face template could not be decrypted).")
                return False

            print("Please look at the camera for face verification...")
            try:
                import face_auth
                if not face_auth.verify_face(face_model_bytes):
                    print("Face verification failed.")
                    return False
            except Exception as e:
                print(f"[Warning] Face verification skipped (no camera?): {e}")
        else:
            print("[Warning] No face template enrolled -- skipping biometric factor.")

        # Factor 3 (final gate): unlock the private key with the password
        try:
            self.private_key = identity.unlock_identity(record, password)
        except identity.IdentityError as e:
            print(f"Login failed: {e}")
            return False

        import base64
        from cryptography.hazmat.primitives import serialization
        self.public_key_bytes = self.private_key.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.user_id = record["user_id"]

        print(f"\nLogin successful. Welcome back, {self.user_id}.")
        return True

    # ---- Networking / Messaging -------------------------------------

    async def connect_to_server(self, server_url: str = DEFAULT_SERVER_URL) -> None:
        ws_url = f"{server_url}/{self.user_id}"
        self.websocket = await websockets.connect(ws_url)
        print(f"Connected to relay server as {self.user_id}.")

    async def request_conversation(self, peer_user_id: str) -> None:
        import base64
        self.pending_outgoing_requests.add(peer_user_id)
        await self.websocket.send(json.dumps({
            "type": "connect_request",
            "to": peer_user_id,
            "public_key": base64.b64encode(self.public_key_bytes).decode(),
        }))
        print(f"Connect request sent to {peer_user_id}. Waiting for them to accept...")

    async def send_message(self, peer_user_id: str, text: str) -> None:
        if peer_user_id not in self.conversation_keys:
            print(f"No established secure conversation with {peer_user_id} yet.")
            return
        shared_key = bytes(self.conversation_keys[peer_user_id])
        encrypted_blob = crypto_utils.encrypt_message(shared_key, text)
        await self.websocket.send(json.dumps({
            "type": "message",
            "to": peer_user_id,
            "blob": encrypted_blob,
        }))

    def _establish_conversation_key(self, peer_user_id: str, peer_public_key_bytes: bytes) -> None:
        shared_key = crypto_utils.derive_shared_key(self.private_key, peer_public_key_bytes)
        buf = bytearray(shared_key)
        self.conversation_keys[peer_user_id] = buf
        self.cleanup_manager.register_sensitive_buffer(buf)

    async def listen_loop(self) -> None:
        """Background loop handling all incoming server messages."""
        async for raw in self.websocket:
            data = json.loads(raw)
            msg_type = data.get("type")

            if msg_type == "connect_request":
                from_id = data["from"]
                peer_public_key_b64 = data["public_key"]
                print(f"\n[Incoming connect request from {from_id}]")
                answer = input(f"Accept conversation with {from_id}? (y/n): ").strip().lower()
                accepted = answer == "y"
                import base64
                await self.websocket.send(json.dumps({
                    "type": "connect_response",
                    "to": from_id,
                    "accepted": accepted,
                    "public_key": base64.b64encode(self.public_key_bytes).decode() if accepted else None,
                }))
                if accepted:
                    self._establish_conversation_key(from_id, base64.b64decode(peer_public_key_b64))
                    print(f"Secure conversation established with {from_id}.")

            elif msg_type == "connect_response":
                from_id = data["from"]
                if data.get("accepted"):
                    import base64
                    peer_public_key_b64 = data["public_key"]
                    self._establish_conversation_key(from_id, base64.b64decode(peer_public_key_b64))
                    print(f"\n{from_id} accepted your request! Secure conversation established.")
                else:
                    print(f"\n{from_id} declined your request.")
                self.pending_outgoing_requests.discard(from_id)

            elif msg_type == "message":
                from_id = data["from"]
                blob = data["blob"]
                if from_id not in self.conversation_keys:
                    print(f"\n[Received message from {from_id} but no shared key exists -- dropping]")
                    continue
                try:
                    shared_key = bytes(self.conversation_keys[from_id])
                    plaintext = crypto_utils.decrypt_message(shared_key, blob)
                    print(f"\n{from_id}: {plaintext}")
                except crypto_utils.CryptoError as e:
                    print(f"\n[Failed to decrypt message from {from_id}: {e}]")

            elif msg_type == "error":
                print(f"\n[Server error]: {data.get('message')}")

    def shutdown(self) -> None:
        """Full session teardown -- call on exit or USB removal."""
        self.cleanup_manager.wipe_all()
        print("\nSession wiped. Goodbye.")


async def main():
    session = MessengerSession()

    if not os.path.exists(IDENTITY_FILE):
        session.first_time_setup()

    if not session.login():
        sys.exit(1)

    server_url = input(f"\nServer address [{DEFAULT_SERVER_URL}]: ").strip() or DEFAULT_SERVER_URL
    await session.connect_to_server(server_url)

    listen_task = asyncio.create_task(session.listen_loop())

    print("\nCommands:")
    print("  /connect <user_id>   - request a conversation")
    print("  /msg <user_id> <text> - send a message")
    print("  /quit                - exit and wipe session\n")

    try:
        while True:
            line = await asyncio.get_event_loop().run_in_executor(None, input, "> ")
            if line.startswith("/connect "):
                peer_id = line.split(" ", 1)[1].strip()
                await session.request_conversation(peer_id)
            elif line.startswith("/msg "):
                _, rest = line.split(" ", 1)
                peer_id, text = rest.split(" ", 1)
                await session.send_message(peer_id, text)
            elif line.strip() == "/quit":
                break
            else:
                print("Unknown command.")
    finally:
        listen_task.cancel()
        session.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
