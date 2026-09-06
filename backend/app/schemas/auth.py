from pydantic import BaseModel, Field, computed_field
from typing import Optional


class LoginRequest(BaseModel):
    username: str
    password: str


class TwoFactorRequest(BaseModel):
    temp_token: str
    code: str


class EnableTwoFactorRequest(BaseModel):
    code: str


class DisableTwoFactorRequest(BaseModel):
    code: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserResponse"


class TwoFactorRequiredResponse(BaseModel):
    requires_2fa: bool = True
    temp_token: str


class UserResponse(BaseModel):
    id: int
    username: str
    totp_enabled: bool
    is_admin: bool = False
    audible_account_id: str | None = None
    owner_name: str | None = None
    download_cap: int | None = None
    permissions: dict | None = None
    #: Read from the ORM object but never serialised — only the boolean below
    #: goes over the wire.
    oidc_subject: str | None = Field(default=None, exclude=True)

    model_config = {"from_attributes": True}

    @computed_field
    @property
    def is_sso_user(self) -> bool:
        """Whether this account signs in through the identity provider.

        The UI uses it to stop offering password and 2FA controls that SSO
        makes meaningless.
        """
        return bool(self.oidc_subject)


class SetupTwoFactorResponse(BaseModel):
    secret: str
    qr_uri: str
    qr_image: str


class MessageResponse(BaseModel):
    message: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class ChangeUsernameRequest(BaseModel):
    new_username: str
    current_password: str
