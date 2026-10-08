"""Request and response models for the API."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

# Palworld lock codes are short numeric strings. Validated here so a bad value
# never reaches the save.
LOCK_CODE_MAX = 8
NICKNAME_MAX = 60
NOTES_MAX = 2000


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class LoginResponse(BaseModel):
    role: str
    expires_at: int


class MetaUpdate(BaseModel):
    """Nickname and notes. Stored outside the save file and never overwritten."""

    nickname: str | None = Field(default=None, max_length=NICKNAME_MAX)
    notes: str | None = Field(default=None, max_length=NOTES_MAX)

    @field_validator("nickname", "notes")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class EditRequest(BaseModel):
    """One queued slot change.

    ``item_id = null`` clears the slot. A stack_count of 0 with an item_id set
    is rejected rather than silently treated as a clear, because the two are
    different intentions and the user should see which one they asked for.
    """

    slot_index: int = Field(ge=0, le=255)
    item_id: str | None = Field(default=None, max_length=120)
    stack_count: int = Field(default=0, ge=0, le=99_999)

    @field_validator("item_id")
    @classmethod
    def _clean_item(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class EditResponse(BaseModel):
    id: int
    container_guid: str
    slot_index: int
    item_id: str | None
    stack_count: int
    requested_by: str
    status: str
    requested_at: str
    applied_at: str | None = None
    error: str | None = None
    expected_window: str | None = None


class StatusResponse(BaseModel):
    server_running: bool | None
    last_ingest: dict | None
    last_good_ingest: dict | None
    next_window: str | None
    queue: dict[str, int]
    lock_codes_available: bool
    stale: bool
    window_in_progress: bool
    # What the previous window did, plus why the next one would refuse.
    last_window: dict | None = None
    save_backend: str
    backend_available: bool
    warnings: list[str] = []
