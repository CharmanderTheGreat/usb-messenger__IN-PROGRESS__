"""
identity.py
-----------
Phase 1: Identity core for the USB-Locked Secure Messenger.

Responsibilities:
  1. Generate a unique X25519 keypair for a USB the first time it's set up.
  2. Encrypt the private key using a password (so the key file alone,
     without the password, is useless).
  3. Derive a short, shareable "User ID" from the public key.
  4. Decrypt the private key back into memory during login (Phase 2 will
     add face recognition on top of this).

Design notes:
  - We use X25519 (Elliptic Curve Diffie-Hellman) because it's the
    standard modern choice for key exchange (same primitive used by
    Signal). We are NOT designing our own crypto scheme — just wiring
    together audited building blocks from the `cryptography` library.
  - Password -> encryption key uses Argon2id via `cryptography`'s
    dependency stack is not built in, so we use PBKDF2-HMAC here for
    zero extra dependencies. (We can swap to Argon2id later using the
    `argon2-cffi` package if you want stronger password hardening —
    flagged as a Phase 5 hardening item.)
  - Nothing here touches the network or the UI. This module only
    handles "what lives in the identity.key file on the USB."
"""

import os
import json
import base64
import hashlib
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# ---- Tunable constants -----------------------------------------------

PBKDF2_ITERATIONS = 600_000  # OWASP-recommended minimum as of 2023+
SALT_SIZE = 16
NONCE_SIZE = 12  # AES-GCM standard nonce size
USER_ID_LENGTH = 12  # characters shown to the user, e.g. "XJ29-KD41-QR"


class IdentityError(Exception):
    """Raised for any identity setup/login failure (wrong password, corrupt file, etc.)."""
    pass


def generate_keypair() -> tuple[X25519PrivateKey, X25519PublicKey]:
    """Generate a brand-new X25519 keypair. Called once, at first-time USB setup."""
    private_key = X25519PrivateKey.generate()
    public_key = private_key.public_key()
    return private_key, public_key


def derive_user_id(public_key: X25519PublicKey) -> str:
    """
    Turn a public key into a short, human-shareable User ID.
    This is what a user gives out to be contacted -- never the raw key.
    """
    raw_public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    digest = hashlib.sha256(raw_public_bytes).digest()
    # Base32 gives us readable, unambiguous characters (no 0/O/1/I confusion issues
    # like some encodings have)
    encoded = base64.b32encode(digest).decode("ascii").rstrip("=")
    short_id = encoded[:USER_ID_LENGTH]
    # Format as XXXX-XXXX-XXXX for readability
    return "-".join(short_id[i:i + 4] for i in range(0, len(short_id), 4))


def _derive_key_from_password(password: str, salt: bytes) -> bytes:
    """Stretch a password into a 32-byte AES key using PBKDF2-HMAC-SHA256."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(password.encode("utf-8"))


def create_identity(password: str) -> dict:
    """
    First-time setup: generate a new identity and return the data structure
    that should be written to identity.key on the USB.

    Returns a dict ready to be JSON-serialized. Nothing is written to disk
    here -- that's the caller's job (keeps this module easily testable).
    """
    private_key, public_key = generate_keypair()

    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    salt = os.urandom(SALT_SIZE)
    nonce = os.urandom(NONCE_SIZE)
    aes_key = _derive_key_from_password(password, salt)

    aesgcm = AESGCM(aes_key)
    encrypted_private_key = aesgcm.encrypt(nonce, private_bytes, None)

    user_id = derive_user_id(public_key)

    identity_record = {
        "version": 1,
        "user_id": user_id,
        "public_key": base64.b64encode(public_bytes).decode("ascii"),
        "encrypted_private_key": base64.b64encode(encrypted_private_key).decode("ascii"),
        "salt": base64.b64encode(salt).decode("ascii"),
        "nonce": base64.b64encode(nonce).decode("ascii"),
        "kdf": "pbkdf2_sha256",
        "kdf_iterations": PBKDF2_ITERATIONS,
    }
    return identity_record


def unlock_identity(identity_record: dict, password: str) -> X25519PrivateKey:
    """
    Attempt to decrypt the private key using the supplied password.
    Raises IdentityError on wrong password or corrupted data.

    This is the function Phase 2's login flow will call *after* face
    verification passes, so both factors are required before the key
    ever exists in memory.
    """
    try:
        salt = base64.b64decode(identity_record["salt"])
        nonce = base64.b64decode(identity_record["nonce"])
        encrypted_private_key = base64.b64decode(identity_record["encrypted_private_key"])
    except (KeyError, ValueError) as e:
        raise IdentityError(f"Identity file is corrupted or malformed: {e}")

    aes_key = _derive_key_from_password(password, salt)
    aesgcm = AESGCM(aes_key)

    try:
        private_bytes = aesgcm.decrypt(nonce, encrypted_private_key, None)
    except Exception:
        # AESGCM raises InvalidTag on wrong key/tampered data -- we don't
        # leak *why* it failed, just that it failed.
        raise IdentityError("Incorrect password or corrupted identity file.")

    return X25519PrivateKey.from_private_bytes(private_bytes)


def save_identity(identity_record: dict, path: str) -> None:
    """Write the identity record to disk (e.g. onto the USB) as JSON."""
    with open(path, "w") as f:
        json.dump(identity_record, f, indent=2)


def load_identity(path: str) -> dict:
    """Read an identity record back from disk."""
    if not os.path.exists(path):
        raise IdentityError(f"No identity file found at {path}")
    with open(path, "r") as f:
        return json.load(f)


# ---- Manual test / demo ------------------------------------------------
if __name__ == "__main__":
    print("=== Phase 1 Demo: Identity Creation & Unlock ===\n")

    test_password = "correct-horse-battery-staple"
    identity_path = "test_identity.key"

    print("[1] Creating new identity...")
    record = create_identity(test_password)
    print(f"    User ID: {record['user_id']}")
    print(f"    Public key (b64): {record['public_key'][:32]}...")

    print("\n[2] Saving to disk (simulating USB write)...")
    save_identity(record, identity_path)
    print(f"    Saved to {identity_path}")

    print("\n[3] Loading identity back from disk...")
    loaded = load_identity(identity_path)
    print(f"    Loaded User ID: {loaded['user_id']}")

    print("\n[4] Unlocking with CORRECT password...")
    try:
        priv_key = unlock_identity(loaded, test_password)
        print("    Success -- private key decrypted into memory.")
    except IdentityError as e:
        print(f"    FAILED: {e}")

    print("\n[5] Unlocking with WRONG password...")
    try:
        priv_key = unlock_identity(loaded, "wrong-password")
        print("    Unexpected success -- this should not happen!")
    except IdentityError as e:
        print(f"    Correctly rejected: {e}")

    os.remove(identity_path)
    print("\n[cleanup] Removed test identity file.")
