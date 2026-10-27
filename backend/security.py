from datetime import datetime, timedelta, timezone
import jwt
from pwdlib import PasswordHash
from backend.config import settings

# Initialize Argon2id for secure password hashing
password_hash = PasswordHash.recommended()

# Security settings (move the secret key to your .env file in production)
SECRET_KEY = "super-secret-dev-key"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60

def hash_password(password: str) -> str:
    """Hashes a password using Argon2id."""
    return password_hash.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifies a plain password against the stored Argon2id hash."""
    return password_hash.verify(plain_password, hashed_password)

def create_access_token(user_id: int, patient_id: int, token_version: int) -> str:
    """
    Generates a JWT containing the user_id, patient_id, and token_version.
    The database uses these IDs to enforce Row-Level Security.
    """
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = {
        "sub": str(user_id),
        "patient_id": patient_id,
        "token_version": token_version,
        "exp": expire
    }
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def decode_access_token(token: str) -> dict:
    """Decodes the JWT and verifies its signature and expiration."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise ValueError("Token has expired")
    except jwt.InvalidTokenError:
        raise ValueError("Invalid token")