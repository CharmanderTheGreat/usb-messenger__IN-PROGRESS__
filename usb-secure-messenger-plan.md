# USB-Locked Secure Messenger — System Design & Plan

## 1. Concept Summary

A **portable, no-install messaging application** that runs entirely from a USB drive. Possession of the USB (with the software and its unique cryptographic identity) is a mandatory access factor — without it, there is no way to participate in the system at all.

**Three-factor access model:**

| Factor | Type | Implementation |
|---|---|---|
| The USB itself | Something you **have** | Unique keypair generated & stored on that specific USB |
| Password | Something you **know** | Unlocks the encrypted private key via PBKDF2/Argon2 |
| Face scan | Something you **are** | Local biometric check before key is usable |

No factor alone is enough. Lose the USB → no access, even with password + face. Clone the software without the key file → useless copy.

---

## 2. High-Level System Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                        USB DRIVE                              │
│  ┌───────────────┐  ┌──────────────┐  ┌────────────────────┐ │
│  │ app.exe        │  │ identity.key │  │ face_template.dat  │ │
│  │ (portable,     │  │ (encrypted   │  │ (encrypted face    │ │
│  │  self-contained)│  │  keypair)    │  │  embedding)        │ │
│  └───────────────┘  └──────────────┘  └────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
         │ plug into any Windows PC
         ▼
┌─────────────────────────────────────────────────────────────┐
│                  RUNTIME (RAM only, on host PC)                │
│                                                                 │
│   [Password Prompt] → [Face Scan] → [Decrypt Private Key]     │
│                              │                                  │
│                              ▼                                  │
│                    [Unlocked Session]                          │
│                              │                                  │
│                    ┌─────────┴─────────┐                       │
│                    ▼                   ▼                       │
│            [Add/Connect by      [Send/Receive                  │
│             User ID]             Encrypted Messages]           │
└─────────────────────────────────────────────────────────────┘
                              │
                    WebSocket (TLS)
                              ▼
              ┌───────────────────────────────┐
              │     RELAY SERVER (FastAPI)      │
              │  - Cannot read message content   │
              │  - Only sees: sender_id,         │
              │    recipient_id, encrypted_blob  │
              │  - Temporary queue for offline   │
              │    recipients                    │
              └───────────────────────────────┘
                              │
                    WebSocket (TLS)
                              ▼
                 (Same runtime flow on the
                  other person's USB + PC)
```

---

## 3. Identity & Key Lifecycle

```
FIRST-EVER USB SETUP
─────────────────────
1. User plugs blank/new USB, runs setup wizard
2. App generates keypair (public_key, private_key) — e.g. X25519 or RSA-4096
3. User sets password
4. private_key is encrypted with a key derived from the password
   (Argon2id → AES-256-GCM) and saved as identity.key on USB
5. User enrolls face (webcam captures embedding, NOT raw photo,
   encrypted and saved as face_template.dat on USB)
6. public_key is hashed/encoded into a short "User ID" (e.g. 12 chars)
   → this is what the user shares to be contacted

EVERY SUBSEQUENT LOGIN
─────────────────────
1. Plug USB → app auto-runs from USB (no install)
2. Enter password → attempt to decrypt identity.key
   ✗ Wrong password → fail, no key material exposed
3. Face scan → compare live embedding vs. face_template.dat
   ✗ No match → fail, even with correct password
4. ✓ Both pass → private_key loaded into RAM only
5. Session begins; connects to relay server using public key
   as identity proof (challenge-response, not stored password)
```

**Key principle:** the private key **never leaves the USB unencrypted**, and only exists **decrypted in RAM** during an active, authenticated session.

---

## 4. Messaging / Connection Flow

Since this is **not a fixed pair** — anyone with the software can potentially talk to anyone else — there must be an explicit mutual-consent step so it doesn't become an open free-for-all:

```
USER A                          RELAY SERVER                    USER B
  │                                   │                             │
  │  "I want to connect to            │                             │
  │   User ID: XJ29-KD41"             │                             │
  ├──────────────────────────────────►│                             │
  │                                   │  forwards connect request   │
  │                                   ├────────────────────────────►│
  │                                   │                             │
  │                                   │   [Accept / Reject prompt]  │
  │                                   │◄────────────────────────────┤
  │      notified: accepted            │                             │
  │◄──────────────────────────────────┤                             │
  │                                   │                             │
  │◄═══════ Key Exchange (ECDH) via server relay, server can't ═══►│
  │         derive the resulting shared secret                     │
  │                                   │                             │
  │  Now both sides hold a shared     │                             │
  │  symmetric key (AES-256-GCM)      │                             │
  │  used to encrypt all messages     │                             │
  │  between exactly these two users  │                             │
  │                                   │                             │
  │  encrypt("hello") ──────────────► │ ──────────────────────────► │
  │                                   │   (server only relays        │
  │                                   │    ciphertext + routing)     │
  │                                   │                             │  decrypt("hello")
```

**Why this is secure even without fixed pairing:**
- Knowing someone's User ID is not enough — they must explicitly accept
- The relay server only ever sees: `sender_id → recipient_id : <encrypted blob>`
- Each conversation gets its own shared key (derived via ECDH), so compromising one conversation doesn't expose others
- Replay/tampering protection via AES-GCM's built-in authentication tag

---

## 5. "No-Trace" / Cleanup Requirement

Since nothing should be installed on the host PC:

| Concern | Mitigation |
|---|---|
| Temp files during runtime | Store only in a temp folder scoped to the app; encrypt anything written to disk (even temp) |
| RAM residue after exit | Explicitly zero out key material in memory on close (not just letting garbage collector handle it) |
| Unexpected USB removal | Detect disconnect → immediately kill session, wipe in-memory keys, attempt to flush/delete temp files |
| Antivirus flags | Expected for unsigned portable exes — document this, consider code-signing later |

---

## 6. Proposed Tech Stack

| Layer | Choice | Notes |
|---|---|---|
| Client app | Python (PyInstaller → portable `.exe`) | You already know Python-adjacent tooling; PyInstaller bundles everything needed onto the USB |
| GUI | PyQt or Tkinter | PyQt looks more modern; Tkinter is faster to prototype |
| Face recognition | `face_recognition` (dlib) or OpenCV + a lightweight embedding model | Runs locally, no cloud dependency |
| Crypto | Python `cryptography` library | Never hand-roll crypto — use audited primitives (X25519, AES-256-GCM, Argon2id) |
| Relay server | FastAPI + WebSockets | You already have FastAPI experience from RosaCycle |
| Transport security | TLS (wss://) between client and server | Protects metadata in transit even though content is already E2E encrypted |

**Mobile/OTG note:** Android sandboxing makes "true" portable/no-install execution impractical. Realistic path is a separate installed Android companion app that reads its identity key from an OTG-connected USB/microSD rather than storing it in normal app storage — worth treating as a Phase 6+ stretch goal, not part of the initial build.

---

## 6a. Hosting Model — Self-Hosted, On-Demand (No Cloud, No Free-Tier Limits)

**Decision:** Reject third-party free-tier hosting (auto-sleep, usage quotas, token/hour limits). The relay server runs on hardware you personally control, only while actively in use — not 24/7.

**Why self-hosted instead of free-tier cloud:**
- No expiry, no monthly hour caps, no cold-starts from a platform putting the server to sleep
- Zero dependency on a third party's policies or pricing changes
- Fits the "I control who can use this" philosophy already driving the rest of the design

**On-demand model — what it means in practice:**

| Aspect | Behavior |
|---|---|
| Server uptime | Only running while you deliberately start it (e.g., before a chat session) |
| Availability to others | Both parties must be online **at the same time** — no server = no delivery, even within the "few hours" offline queue window from Section 8 |
| Shutdown | Just stop the process — nothing installed, nothing lingers |

**Hosting device options:**

| Device | Feasibility | Notes |
|---|---|---|
| Spare/retired laptop or PC | Best option | Stable, full OS, no battery/thermal concerns, easiest to secure (firewall, updates) |
| Always-on dedicated machine (mini PC, Raspberry Pi) | Great if available | Purpose-built for this kind of always-there-but-controlled-by-you role |
| Phone via Termux | Workable for on-demand, casual use | See below |
| Daily-driver phone, 24/7 | **Not recommended** | Battery drain, thermal load, background-kill risk from Android's power management |

**Phone-as-server via Termux (on-demand use case):**
1. Install Termux, set up Python + FastAPI inside it
2. Start the relay server manually only when a chat session is wanted
3. Share the current address (IP or DDNS name) with the other party for that session
4. Stop the script in Termux when done — no persistent background service required
5. Because it's not 24/7, Android's aggressive battery-optimization/doze killing of background apps is much less of a problem — the app only needs to survive while the screen/session is active

**Still needed regardless of which device hosts it:**
- **Port forwarding** on the home router, so the device is reachable from outside the LAN
- **Dynamic DNS** (free options: DuckDNS, No-IP), since home internet IP addresses usually change periodically — this gives a stable name to point to even as the underlying IP shifts
- **TLS/wss://** on the relay connection, and firewall rules limiting what's exposed on the router beyond the one port needed

**Tradeoff to keep in mind:** self-hosted + on-demand means the messenger only works when you choose to make it work — this is a deliberate control/security choice, not a bug, but it does mean no "always reachable" experience like a commercial messenger.

---

## 7. Build Phases

1. **Phase 1 — Identity core:** keypair generation, password-based encryption of private key, User ID derivation
2. **Phase 2 — Auth flow:** password prompt + face enrollment/verification, session unlock logic
3. **Phase 3 — Relay server:** FastAPI + WebSocket server, connect request/accept flow, message relay (ciphertext only)
4. **Phase 4 — E2E messaging:** ECDH key exchange per conversation, AES-GCM encrypt/decrypt, basic chat UI
5. **Phase 5 — Cleanup & hardening:** memory wiping, temp file encryption/deletion, USB-removal detection
6. **Phase 6 — Packaging:** PyInstaller portable build, test across multiple Windows machines
7. **Phase 7 (stretch) — Mobile/OTG companion**

---

## 8. Design Decisions (finalized)

| Question | Decision | Implication |
|---|---|---|
| Lost USB recovery | **No recovery — intentionally unrecoverable** | If the USB is lost/destroyed, that identity is gone permanently. No backdoor, no reset mechanism. This is what makes the "possession = access" model actually mean something — a recovery path would be an exploitable weak point. Users should be warned clearly during setup that this is irreversible. |
| Server registration | **Fully anonymous — public key is the only identity** | No email, no username, no account database. The server never learns who a person is, only that "this public key exists and wants to relay messages." Minimizes what could ever be leaked or subpoenaed from the server. |
| Offline message retention | **Few hours only, then expire** | Keeps server storage minimal and reduces the window where encrypted-but-intercepted blobs could sit around. If the recipient doesn't come online in time, the message is dropped — sender should get a "delivery failed/expired" notice so they know to resend. |

**Combined effect:** this system deliberately favors airtight security over convenience at every fork — no recovery, no accounts, no long-term server storage. Good to state this explicitly to yourself now, because it means the eventual UX has to be very clear about "this is destroyed if you lose the USB" so users aren't blindsided.
