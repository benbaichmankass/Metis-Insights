"""S-013 M3 PR #1 — POST /api/auth/login.

Issuance only. Enforcement on protected routes lands in M3 PR #2 when
``require_session`` flips from no-op to real header parsing.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr

from src.web.api import auth as auth_module

router = APIRouter(prefix="/api/auth", tags=["auth"])
logger = logging.getLogger(__name__)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = auth_module.TOKEN_TTL_SECONDS


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest) -> LoginResponse:
    # Read auth config first. Any missing env var → 500 with a generic
    # body so we don't leak which secret is unset.
    try:
        allowed = auth_module._allowed_email()
        expected_hash = auth_module._password_hash()
    except RuntimeError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "auth_unavailable"},
        )

    submitted_email = body.email.strip().lower()

    # Allowlist gate first — return 403 (not 401) so the operator can tell
    # an off-allowlist account from a typo'd password.
    if submitted_email != allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"error": "email_not_allowlisted"},
        )

    if not auth_module.verify_password(body.password, expected_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": "invalid_credentials"},
        )

    try:
        token = auth_module.issue_token(submitted_email)
    except RuntimeError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "auth_unavailable"},
        )

    return LoginResponse(access_token=token)
