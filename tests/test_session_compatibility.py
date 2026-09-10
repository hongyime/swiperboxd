"""Read synthetic tokens produced before the cryptography 50 dependency update."""

import base64
import json
from pathlib import Path

import pytest
from cryptography.fernet import InvalidToken

from src.api.security import decrypt_session_cookie


CASES = json.loads((Path(__file__).parent / 'fixtures/session-cryptography-48.json')
                   .read_text(encoding='utf-8'))['cases']


@pytest.mark.parametrize('case', CASES, ids=lambda case: case['name'])
def test_existing_session_token_remains_readable(case):
    assert decrypt_session_cookie(case['token'], case['master_key']) == case['plaintext']


@pytest.mark.parametrize('case', CASES, ids=lambda case: case['name'])
def test_existing_session_rejects_wrong_key(case):
    with pytest.raises(InvalidToken):
        decrypt_session_cookie(case['token'], case['master_key'] + '-wrong')


@pytest.mark.parametrize('case', CASES, ids=lambda case: case['name'])
def test_existing_session_rejects_tampering(case):
    raw = bytearray(base64.urlsafe_b64decode(case['token']))
    raw[-1] ^= 1
    tampered = base64.urlsafe_b64encode(raw).decode('ascii')
    with pytest.raises(InvalidToken):
        decrypt_session_cookie(tampered, case['master_key'])
