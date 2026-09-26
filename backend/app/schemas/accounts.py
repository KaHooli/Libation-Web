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


class UnlinkedAccountResponse(BaseModel):
    """A stale reference to an Audible account that no longer exists.

    Produced only after the move onto the Potation engine, where re-authorising
    mints a fresh account id. Nothing errors on a stale one — the affected pages
    simply return no rows — so this is what makes the breakage visible.
    """
    account_id: str
    #: Usernames whose `audible_account_id` still points here. My Books shows
    #: them an empty library until it is repointed.
    users: list[str] = []
    #: Whether the legacy `audible_account_settings` row survives, which is
    #: where auto-download used to live.
    had_settings: bool = False


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
