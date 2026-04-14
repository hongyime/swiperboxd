import base64
import hashlib
from cryptography.fernet import Fernet


def _derive_fernet_key(master_key: str) -> bytes:
    digest = hashlib.sha256(master_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def encrypt_session_cookie(session_cookie: str, master_key: str) -> str:
    fernet = Fernet(_derive_fernet_key(master_key))
    return fernet.encrypt(session_cookie.encode("utf-8")).decode("utf-8")


def decrypt_session_cookie(token: str, master_key: str) -> str:
    fernet = Fernet(_derive_fernet_key(master_key))
    return fernet.decrypt(token.encode("utf-8")).decode("utf-8")
