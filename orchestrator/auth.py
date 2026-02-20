"""Authentication and authorization utilities for the orchestrator."""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from .models import (
    AuthConfig,
    LoginRequest,
    LoginResponse,
    Session,
    SessionResponse,
    User,
    UserRole,
)

logger = logging.getLogger(__name__)

security = HTTPBearer(auto_error=False)


class AuthManager:
    """Manages user authentication and sessions."""

    SESSION_TIMEOUT_HOURS = 24
    SUDO_TIMEOUT_MINUTES = 10

    def __init__(self, state: dict[str, Any]):
        self.state = state
        self._init_auth_section()

    def _init_auth_section(self) -> None:
        """Initialize auth section in state if not present."""
        if "auth" not in self.state:
            self.state["auth"] = {
                "version": 1,
                "users": [],
                "sessions": [],
                "settings": {
                    "session_timeout_hours": self.SESSION_TIMEOUT_HOURS,
                    "sudo_timeout_minutes": self.SUDO_TIMEOUT_MINUTES,
                },
            }

    # scrypt parameters (memory-hard KDF, resistant to GPU/ASIC attacks)
    _SCRYPT_N = 16384  # CPU/memory cost (2^14)
    _SCRYPT_R = 8  # block size
    _SCRYPT_P = 1  # parallelization
    _SCRYPT_DKLEN = 64  # derived key length in bytes

    def _hash_password(self, password: str) -> str:
        """Hash a password using scrypt (memory-hard KDF).

        Format: ``scrypt${salt_hex}${hash_hex}``

        Uses hashlib.scrypt from the Python stdlib — no extra dependencies.
        """
        salt = secrets.token_bytes(16)
        dk = hashlib.scrypt(
            password.encode(),
            salt=salt,
            n=self._SCRYPT_N,
            r=self._SCRYPT_R,
            p=self._SCRYPT_P,
            dklen=self._SCRYPT_DKLEN,
        )
        return f"scrypt${salt.hex()}${dk.hex()}"

    def _verify_password(self, password: str, hashed: str) -> bool:
        """Verify a password against a hash.

        Supports both the new scrypt format (``scrypt$salt$hash``) and
        the legacy SHA-256 format (``salt$hash``) for backward compatibility.
        """
        try:
            if hashed.startswith("scrypt$"):
                # New format: scrypt$salt_hex$hash_hex
                _, salt_hex, stored_hex = hashed.split("$")
                salt = bytes.fromhex(salt_hex)
                dk = hashlib.scrypt(
                    password.encode(),
                    salt=salt,
                    n=self._SCRYPT_N,
                    r=self._SCRYPT_R,
                    p=self._SCRYPT_P,
                    dklen=self._SCRYPT_DKLEN,
                )
                return secrets.compare_digest(dk.hex(), stored_hex)
            else:
                # Legacy format: salt_hex$sha256_hex
                salt, stored_hash = hashed.split("$")
                pwdhash = hashlib.sha256((password + salt).encode()).hexdigest()
                return secrets.compare_digest(pwdhash, stored_hash)
        except (ValueError, KeyError):
            return False

    def _needs_rehash(self, hashed: str) -> bool:
        """Check if a hash uses the legacy format and should be upgraded."""
        return not hashed.startswith("scrypt$")

    def _generate_token(self) -> str:
        """Generate a secure random session token."""
        return secrets.token_urlsafe(32)

    def _get_now(self) -> datetime:
        """Get current UTC time."""
        return datetime.now(timezone.utc)

    def create_user(
        self, username: str, password: str, role: UserRole = UserRole.ADMIN
    ) -> User:
        """Create a new user with hashed password."""
        users = self.state["auth"]["users"]

        # Check if user exists
        for user in users:
            if user["username"] == username:
                raise ValueError(f"User {username} already exists")

        user_data = {
            "username": username,
            "password_hash": self._hash_password(password),
            "role": role.value,
            "created_at": self._get_now().isoformat(),
        }
        users.append(user_data)

        return User.model_validate(user_data)

    def authenticate(self, username: str, password: str) -> Optional[Session]:
        """Authenticate a user and create a session.

        On successful login with a legacy SHA-256 hash, the hash is
        transparently upgraded to scrypt (progressive rehashing).
        """
        users = self.state["auth"]["users"]

        for user_data in users:
            if user_data["username"] == username:
                if self._verify_password(password, user_data["password_hash"]):
                    # Progressive rehash: upgrade legacy hashes on login
                    if self._needs_rehash(user_data["password_hash"]):
                        user_data["password_hash"] = self._hash_password(password)
                        logger.info(
                            "Upgraded password hash for user %s to scrypt", username
                        )

                    # Create session
                    session_data = {
                        "token": self._generate_token(),
                        "username": username,
                        "role": user_data["role"],
                        "created_at": self._get_now().isoformat(),
                        "expires_at": (
                            self._get_now()
                            + timedelta(hours=self.SESSION_TIMEOUT_HOURS)
                        ).isoformat(),
                        "sudo_expires_at": None,
                    }
                    self.state["auth"]["sessions"].append(session_data)
                    return Session.model_validate(session_data)
                else:
                    return None

        return None

    def validate_session(self, token: str) -> Optional[Session]:
        """Validate a session token."""
        sessions = self.state["auth"]["sessions"]
        now = self._get_now()

        for session_data in sessions:
            if session_data["token"] == token:
                expires_at = datetime.fromisoformat(session_data["expires_at"])
                if expires_at > now:
                    return Session.model_validate(session_data)
                else:
                    # Clean up expired session
                    sessions.remove(session_data)
                    return None

        return None

    def revoke_session(self, token: str) -> bool:
        """Revoke a session token."""
        sessions = self.state["auth"]["sessions"]
        for session_data in sessions:
            if session_data["token"] == token:
                sessions.remove(session_data)
                return True
        return False

    def verify_sudo(self, token: str, password: str) -> bool:
        """Verify password for sudo mode and extend session."""
        session = self.validate_session(token)
        if not session:
            return False

        # Re-authenticate with password
        users = self.state["auth"]["users"]
        for user_data in users:
            if user_data["username"] == session.username:
                if self._verify_password(password, user_data["password_hash"]):
                    # Extend sudo mode
                    now = self._get_now()
                    sudo_expires = now + timedelta(minutes=self.SUDO_TIMEOUT_MINUTES)

                    # Update session
                    sessions = self.state["auth"]["sessions"]
                    for s in sessions:
                        if s["token"] == token:
                            s["sudo_expires_at"] = sudo_expires.isoformat()
                            return True

        return False

    def check_sudo(self, token: str) -> bool:
        """Check if session has active sudo mode."""
        session = self.validate_session(token)
        if not session:
            return False

        if session.sudo_expires_at is None:
            return False

        now = self._get_now()
        return session.sudo_expires_at > now

    def get_user(self, username: str) -> Optional[User]:
        """Get a user by username."""
        users = self.state["auth"]["users"]
        for user_data in users:
            if user_data["username"] == username:
                return User.model_validate(user_data)
        return None

    def list_users(self) -> list[User]:
        """List all users."""
        users = self.state["auth"]["users"]
        return [User.model_validate(u) for u in users]

    def delete_user(self, username: str) -> bool:
        """Delete a user."""
        users = self.state["auth"]["users"]
        for user_data in users:
            if user_data["username"] == username:
                users.remove(user_data)
                # Also revoke all sessions for this user
                sessions = self.state["auth"]["sessions"]
                self.state["auth"]["sessions"] = [
                    s for s in sessions if s["username"] != username
                ]
                return True
        return False

    def change_password(self, username: str, new_password: str) -> bool:
        """Change a user's password."""
        users = self.state["auth"]["users"]
        for user_data in users:
            if user_data["username"] == username:
                user_data["password_hash"] = self._hash_password(new_password)
                # Revoke all sessions for this user (force re-login)
                sessions = self.state["auth"]["sessions"]
                self.state["auth"]["sessions"] = [
                    s for s in sessions if s["username"] != username
                ]
                return True
        return False

    def has_users(self) -> bool:
        """Check if any users exist."""
        return len(self.state["auth"]["users"]) > 0

    def cleanup_expired_sessions(self) -> int:
        """Remove expired sessions and return count removed."""
        sessions = self.state["auth"]["sessions"]
        now = self._get_now()
        original_count = len(sessions)

        self.state["auth"]["sessions"] = [
            s for s in sessions if datetime.fromisoformat(s["expires_at"]) > now
        ]

        return original_count - len(self.state["auth"]["sessions"])


# FastAPI dependencies


async def get_auth_manager(request: Request) -> AuthManager:
    """Dependency to get AuthManager from request state."""
    return request.state.auth_manager


async def require_auth(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    auth_manager: AuthManager = Depends(get_auth_manager),
) -> Session:
    """Dependency to require authentication."""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    session = auth_manager.validate_session(token)

    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return session


async def require_admin(
    session: Session = Depends(require_auth),
) -> Session:
    """Dependency to require admin role."""
    if session.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return session


async def require_sudo(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    auth_manager: AuthManager = Depends(get_auth_manager),
) -> Session:
    """Dependency to require sudo mode (recent password verification)."""
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    token = credentials.credentials
    session = auth_manager.validate_session(token)

    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session",
        )

    if not auth_manager.check_sudo(token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Sudo mode required. Please verify your password.",
        )

    return session
