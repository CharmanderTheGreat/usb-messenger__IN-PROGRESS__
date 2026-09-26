"""
crypto_utils.py
---------------
Phase 4: End-to-end message encryption.

Responsibilities:
  1. Perform an ECDH (Elliptic Curve Diffie-Hellman) key exchange between
     two users' X25519 keypairs to derive a shared secret.
  2. Turn that shared secret into an AES-256 key via HKDF.
  3. Encrypt/decrypt individual chat messages with that per-conversation key.

Design notes:
  - Each pair of users gets its OWN shared key, derived independently on
    each side from (my private key + their public key). Neither side ever
    transmits the shared key itself -- it's mathematically derived, so
    the relay server never sees it and cannot compute it either.
  - We use HKDF (HMAC-based Key Derivation Function) to turn the raw ECDH
    output into a proper AES key, rather than using the raw ECDH bytes
    directly. This is standard practice recommended by cryptography
    library docs and the general ECDH literature.
  - AES-256-GCM provides both confidentiality AND integrity (a tampered
    ciphertext fails to decrypt rather than silently producing garbage).
"""

import os
import base64
from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_SIZE = 12


class CryptoError(Exception):
    """Raised on any encryption/decryption/key-exchange failure."""
    pass


def derive_shared_key(my_private_key: X25519PrivateKey, their_public_key_bytes: bytes) -> bytes:
    """
    Perform ECDH + HKDF to derive a 32-byte AES key shared between two users.

    `their_public_key_bytes` is the raw public key bytes of the OTHER user
    (received during the connect-request/accept handshake).

    Both sides call this independently:
      - User A calls derive_shared_key(A_private, B_public)
      - User B calls derive_shared_key(B_private, A_public)
    Both computations mathematically produce the IDENTICAL shared key,
    without either private key ever being transmitted.
    """
    try:
        their_public_key = X25519PublicKey.from_public_bytes(their_public_key_bytes)
    except Exception as e:
        raise CryptoError(f"Invalid public key received from peer: {e}")

    raw_shared_secret = my_private_key.exchange(their_public_key)

    # Stretch/normalize the raw ECDH output into a proper AES-256 key.
    derived_key = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"usb-messenger-conversation-key",
    ).derive(raw_shared_secret)

    return derived_key


def encrypt_message(shared_key: bytes, plaintext: str) -> str:
    """
    Encrypt a message using the per-conversation shared key.
    Returns a base64 string safe to send over the wire as JSON:
      base64(nonce) + ":" + base64(ciphertext)
    """
    nonce = os.urandom(NONCE_SIZE)
    aesgcm = AESGCM(shared_key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)

    return base64.b64encode(nonce).decode("ascii") + ":" + base64.b64encode(ciphertext).decode("ascii")


def decrypt_message(shared_key: bytes, encoded_blob: str) -> str:
    """
    Decrypt a message produced by encrypt_message().
    Raises CryptoError if the message is corrupted, tampered with, or
    encrypted with a different key (e.g. wrong conversation).
    """
    try:
        nonce_b64, ciphertext_b64 = encoded_blob.split(":", 1)
        nonce = base64.b64decode(nonce_b64)
        ciphertext = base64.b64decode(ciphertext_b64)
    except (ValueError, Exception) as e:
        raise CryptoError(f"Malformed encrypted message: {e}")

    aesgcm = AESGCM(shared_key)
    try:
        plaintext_bytes = aesgcm.decrypt(nonce, ciphertext, None)
    except Exception:
        raise CryptoError("Failed to decrypt -- wrong key, or message was tampered with.")

    return plaintext_bytes.decode("utf-8")


# ---- Manual test / demo ------------------------------------------------
if __name__ == "__main__":
    print("=== Phase 4 Demo: ECDH Key Exchange + Message Encryption ===\n")

    # Simulate two separate users, each with their own keypair
    alice_private = X25519PrivateKey.generate()
    alice_public_bytes = alice_private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    bob_private = X25519PrivateKey.generate()
    bob_public_bytes = bob_private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    print("[1] Alice derives shared key using her private key + Bob's public key")
    alice_shared_key = derive_shared_key(alice_private, bob_public_bytes)

    print("[2] Bob derives shared key using his private key + Alice's public key")
    bob_shared_key = derive_shared_key(bob_private, alice_public_bytes)

    print(f"\n[3] Keys match? {alice_shared_key == bob_shared_key}")
    assert alice_shared_key == bob_shared_key, "ECDH derivation mismatch!"

    print("\n[4] Alice encrypts a message...")
    message = "Hello Bob, this message is end-to-end encrypted!"
    encrypted = encrypt_message(alice_shared_key, message)
    print(f"    Encrypted blob: {encrypted[:50]}...")

    print("\n[5] Bob decrypts it using his independently-derived shared key...")
    decrypted = decrypt_message(bob_shared_key, encrypted)
    print(f"    Decrypted: {decrypted}")
    assert decrypted == message

    print("\n[6] Testing tamper detection -- corrupting the ciphertext...")
    tampered = encrypted[:-4] + "abcd"
    try:
        decrypt_message(bob_shared_key, tampered)
        print("    UNEXPECTED: tampered message decrypted successfully!")
    except CryptoError as e:
        print(f"    Correctly rejected: {e}")

    print("\nAll Phase 4 crypto checks passed.")
