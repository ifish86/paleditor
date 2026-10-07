"""Shared FastAPI dependencies: the config, a connection, and the session."""

from __future__ import annotations

import sqlite3
from typing import Iterator

from fastapi import Cookie, Depends, HTTPException, Request, status

from .. import auth, db
from ..config import Config


def get_config(request: Request) -> Config:
    return request.app.state.config


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    """A short-lived connection per request.

    SQLite in WAL mode handles this well, and it keeps a slow request from
    pinning a connection the window worker wants.
    """
    conn = db.connect(request.app.state.config.database.path)
    try:
        yield conn
    finally:
        conn.close()


def get_session(
    request: Request,
    conn: sqlite3.Connection = Depends(get_conn),
    paleditor_session: str | None = Cookie(default=None),
) -> auth.Session:
    key = auth.signing_key(conn)
    session = auth.read(key, paleditor_session)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="not signed in",
            headers={"WWW-Authenticate": "Cookie"},
        )
    return session


def require_owner(session: auth.Session = Depends(get_session)) -> auth.Session:
    """Gate for the destructive endpoints.

    Covers POST /api/maintenance/run and cancelling another user's edit.
    """
    if not session.is_owner:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="this action needs the owner password",
        )
    return session
