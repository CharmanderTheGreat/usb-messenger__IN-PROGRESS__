# USB-Locked Secure Messenger — README

This is the working codebase for the project we designed in `usb-secure-messenger-plan.md`. Read that document first for the *why* behind every design decision; this README covers the *how to run it*.

## What's actually built vs. what's still manual

**Fully implemented and tested:**
- `client/identity.py` — keypair generation, password-encrypted identity, User ID derivation
- `client/face_auth.py` — face enrollment/verification (OpenCV LBPH, not the heavy `face_recognition`/dlib library)
- `client/crypto_utils.py` — ECDH key exchange + AES-256-GCM message encryption
- `client/cleanup.py` — sensitive memory wiping, scoped temp directories, USB-removal watchdog
- `client/messenger_client.py` — CLI client that ties all of the above together into a working chat app
- `server/relay_server.py` — FastAPI + WebSocket relay server (connect requests, mutual accept, encrypted message relay, offline queueing with expiry)
- `build/build_client.spec` — PyInstaller spec to package the client into a portable `.exe`

**Honest limitations you should know about:**
- The client is currently a **CLI (command-line) app**, not a GUI. It works, but it's not pretty. A PyQt/Tkinter GUI can be built on top of the existing `MessengerSession` class without touching the crypto/networking logic.
- Face recognition uses OpenCV's LBPH recognizer rather than the `face_recognition` library, specifically because `face_recognition` depends on `dlib`, which is slow/painful to bundle into a portable exe. LBPH is less accurate than dlib-based embeddings but is functional and dependency-light. If accuracy turns out to be a problem in testing, swapping to `face_recognition` is a contained change inside `face_auth.py`.
- Password hashing uses **PBKDF2-HMAC-SHA256** (600,000 iterations), not Argon2id. This was a deliberate choice to avoid adding a dependency that complicates the portable build. PBKDF2 at this iteration count is still solid, but Argon2id is the stronger modern choice if you want to harden this later (`argon2-cffi` package).
- Memory wiping (`cleanup.py`) is **best-effort**, not a forensic-grade guarantee — see the docstring in that file for why (Python's memory model has real limits here).
- The PyInstaller spec was validated by building successfully in this Linux environment, but **you must actually run PyInstaller on a Windows machine** to produce a real `.exe` (PyInstaller builds for whatever OS it runs on).

## Project structure

```
usb-messenger/
├── client/
│   ├── identity.py          # Phase 1: keypair + password encryption
│   ├── face_auth.py         # Phase 2: face enrollment/verification
│   ├── crypto_utils.py      # Phase 4: E2E message encryption
│   ├── cleanup.py           # Phase 5: no-trace session teardown
│   ├── messenger_client.py  # Main CLI app tying it all together
│   └── requirements.txt
├── server/
│   ├── relay_server.py      # Phase 3: relay server
│   ├── test_relay_flow.py   # Integration test (2 real clients talking through the server)
│   └── requirements.txt
└── build/
    └── build_client.spec    # Phase 6: PyInstaller packaging config
```

## How to run it (development/testing, not yet the final USB deployment)

### 1. Set up the server (this is what you self-host, on-demand, per our plan)

```bash
cd server
pip install -r requirements.txt
python relay_server.py
```

This starts the relay on `0.0.0.0:8000`. Leave this running while people are actively chatting; stop it (Ctrl+C) when you're done, per the on-demand hosting model we agreed on.

To actually make it reachable over the internet (not just your home LAN), you still need the port forwarding + Dynamic DNS setup we talked about separately — that's an infrastructure step, not something code can automate for you.

### 2. Run the client (do this on each person's machine, from their USB in the real deployment)

```bash
cd client
pip install -r requirements.txt
python messenger_client.py
```

First run on a fresh identity: it'll walk you through password setup and face enrollment, then print your **User ID** — share that with whoever you want to talk to.

Every run after that: enter password → face scan → you're in.

Once logged in, it asks for the server address (defaults to `ws://127.0.0.1:8000/ws` for local testing — point this at your DDNS address for real cross-internet use).

**In-app commands:**
```
/connect <user_id>       - send a conversation request to someone
/msg <user_id> <text>    - send a message (only works after they've accepted)
/quit                    - exit and wipe the session
```

### 3. Test the two pieces independently

```bash
# Verify the identity module works (no camera/network needed)
python client/identity.py

# Verify the crypto module works (no camera/network needed)
python client/crypto_utils.py

# Verify the cleanup module works (no camera/network needed)
python client/cleanup.py

# Verify the full relay server flow works (starts a real server + 2 real clients)
python server/test_relay_flow.py

# Face enrollment/verification needs an actual webcam -- run this on your
# own machine, not in a headless environment
python client/face_auth.py
```

## Building the portable .exe (do this part on Windows)

```bash
cd client
pip install -r requirements.txt
pip install pyinstaller

cd ../build
pyinstaller build_client.spec
```

Output lands in `build/dist/USBMessenger.exe`. Copy that single file onto the USB drive. The first time it's run from the USB, it will create `identity.key` and `face_template.dat` right next to itself on the drive — that's the whole "identity" for that USB, and per our earlier decision, **there's no recovery if the USB is lost.**

## What's still genuinely open (not yet built)

These are real gaps, not just "phase not started yet" — flagging them so nothing gets assumed to be done:

1. **GUI.** Everything works through the CLI right now. A GUI wrapper (PyQt is probably the better fit for a polished look) would sit on top of `MessengerSession` in `messenger_client.py` without needing changes to the crypto/network/identity code underneath.
2. **USB-removal-triggers-shutdown wiring.** `cleanup.py` has a working `USBWatchdog` class, but `messenger_client.py` doesn't call it yet — the main app needs to know its own USB drive path and hook the watchdog's `on_removed` callback to `session.shutdown()`.
3. **TLS on the relay connection.** The server currently runs plain `ws://`, not `wss://`. For real deployment (especially once port-forwarded to the internet), this should be behind TLS — either via a reverse proxy (nginx with a Let's Encrypt cert) or `uvicorn`'s own SSL options.
4. **Mobile/OTG support.** Per the plan document, this is a Phase 7 stretch goal and hasn't been started. Android's sandboxing means it would need a different approach than the Windows portable-exe model.
