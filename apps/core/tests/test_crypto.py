import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from apps.core.crypto import TokenDecryptionError, decrypt, encrypt, generate_key


def test_round_trip_returns_original_plaintext():
    token = "IGQVJXsome-long-instagram-access-token"
    assert decrypt(encrypt(token)) == token


def test_ciphertext_does_not_contain_plaintext():
    token = "IGQVJXsome-long-instagram-access-token"
    assert token not in encrypt(token)


def test_two_encryptions_differ_but_both_decrypt():
    token = "IGQVJXsome-long-instagram-access-token"
    a, b = encrypt(token), encrypt(token)
    assert a != b
    assert decrypt(a) == decrypt(b) == token


def test_decrypting_garbage_raises():
    with pytest.raises(TokenDecryptionError):
        decrypt("not-real-ciphertext")


def test_decrypting_empty_raises():
    with pytest.raises(TokenDecryptionError):
        decrypt("")


def test_key_from_another_install_cannot_decrypt():
    ciphertext = encrypt("secret")
    with override_settings(TOKEN_ENCRYPTION_KEY=generate_key()):
        with pytest.raises(TokenDecryptionError):
            decrypt(ciphertext)


def test_missing_key_is_a_configuration_error():
    with override_settings(TOKEN_ENCRYPTION_KEY=""):
        with pytest.raises(ImproperlyConfigured):
            encrypt("secret")
