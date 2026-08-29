"""Authentication and user schemas."""

from __future__ import annotations

import re
from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.common import ORMModel

USERNAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-.]{2,39}$")


class RegisterRequest(BaseModel):
    email: EmailStr
    username: str = Field(min_length=3, max_length=40)
    password: str = Field(min_length=10, max_length=200)
    full_name: str | None = Field(default=None, max_length=200)

    @field_validator("username")
    @classmethod
    def _valid_username(cls, value: str) -> str:
        if not USERNAME_RE.match(value):
            raise ValueError(
                "username must start with a letter or digit and contain only letters, "
                "digits, '_', '-' or '.'"
            )
        return value

    @field_validator("password")
    @classmethod
    def _strong_password(cls, value: str) -> str:
        checks = (
            (any(c.islower() for c in value), "a lowercase letter"),
            (any(c.isupper() for c in value), "an uppercase letter"),
            (any(c.isdigit() for c in value), "a digit"),
        )
        missing = [label for ok, label in checks if not ok]
        if missing:
            raise ValueError("password must contain " + ", ".join(missing))
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


class RefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(ORMModel):
    id: str
    email: str
    username: str
    full_name: str | None = None
    github_login: str | None = None
    avatar_url: str | None = None
    is_active: bool
    is_superuser: bool
    created_at: datetime


class InstallationResponse(ORMModel):
    id: str
    installation_id: int
    account_login: str
    account_type: str
    avatar_url: str | None = None
    is_active: bool
    suspended: bool
    repository_count: int = 0
    created_at: datetime


class AuthContext(BaseModel):
    user: UserResponse
    installations: list[InstallationResponse]
    demo_mode: bool
