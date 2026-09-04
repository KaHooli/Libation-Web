from typing import Optional

from pydantic_settings import BaseSettings
import secrets

#: SQLite on the /data volume. PostgreSQL is opt-in by setting DATABASE_URL.
DEFAULT_DATABASE_URL = "sqlite:////data/app.db"


class Settings(BaseSettings):
    SECRET_KEY: str = secrets.token_hex(32)
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 60
    TEMP_TOKEN_EXPIRE_MINUTES: int = 5

    DATABASE_URL: str = DEFAULT_DATABASE_URL

    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "admin"

    LIBATION_CLI: str = "/usr/bin/libationcli"
    LIBATION_CONFIG: str = "/config"
    AUDIOBOOKS_DIR: str = "/audiobooks"

    BRIDGE_URL: str = "http://localhost:8001"

    # ── OIDC single sign-on ───────────────────────────────────────────────
    OIDC_ENABLED: bool = False
    #: Issuer base URL. Discovery is read from {issuer}/.well-known/openid-configuration.
    OIDC_ISSUER: str = ""
    OIDC_CLIENT_ID: str = ""
    OIDC_CLIENT_SECRET: str = ""
    #: Absolute callback URL registered with the provider. Derived from the
    #: incoming request when blank, which is right for a single-origin deploy
    #: but wrong behind a proxy that rewrites the host.
    OIDC_REDIRECT_URL: str = ""
    OIDC_SCOPES: str = "openid profile email"
    #: Label on the sign-in button.
    OIDC_PROVIDER_NAME: str = "SSO"

    OIDC_USERNAME_CLAIM: str = "preferred_username"
    OIDC_EMAIL_CLAIM: str = "email"
    OIDC_GROUPS_CLAIM: str = "groups"
    #: Members of this group are made admins on each login. Blank disables the
    #: mapping and leaves admin rights managed in the app.
    OIDC_ADMIN_GROUP: str = ""
    #: Create a local user the first time someone signs in through the provider.
    #: With this off, only users who already exist can use SSO.
    OIDC_AUTO_CREATE_USERS: bool = True

    #: Username/password sign-in. Left unset it follows OIDC: on until SSO is
    #: working, off once it is. Set it explicitly to force either way — the
    #: escape hatch for a misconfigured provider locking everyone out.
    ALLOW_PASSWORD_LOGIN: Optional[bool] = None

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

    @property
    def oidc_configured(self) -> bool:
        """True only when SSO could actually complete a login.

        Deliberately stricter than `OIDC_ENABLED`: a half-filled configuration
        must not be able to switch password login off, or a typo in the issuer
        locks every user out.
        """
        return bool(
            self.OIDC_ENABLED
            and self.OIDC_ISSUER.strip()
            and self.OIDC_CLIENT_ID.strip()
            and self.OIDC_CLIENT_SECRET.strip()
        )

    @property
    def password_login_enabled(self) -> bool:
        if self.ALLOW_PASSWORD_LOGIN is not None:
            return self.ALLOW_PASSWORD_LOGIN
        return not self.oidc_configured


settings = Settings()
