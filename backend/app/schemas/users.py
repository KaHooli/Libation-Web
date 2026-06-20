from pydantic import BaseModel
from typing import Optional
from datetime import datetime


class CreateUserRequest(BaseModel):
    username: str
    password: str
    is_admin: bool = False


class UpdateUserRequest(BaseModel):
    is_active: Optional[bool] = None
    is_admin: Optional[bool] = None
    new_password: Optional[str] = None
    owner_name: Optional[str] = None
    audible_account_id: Optional[str] = None


class PermissionsUpdate(BaseModel):
    can_download: Optional[bool] = None
    can_scan: Optional[bool] = None
    can_manage_accounts: Optional[bool] = None
    can_liberate: Optional[bool] = None
    can_remove_downloads: Optional[bool] = None
    download_cap: Optional[int] = None


class UserAdminResponse(BaseModel):
    id: int
    username: str
    is_active: bool
    is_admin: bool
    totp_enabled: bool
    permissions: Optional[dict] = None
    download_cap: Optional[int] = None
    audible_account_id: Optional[str] = None
    owner_name: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class SessionResponse(BaseModel):
    id: int
    created_at: datetime
    last_used_at: datetime
    expires_at: datetime
    user_agent: Optional[str] = None
    ip_address: Optional[str] = None

    model_config = {"from_attributes": True}
