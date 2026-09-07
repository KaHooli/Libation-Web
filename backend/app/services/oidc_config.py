"""Where the OIDC settings come from, and what may switch password login off.

SSO used to be environment-only. It is now editable in Settings, which changes
one thing that matters a great deal: for the first time an administrator can
turn password sign-in off *from inside the app*, using a configuration stored in
a database they would then be unable to reach. Two rules keep that from becoming
a lockout.

**Environment wins, per field.** Anything set in the environment stays
authoritative and is shown read-only in the UI, so a deployment that pins its
config as code keeps it, and `ALLOW_PASSWORD_LOGIN=true` remains the documented
way back in. `settings.model_fields_set` is what distinguishes a value someone
supplied from a default nobody chose.

**Password login only switches off after SSO has actually worked.** Not when it
is configured, not when a connection test passes — a test proves the provider is
reachable, which a wrong client secret or a mismatched redirect URL both survive.
It switches off once some user has genuinely signed in through the provider,
which is a fact recorded in `users.oidc_subject` and cannot be faked by a
half-finished config.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import settings
from ..models.user import User
from .potation import creds

#: `system_settings` keys. The client secret is stored under a separate name so
#: nothing can mistake the ciphertext column for a readable one.
SETTING_KEYS = (
    "oidc_enabled",
    "oidc_issuer",
    "oidc_client_id",
    "oidc_client_secret_enc",
    "oidc_redirect_url",
    "oidc_scopes",
    "oidc_provider_name",
    "oidc_username_claim",
    "oidc_email_claim",
    "oidc_groups_claim",
    "oidc_admin_group",
    "oidc_auto_create_users",
)

#: Setting key → the environment variable that overrides it, and the field name
#: exposed on OidcConfig.
_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("oidc_enabled", "OIDC_ENABLED", "enabled"),
    ("oidc_issuer", "OIDC_ISSUER", "issuer"),
    ("oidc_client_id", "OIDC_CLIENT_ID", "client_id"),
    ("oidc_client_secret_enc", "OIDC_CLIENT_SECRET", "client_secret"),
    ("oidc_redirect_url", "OIDC_REDIRECT_URL", "redirect_url"),
    ("oidc_scopes", "OIDC_SCOPES", "scopes"),
    ("oidc_provider_name", "OIDC_PROVIDER_NAME", "provider_name"),
    ("oidc_username_claim", "OIDC_USERNAME_CLAIM", "username_claim"),
    ("oidc_email_claim", "OIDC_EMAIL_CLAIM", "email_claim"),
    ("oidc_groups_claim", "OIDC_GROUPS_CLAIM", "groups_claim"),
    ("oidc_admin_group", "OIDC_ADMIN_GROUP", "admin_group"),
    ("oidc_auto_create_users", "OIDC_AUTO_CREATE_USERS", "auto_create_users"),
)

#: The configurable field names on OidcConfig. `env_locked` is deliberately not
#: one of them: it describes the machine, not the configuration.
FIELD_NAMES: tuple[str, ...] = tuple(name for _key, _env, name in _FIELDS)

_BOOL_FIELDS = frozenset({"enabled", "auto_create_users"})

#: Fields whose value must never leave the server.
SECRET_FIELDS = frozenset({"client_secret"})


@dataclass(frozen=True)
class OidcConfig:
    enabled: bool = False
    issuer: str = ""
    client_id: str = ""
    client_secret: str = ""
    redirect_url: str = ""
    scopes: str = "openid profile email"
    provider_name: str = "SSO"
    username_claim: str = "preferred_username"
    email_claim: str = "email"
    groups_claim: str = "groups"
    admin_group: str = ""
    auto_create_users: bool = True
    #: Field names pinned by the environment. The UI renders these read-only
    #: rather than silently discarding an edit that would never take effect.
    env_locked: frozenset = field(default_factory=frozenset)

    @property
    def normalized_issuer(self) -> str:
        return self.issuer.strip().rstrip("/")

    @property
    def configured(self) -> bool:
        """Whether SSO could actually complete a login.

        Deliberately stricter than `enabled`: a half-filled configuration must
        not be able to affect password sign-in.
        """
        return bool(
            self.enabled
            and self.normalized_issuer
            and self.client_id.strip()
            and self.client_secret.strip()
        )


def _env_set(var: str) -> bool:
    return var in settings.model_fields_set


def _as_bool(raw: str) -> bool:
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _stored(db: Session) -> dict[str, str]:
    rows = db.connection().execute(
        text("SELECT key, value FROM system_settings WHERE key LIKE 'oidc\\_%' ESCAPE '\\'")
    ).fetchall()
    return {r[0]: (r[1] or "") for r in rows}


def load_config(db: Session) -> OidcConfig:
    """Resolve the effective configuration: environment over database."""
    raw = _stored(db)
    values: dict = {}
    locked: set[str] = set()

    for key, env_var, name in _FIELDS:
        if _env_set(env_var):
            values[name] = getattr(settings, env_var)
            locked.add(name)
            continue

        stored = raw.get(key, "")
        if name in _BOOL_FIELDS:
            # Absent means "never configured", which for auto_create_users is
            # not the same as "off" — fall back to the dataclass default.
            values[name] = _as_bool(stored) if stored != "" else None
        elif name == "client_secret":
            # A key that will not decrypt must not take SSO down; treat it as
            # unconfigured, which fails toward password login staying on.
            values[name] = creds.try_decrypt(stored) or "" if stored else ""
        else:
            values[name] = stored

    defaults = OidcConfig()
    clean = {
        name: (getattr(defaults, name) if value is None or value == "" else value)
        for name, value in values.items()
    }
    # An explicit false must survive the "empty means default" collapse above.
    for name in _BOOL_FIELDS:
        if values.get(name) is not None:
            clean[name] = bool(values[name])

    return OidcConfig(**clean, env_locked=frozenset(locked))


def save_config(db: Session, patch: dict) -> OidcConfig:
    """Persist the supplied fields. Environment-pinned fields are ignored.

    An omitted `client_secret` keeps whatever is stored; an empty string clears
    it. Anything else would make it impossible to edit the issuer without
    retyping the secret, and the UI never receives the secret to send back.
    """
    by_name = {name: key for key, _env, name in _FIELDS}
    locked = load_config(db).env_locked
    conn = db.connection()

    for name, value in patch.items():
        key = by_name.get(name)
        if key is None or value is None or name in locked:
            continue
        if name == "client_secret":
            stored = creds.encrypt(value) if value else ""
        elif name in _BOOL_FIELDS:
            stored = "1" if value else "0"
        else:
            stored = str(value).strip()
        conn.execute(
            text(
                "INSERT INTO system_settings (key, value) VALUES (:k, :v) "
                "ON CONFLICT(key) DO UPDATE SET value = :v"
            ),
            {"k": key, "v": stored},
        )
    db.commit()
    return load_config(db)


# ── What may switch password login off ───────────────────────────────────────

def sso_has_worked(db: Session, cfg: OidcConfig) -> bool:
    """Whether any user has actually signed in through the configured provider.

    Scoped by issuer, so pointing the app at a different provider does not
    inherit the previous one's proof. If the stored issuer string and the
    configured one disagree this reports False, which keeps password login on —
    the safe direction.
    """
    issuer = cfg.normalized_issuer
    if not issuer:
        return False
    linked = db.query(User.oidc_issuer).filter(User.oidc_subject.isnot(None)).all()
    return any((row[0] or "").strip().rstrip("/") == issuer for row in linked)


def password_login_enabled(db: Session) -> bool:
    """Whether username/password sign-in is accepted right now.

    `ALLOW_PASSWORD_LOGIN` wins outright in both directions — it is the
    documented escape hatch, and the one setting that must not depend on the
    provider being reachable.
    """
    if settings.ALLOW_PASSWORD_LOGIN is not None:
        return settings.ALLOW_PASSWORD_LOGIN
    cfg = load_config(db)
    if not cfg.configured:
        return True
    return not sso_has_worked(db, cfg)


def public_config(db: Session) -> dict:
    """What the sign-in page may know before anyone has credentials.

    Never the issuer, client id or secret: this endpoint is unauthenticated.
    """
    cfg = load_config(db)
    return {
        "password_login_enabled": password_login_enabled(db),
        "oidc_enabled": cfg.configured,
        "oidc_provider_name": cfg.provider_name,
    }
