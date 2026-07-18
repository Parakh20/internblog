"""Encrypt/decrypt secrets (Google Calendar refresh tokens) at rest, using
a Fernet key from settings. Refresh tokens are long-lived credentials for a
user's personal calendar - storing them in plaintext would be a real
exposure if the database ever leaked."""

from cryptography.fernet import Fernet

from app.config import settings


def _fernet() -> Fernet:
    return Fernet(settings.secret_encryption_key.encode())


def encrypt_token(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_token(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()
