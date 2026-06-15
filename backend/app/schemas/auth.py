from pydantic import BaseModel
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

    model_config = {"from_attributes": True}


class SetupTwoFactorResponse(BaseModel):
    secret: str
    qr_uri: str
    qr_image: str


class MessageResponse(BaseModel):
    message: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str
