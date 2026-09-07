from typing import Optional

from pydantic import BaseModel, Field

from .settings import LibationSettings


class BackupChaptarr(BaseModel):
    """Chaptarr settings as they travel. Every field optional — an older backup
    is missing whatever did not exist yet, and an absent value means "keep"."""

    enabled: Optional[bool] = None
    url: Optional[str] = None
    import_mode: Optional[str] = None
    auto_import: Optional[bool] = None
    path_from: Optional[str] = None
    path_to: Optional[str] = None
    skip_existing: Optional[bool] = None
    skip_when: Optional[str] = None
    #: Absent in a sanitised backup; the stored key then survives the restore.
    api_key: Optional[str] = None


class BackupOidc(BaseModel):
    enabled: Optional[bool] = None
    issuer: Optional[str] = None
    client_id: Optional[str] = None
    #: Plain text, both ways. It is stored encrypted under a key that never
    #: leaves the machine, so ciphertext in a backup would restore to nothing.
    client_secret: Optional[str] = None
    redirect_url: Optional[str] = None
    scopes: Optional[str] = None
    provider_name: Optional[str] = None
    username_claim: Optional[str] = None
    email_claim: Optional[str] = None
    groups_claim: Optional[str] = None
    admin_group: Optional[str] = None
    auto_create_users: Optional[bool] = None


class SettingsBackup(BaseModel):
    """The document. Validated before anything is written."""

    # Optional at the schema level on purpose: a file missing them is not ours,
    # and `backup.validate_document` says so in words. Making them required here
    # would answer with a pydantic field error instead.
    format: Optional[str] = None
    version: Optional[int] = None
    exported_at: Optional[str] = None
    app_version: Optional[str] = None
    includes_secrets: bool = False
    secrets_omitted: list[str] = Field(default_factory=list)
    oidc_env_locked: list[str] = Field(default_factory=list)
    chaptarr: Optional[BackupChaptarr] = None
    oidc: Optional[BackupOidc] = None
    libation: Optional[LibationSettings] = None


class RestoreRequest(BaseModel):
    backup: SettingsBackup
    #: Which sections to apply. Omitted means every section the file carries,
    #: so restoring only Chaptarr from a full backup is one field away.
    sections: Optional[list[str]] = None


class RestoreReportResponse(BaseModel):
    applied: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    env_locked: list[str] = Field(default_factory=list)
    secrets_missing: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
