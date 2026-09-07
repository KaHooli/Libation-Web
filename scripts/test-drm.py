#!/usr/bin/env python3
"""The DRM delivery branch, voucher key handling, and licence reuse.

Phase B's first unit. Three things here are worth testing hard because they fail
*quietly* rather than loudly:

  * **The wrong-length key.** Handing ffmpeg a 4-byte activation blob where a
    16-byte AAXC key belongs does not error — it writes a file. A corrupt m4b
    that reaches Chaptarr and then Audiobookshelf is discovered by whoever tries
    to listen to it, which is the worst possible place to find out.
  * **The unknown DRM type.** Libation treats anything it does not recognise as
    unencrypted mp3 delivery. We refuse instead, and that divergence needs to
    stay deliberate rather than drift back.
  * **Licence reuse.** A `Download` licence counts against Audible's daily
    allowance. A retry that re-licenses spends quota to learn what is already
    known, and an expired CDN link that gets reused just 403s after the download
    has already started.

Key material is a secret, so there are also checks that it never reaches an
error message or the database in the clear.

Needs only `backend/requirements.txt` — no test framework.

Usage:
    PYTHONPATH=backend scripts/test-drm.py
"""
import os
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORKDIR = Path(tempfile.mkdtemp(prefix="drm-test-"))
CONFIG = WORKDIR / "config"
DATA = WORKDIR / "data"
BOOKS = WORKDIR / "audiobooks"
for d in (DATA, CONFIG, BOOKS):
    d.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("DATABASE_URL", f"sqlite:///{DATA / 'app.db'}")
os.environ.setdefault("LIBATION_CONFIG", str(CONFIG))
os.environ.setdefault("AUDIOBOOKS_DIR", str(BOOKS))
os.environ.setdefault("SECRET_KEY", "drm-test-only-not-a-real-secret")

PRODUCTION_PATHS = [Path("/data"), Path("/config"), Path("/audiobooks")]
PREEXISTING = {p for p in PRODUCTION_PATHS if p.exists()}

#: 16 bytes each, as AES-128 requires.
KEY = "0123456789abcdef0123456789abcdef"
IV = "fedcba9876543210fedcba9876543210"
#: 4 bytes, the legacy AAX activation blob.
ACTIVATION = "1a2b3c4d"


def assert_no_stray_dirs() -> None:
    created = sorted(str(p) for p in PRODUCTION_PATHS if p.exists() and p not in PREEXISTING)
    assert not created, (
        f"app created {created} instead of using its configured paths — "
        "this fails on an unprivileged host"
    )


# ── The four arms ────────────────────────────────────────────────────────────

def test_the_four_arms() -> None:
    from app.services.potation import drm

    aaxc = drm.plan_delivery("Adrm", key=KEY, iv=IV)
    assert aaxc.kind == drm.KIND_AAXC and aaxc.usable and aaxc.needs_decryption
    assert drm.ffmpeg_decrypt_args(aaxc) == ["-audible_key", KEY, "-audible_iv", IV]

    aax = drm.plan_delivery("Adrm", activation_bytes=ACTIVATION)
    assert aax.kind == drm.KIND_AAX and aax.needs_decryption
    assert drm.ffmpeg_decrypt_args(aax) == ["-activation_bytes", ACTIVATION]

    for drm_type in ("Mpeg", "", None):
        plain = drm.plan_delivery(drm_type)
        assert plain.kind == drm.KIND_UNENCRYPTED, (drm_type, plain)
        assert plain.usable and not plain.needs_decryption
        # The load-bearing empty list: passing -audible_key for a file with no
        # encryption is an ffmpeg error, not a harmless extra flag.
        assert drm.ffmpeg_decrypt_args(plain) == [], drm_type

    for scheme in ("Widevine", "PlayReady", "FairPlay"):
        blocked = drm.plan_delivery(scheme)
        assert blocked.kind == drm.KIND_UNSUPPORTED and not blocked.usable, scheme
        assert "decryption module" in blocked.reason, blocked.reason
    print("✓ all four delivery arms branch correctly and produce the right ffmpeg flags")


def test_unsupported_plans_refuse_to_produce_arguments() -> None:
    """An unusable plan must not silently yield an empty argument list.

    Returning `[]` would be indistinguishable from "unencrypted", and the
    download would proceed and write an encrypted file with an .m4b name.
    """
    from app.services.potation import drm

    for plan in (drm.plan_delivery("Widevine"), drm.plan_delivery("SomethingNew")):
        try:
            drm.ffmpeg_decrypt_args(plan)
            raise AssertionError(f"{plan.kind} plan produced ffmpeg arguments")
        except ValueError:
            pass
    print("✓ an unsupported plan raises rather than returning no flags")


def test_unknown_drm_is_refused_not_assumed_plain() -> None:
    """The deliberate divergence from Libation, pinned so it cannot drift back."""
    from app.services.potation import drm

    for unknown in ("Dash", "Hls", "HlsCmaf", "SomethingAudibleInventsIn2027"):
        plan = drm.plan_delivery(unknown)
        assert plan.kind == drm.KIND_UNSUPPORTED, (
            f"{unknown!r} was treated as {plan.kind}; guessing it is unencrypted "
            "writes a corrupt file instead of failing"
        )
        assert "Unrecognised DRM type" in plan.reason
    print("✓ an unrecognised DRM type is refused, not assumed to be unencrypted")


# ── The quiet failure: wrong-shaped key material ─────────────────────────────

def test_wrong_shaped_key_material_is_refused() -> None:
    from app.services.potation import drm

    cases = {
        "activation bytes in the key slot": dict(key=ACTIVATION, iv=ACTIVATION),
        "a key but no iv": dict(key=KEY),
        "an iv but no key": dict(iv=IV),
        "no key material at all": dict(),
        "a truncated key": dict(key=KEY[:30], iv=IV),
        "an over-long key": dict(key=KEY + "ab", iv=IV),
        "an odd number of hex digits": dict(key=KEY[:-1], iv=IV),
        "non-hex characters": dict(key="z" * 32, iv=IV),
        "activation bytes of the wrong length": dict(activation_bytes="1a2b3c"),
    }
    for label, kwargs in cases.items():
        plan = drm.plan_delivery("Adrm", **kwargs)
        assert plan.kind == drm.KIND_UNSUPPORTED, (
            f"{label}: produced a usable {plan.kind} plan — ffmpeg would write a "
            "file rather than reject this"
        )
        assert plan.reason, label
    print("✓ Adrm with key material of the wrong shape is refused, nine ways")


def test_key_material_never_appears_in_a_reason() -> None:
    """Reasons are shown to people and written to logs; keys are secrets."""
    from app.services.potation import drm

    secret_key = "deadbeef" * 8          # 32 bytes: wrong length, so it is rejected
    plan = drm.plan_delivery("Adrm", key=secret_key, iv=IV)
    assert plan.kind == drm.KIND_UNSUPPORTED
    assert secret_key not in plan.reason and IV not in plan.reason, plan.reason
    # It should still say enough to diagnose the problem.
    assert "32" in plan.reason or "wrong shape" in plan.reason, plan.reason
    print("✓ a rejection explains the shape without quoting the key material")


def test_hex_is_normalised() -> None:
    from app.services.potation import drm

    plan = drm.plan_delivery("Adrm", key=KEY.upper(), iv=f"  {IV.upper()}  ")
    assert plan.kind == drm.KIND_AAXC
    assert plan.key == KEY and plan.iv == IV, (plan.key, plan.iv)
    print("✓ upper-case and padded hex are accepted and normalised")


# ── Persistence and reuse ────────────────────────────────────────────────────

def _session():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database import Base
    from app.models import chaptarr, download, potation, user
    assert all((chaptarr, download, potation, user))

    engine = create_engine(
        f"sqlite:///{DATA / 'licences.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def test_key_material_is_encrypted_at_rest(db) -> None:
    from sqlalchemy import text
    from app.services.potation import license as lic
    from app.services.potation.creds import try_decrypt

    info = lic.LicenseInfo(
        asin="B0KEYTEST1", drm_type="Adrm",
        download_url="https://cdn.example/x?Expires=4102444800",
        has_voucher=True, key=KEY, iv=IV,
    )
    lic._persist(db, info)

    stored = db.connection().execute(
        text("SELECT key_ciphertext, iv_ciphertext FROM audible_licenses "
             "WHERE book_asin = 'B0KEYTEST1'")
    ).first()
    assert stored[0] and stored[0] != KEY, "the AAXC key was stored in plain text"
    assert stored[1] and stored[1] != IV, "the AAXC iv was stored in plain text"
    assert try_decrypt(stored[0]) == KEY and try_decrypt(stored[1]) == IV
    print("✓ the AAXC key and iv are encrypted at rest and decrypt back")


def test_reuse_returns_the_stored_licence(db) -> None:
    from app.services.potation import drm
    from app.services.potation import license as lic

    reused = lic.stored_license(db, "B0KEYTEST1")
    assert reused is not None, "a fresh stored licence was not reused"
    assert reused.key == KEY and reused.iv == IV
    # And it feeds the branch straight through.
    plan = drm.plan_delivery(reused.drm_type, key=reused.key, iv=reused.iv)
    assert plan.kind == drm.KIND_AAXC
    print("✓ a stored licence is reused and drives the delivery branch")


def test_a_census_probe_does_not_blank_stored_key_material(db) -> None:
    """The census re-licenses without decrypting; that must not erase a key.

    `_reprobe_as_native` writes over the same row. If persistence cleared the
    key columns whenever the incoming licence had none, a census run would
    quietly disarm every download licence already fetched.
    """
    from app.services.potation import license as lic

    lic._persist(db, lic.LicenseInfo(
        asin="B0KEYTEST1", drm_type="Adrm",
        download_url="https://cdn.example/x?Expires=4102444800",
        has_voucher=True,           # a voucher existed; we just did not decrypt it
    ))
    survived = lic.stored_license(db, "B0KEYTEST1")
    assert survived is not None and survived.key == KEY, (
        "a licence probe without voucher decryption erased the stored key"
    )
    print("✓ a probe that does not decrypt leaves stored key material alone")


def test_an_expired_link_is_not_reused(db) -> None:
    """An expired CDN link 403s *after* the download has started."""
    from app.services.potation import license as lic

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    lic._persist(db, lic.LicenseInfo(
        asin="B0EXPIRED1", drm_type="Adrm", key=KEY, iv=IV,
        download_url="https://cdn.example/y", url_expires_at=past,
    ))
    assert lic.stored_license(db, "B0EXPIRED1") is None, "an expired licence was reused"

    # And one expiring inside the margin is treated as expired, because the
    # download takes minutes and the link would die part-way through.
    soon = datetime.now(timezone.utc) + timedelta(minutes=1)
    lic._persist(db, lic.LicenseInfo(
        asin="B0SOON0001", drm_type="Adrm", key=KEY, iv=IV,
        download_url="https://cdn.example/z", url_expires_at=soon,
    ))
    assert lic.stored_license(db, "B0SOON0001") is None, (
        "a licence expiring within the margin was reused; the link would die mid-download"
    )

    later = datetime.now(timezone.utc) + timedelta(hours=6)
    lic._persist(db, lic.LicenseInfo(
        asin="B0GOOD0001", drm_type="Adrm", key=KEY, iv=IV,
        download_url="https://cdn.example/w", url_expires_at=later,
    ))
    assert lic.stored_license(db, "B0GOOD0001") is not None
    print("✓ an expired link — or one expiring within the margin — is not reused")


def test_unreadable_key_material_reads_as_no_licence(db) -> None:
    """A lost potation.key costs one licence request, not a broken download."""
    from sqlalchemy import text
    from app.services.potation import license as lic

    db.connection().execute(
        text("UPDATE audible_licenses SET key_ciphertext = 'not-fernet-ciphertext' "
             "WHERE book_asin = 'B0GOOD0001'")
    )
    db.commit()
    assert lic.stored_license(db, "B0GOOD0001") is None, (
        "a licence whose key will not decrypt was returned; the download would "
        "produce a file that does not play"
    )
    print("✓ key material that will not decrypt reads as 'no licence', not a bad one")


def test_missing_licence_is_none(db) -> None:
    from app.services.potation import license as lic
    assert lic.stored_license(db, "B0NOSUCH01") is None
    print("✓ a title with no stored licence returns None rather than raising")


# ── Runner ───────────────────────────────────────────────────────────────────

def main() -> None:
    test_the_four_arms()
    test_unsupported_plans_refuse_to_produce_arguments()
    test_unknown_drm_is_refused_not_assumed_plain()
    test_wrong_shaped_key_material_is_refused()
    test_key_material_never_appears_in_a_reason()
    test_hex_is_normalised()

    Session = _session()
    with Session() as db:
        test_key_material_is_encrypted_at_rest(db)
        test_reuse_returns_the_stored_licence(db)
        test_a_census_probe_does_not_blank_stored_key_material(db)
        test_an_expired_link_is_not_reused(db)
        test_unreadable_key_material_reads_as_no_licence(db)
        test_missing_licence_is_none(db)

    assert_no_stray_dirs()
    print("✓ no stray top-level directories created")
    print("\nAll DRM checks passed.")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    try:
        main()
    except AssertionError as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
    finally:
        shutil.rmtree(WORKDIR, ignore_errors=True)
