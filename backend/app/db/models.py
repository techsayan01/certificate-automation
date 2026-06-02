"""
Pydantic models — what we read/write to MongoDB.

Wire convention:
  • All documents use `_id` as ObjectId. We expose `id: str` on the API.
  • Sensitive fields are stored encrypted (Fernet). The encrypted variant
    has the `_enc` suffix; plaintext is never persisted.
  • Created/updated timestamps are set server-side, not by the client.

These models are intentionally Mongo-shaped (not DTOs). API responses
re-serialise through the *Public variants to strip secrets.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from bson import ObjectId
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


# ── Helpers ───────────────────────────────────────────────────────────────────

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PyObjectId(str):
    """ObjectId that JSON-serialises to its hex string."""

    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v, info=None):
        if isinstance(v, ObjectId):
            return str(v)
        if isinstance(v, str) and ObjectId.is_valid(v):
            return v
        raise ValueError(f"Invalid ObjectId: {v!r}")


# ── Enums ─────────────────────────────────────────────────────────────────────

class UserRole(str, Enum):
    ADMIN          = "admin"
    FESTIVAL_USER  = "festival_user"


class FestivalStatus(str, Enum):
    ACTIVE   = "active"
    INACTIVE = "inactive"


class JudgingStatus(str, Enum):
    """Canonical statuses recognised by the pipeline.

    The CSV reader normalises FilmFreeway's free-text statuses into one
    of these values. Templates are matched on this exact string.
    """
    AWARD_WINNER       = "Award Winner"
    FINALIST           = "Finalist"
    SEMI_FINALIST      = "Semi-Finalist"
    QUARTER_FINALIST   = "Quarter-Finalist"
    OFFICIAL_SELECTION = "Official Selection"
    NOMINEE            = "Nominee"
    HONORABLE_MENTION  = "Honorable Mention"


class RunStatus(str, Enum):
    QUEUED  = "queued"
    RUNNING = "running"
    DONE    = "done"
    FAILED  = "failed"


# ── User ──────────────────────────────────────────────────────────────────────

class UserBase(BaseModel):
    email: EmailStr
    role:  UserRole
    festival_id: str | None = None     # required when role=festival_user

    @field_validator("festival_id")
    @classmethod
    def _festival_required_for_user_role(cls, v, info):
        if info.data.get("role") == UserRole.FESTIVAL_USER and not v:
            raise ValueError("festival_id is required for festival_user role")
        return v


class UserCreate(UserBase):
    password: str = Field(min_length=8)


class UserDoc(UserBase):
    """Shape stored in Mongo."""
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

    id: PyObjectId | None = Field(default=None, alias="_id")
    password_hash: str
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class UserPublic(UserBase):
    """Shape returned by the API — no password fields."""
    id: str
    created_at: datetime


# ── Festival ──────────────────────────────────────────────────────────────────

class GmailCredentials(BaseModel):
    client_id:         str = ""
    client_secret_enc: str = ""        # encrypted
    project_id:        str = ""
    sender_email:      str = ""        # e.g. globalvisionariesfilmfest@gmail.com
    refresh_token_enc: str = ""        # set after OAuth completes
    signature_html:    str = ""        # cached signature, re-fetched on schedule


class CanvaCredentials(BaseModel):
    """Optional — if a festival has its own Canva integration.
    Otherwise the global Canva app credentials are used and each festival
    just supplies its own brand template IDs.
    """
    client_id_enc:     str = ""
    client_secret_enc: str = ""
    refresh_token_enc: str = ""


class FestivalDefaults(BaseModel):
    email_subject:   str = "Congratulations — Your Certificate is Here!"
    email_from_name: str = "Festival Team"
    season:          str = ""        # e.g. "Season 5"
    season_date:     str = ""        # e.g. "Sep - Jan 2026"


class FestivalBase(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9_-]+$", min_length=2, max_length=64)
    name: str = Field(min_length=2, max_length=200)
    status: FestivalStatus = FestivalStatus.ACTIVE
    defaults: FestivalDefaults = Field(default_factory=FestivalDefaults)


class FestivalCreate(FestivalBase):
    """Plaintext Gmail fields on input — we encrypt before persisting."""
    gmail_client_id:     str = ""
    gmail_client_secret: str = ""
    gmail_project_id:    str = ""
    gmail_sender_email:  str = ""


class FestivalDoc(FestivalBase):
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

    id: PyObjectId | None = Field(default=None, alias="_id")
    gmail: GmailCredentials = Field(default_factory=GmailCredentials)
    canva: CanvaCredentials = Field(default_factory=CanvaCredentials)
    created_by: str | None = None     # user id
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class FestivalPublic(FestivalBase):
    """API response — strips encrypted secrets, keeps sender_email for display."""
    id: str
    gmail_sender_email: str = ""
    gmail_connected:    bool = False     # True if refresh_token is set
    canva_connected:    bool = False
    created_at: datetime


# ── Certificate template ──────────────────────────────────────────────────────

class CertTemplateBase(BaseModel):
    festival_id:    str
    category:       str = Field(min_length=1, max_length=200)
    judging_status: JudgingStatus

    canva_brand_template_id: str = Field(min_length=1)
    # Field names in Canva — defaults match a sensible naming convention
    canva_field_map: dict[str, str] = Field(
        default_factory=lambda: {
            "name":     "Name",
            "project":  "Project",
            "category": "Category",
            "season":   "Season",
            "season_date": "SeasonDate",
        }
    )

    email_template_html: str = ""    # Jinja2 template body — rendered with recipient context
    laurel_gcs_url:      str = ""    # public or signed URL


class CertTemplateCreate(CertTemplateBase):
    pass


class CertTemplateDoc(CertTemplateBase):
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)
    id: PyObjectId | None = Field(default=None, alias="_id")
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class CertTemplatePublic(CertTemplateBase):
    id: str
    created_at: datetime


# ── Run ───────────────────────────────────────────────────────────────────────

class RunTotals(BaseModel):
    total:   int = 0
    sent:    int = 0
    failed:  int = 0
    skipped: int = 0


class RunDoc(BaseModel):
    model_config = ConfigDict(populate_by_name=True, arbitrary_types_allowed=True)

    id: PyObjectId | None = Field(default=None, alias="_id")
    festival_id:   str
    triggered_by:  str           # user id
    csv_gcs_url:   str
    season:        str = ""
    season_date:   str = ""
    status:        RunStatus = RunStatus.QUEUED
    totals:        RunTotals = Field(default_factory=RunTotals)
    log:           list[dict] = Field(default_factory=list)
    started_at:    datetime | None = None
    finished_at:   datetime | None = None
    created_at:    datetime = Field(default_factory=utc_now)


class RunPublic(BaseModel):
    id: str
    festival_id:  str
    status:       RunStatus
    totals:       RunTotals
    season:       str
    season_date:  str
    started_at:   datetime | None
    finished_at:  datetime | None
    created_at:   datetime
