import secrets

import bcrypt


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def generate_unusable_password_hash() -> str:
    """Create a random password hash for records created without a user-supplied password."""
    return hash_password(secrets.token_urlsafe(24))
