"""Sign in and out."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Response, status

from .. import auth
from ..config import Config
from ..models import LoginRequest, LoginResponse
from .deps import get_config, get_conn, get_session

router = APIRouter(tags=["session"])


@router.post("/session", response_model=LoginResponse)
def login(
    body: LoginRequest,
    response: Response,
    config: Config = Depends(get_config),
    conn: sqlite3.Connection = Depends(get_conn),
) -> LoginResponse:
    role = auth.identify(config.auth, body.password)
    if role is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="wrong password"
        )
    key = auth.signing_key(conn)
    token = auth.issue(key, role, days=config.auth.session_days)
    session = auth.read(key, token)
    assert session is not None
    response.set_cookie(
        auth.SESSION_COOKIE,
        token,
        max_age=config.auth.session_days * 86400,
        httponly=True,
        samesite="lax",
        # Not forced on: the deployment is reachable over plain HTTP on a VPN
        # address, and a Secure cookie there would never be sent.
        secure=False,
    )
    return LoginResponse(role=role, expires_at=session.expires_at)


@router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response) -> None:
    response.delete_cookie(auth.SESSION_COOKIE)


@router.get("/session", response_model=LoginResponse)
def whoami(session: auth.Session = Depends(get_session)) -> LoginResponse:
    return LoginResponse(role=session.role, expires_at=session.expires_at)
