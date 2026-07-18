import pytest

from app import crypto
from app.config import settings


@pytest.fixture(autouse=True)
def _fernet_key(monkeypatch):
    from cryptography.fernet import Fernet

    monkeypatch.setattr(settings, "secret_encryption_key", Fernet.generate_key().decode())


def test_encrypt_then_decrypt_round_trips():
    ciphertext = crypto.encrypt_token("my-refresh-token")
    assert crypto.decrypt_token(ciphertext) == "my-refresh-token"


def test_ciphertext_does_not_contain_plaintext():
    ciphertext = crypto.encrypt_token("my-refresh-token")
    assert "my-refresh-token" not in ciphertext


def test_decrypt_with_wrong_key_raises(monkeypatch):
    from cryptography.fernet import Fernet, InvalidToken

    ciphertext = crypto.encrypt_token("my-refresh-token")
    monkeypatch.setattr(settings, "secret_encryption_key", Fernet.generate_key().decode())
    with pytest.raises(InvalidToken):
        crypto.decrypt_token(ciphertext)
