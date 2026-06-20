from pydantic import BaseModel


class AccountResponse(BaseModel):
    account_id: str
    name: str
    locale: str
    scan_library: bool
    authenticated: bool
    owner_name: str | None = None
    owner_username: str | None = None
    auto_download: bool = False
    added_by_user_id: int | None = None


class StartLoginRequest(BaseModel):
    email: str
    locale: str


class StartLoginResponse(BaseModel):
    session_id: str
    login_url: str


class CompleteLoginRequest(BaseModel):
    session_id: str
    response_url: str


class MessageResponse(BaseModel):
    message: str
