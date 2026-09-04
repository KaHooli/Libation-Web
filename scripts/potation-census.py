#!/usr/bin/env python3
"""Connect an Audible account, sync its library, and measure DRM exposure.

This answers the question the whole native-engine plan hangs on: **what share of
your library could a pure-Python engine actually download?**

A Python pipeline can decrypt Adrm (AAX and AAXC, via ffmpeg) and pass through
unencrypted delivery. It has no content decryption module, so anything Audible
serves under Widevine, PlayReady or FairPlay is out of reach — those titles would
still need LibationCli.

    # 1. Connect an account (prints a URL, asks for the redirect you land on)
    PYTHONPATH=backend python scripts/potation-census.py login --marketplace us

    # 2. Pull the library into the local database
    PYTHONPATH=backend python scripts/potation-census.py sync

    # 3. Measure. Samples 25 titles by default
    PYTHONPATH=backend python scripts/potation-census.py census --sample 25

Point DATABASE_URL and LIBATION_CONFIG at your real install to use accounts you
have already connected. Left unset, this keeps its own database and credential
key under `.potation-census/` (gitignored, and override with
POTATION_CENSUS_DIR) — that directory holds live Audible credentials, so treat
it like any other secret and delete it when you are done.

**On quota:** a Download license counts against Audible's daily download
allowance, so this samples rather than sweeping. `--sample 0` checks everything
and is a lot of requests — think before using it on a large library. Licences
fetched here are stored and reusable by a later download, so a probe is not
purely spent.
"""
import argparse
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# Fall back to a scratch workspace so the script is safe to try out, but respect
# a real install when one is pointed at. Confined to one gitignored directory
# because both the database and `potation.key` hold live Audible credentials —
# they must not land loose in the repository root.
SCRATCH = Path(os.environ.get("POTATION_CENSUS_DIR", ".potation-census")).resolve()
SCRATCH.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("DATABASE_URL", f"sqlite:///{SCRATCH / 'census.db'}")
os.environ.setdefault("LIBATION_CONFIG", str(SCRATCH))
os.environ.setdefault("SECRET_KEY", "potation-census-local-only")

from app.database import SessionLocal  # noqa: E402
from app.migrations import run_migrations  # noqa: E402
from app.models.potation import AudibleAccount, Book  # noqa: E402
from app.services.potation import auth as auth_svc  # noqa: E402
from app.services.potation import library as library_svc  # noqa: E402
from app.services.potation import license as license_svc  # noqa: E402
from app.services.potation.client import active_accounts  # noqa: E402
from app.services.potation.marketplaces import VALID_MARKETPLACES  # noqa: E402


def cmd_login(args) -> int:
    with SessionLocal() as db:
        started = auth_svc.begin_login(db, marketplace=args.marketplace, email=args.email)

        print("\n1. Open this URL in a browser and sign in to Audible:\n")
        print(f"   {started['login_url']}\n")
        print("2. After signing in you will land on a page that may fail to load.")
        print("   That is expected — copy the full URL from the address bar.\n")

        response_url = input("Paste the URL you landed on: ").strip()
        if not response_url:
            print("Nothing pasted; aborting.", file=sys.stderr)
            return 1

        try:
            account = auth_svc.complete_login(db, started["session_id"], response_url)
        except auth_svc.AudibleAuthError as exc:
            print(f"\nSign-in failed: {exc}", file=sys.stderr)
            return 1

    print(f"\n✓ Connected Audible account {account.account_id} ({account.account_name})")
    print("  Next: python scripts/potation-census.py sync")
    return 0


def cmd_sync(args) -> int:
    with SessionLocal() as db:
        accounts = active_accounts(db)
        if not accounts:
            print("No usable Audible accounts. Run `login` first.", file=sys.stderr)
            return 1

        for account in accounts:
            print(f"Syncing {account.account_id} ...", flush=True)
            result = library_svc.sync_account(db, account)
            print(
                f"  fetched {result.fetched}, added {result.added}, "
                f"updated {result.updated}, parts {result.parts}"
            )
            for err in result.errors[:5]:
                print(f"  ! {err}", file=sys.stderr)
            if len(result.errors) > 5:
                print(f"  ! ...and {len(result.errors) - 5} more", file=sys.stderr)
    return 0


def cmd_census(args) -> int:
    with SessionLocal() as db:
        accounts = active_accounts(db)
        if not accounts:
            print("No usable Audible accounts. Run `login` first.", file=sys.stderr)
            return 1

        sample = None if args.sample == 0 else args.sample
        for account in accounts:
            total = (
                db.query(Book)
                .filter(Book.account_id == account.account_id,
                        Book.is_multipart_parent.is_(False))
                .count()
            )
            if total == 0:
                print(f"{account.account_id}: no books synced yet — run `sync` first.")
                continue

            checking = total if sample is None else min(sample, total)
            print(f"\n{account.account_id}: {total} titles, checking {checking}")
            if sample is None and total > 100:
                print(
                    f"  ! A full sweep issues {total} license requests, which counts\n"
                    f"    against Audible's daily download allowance.",
                    file=sys.stderr,
                )
                if input("  Continue? [y/N] ").strip().lower() != "y":
                    continue

            def progress(done, of, _census):
                print(f"\r  {done}/{of}", end="", flush=True)

            census = license_svc.run_census(
                db, account, sample_size=sample,
                consumption_type=args.consumption_type, progress=progress,
            )
            print()
            _report(census)
    return 0


def _report(census: license_svc.DrmCensus) -> None:
    print(f"\n  Sampled:            {census.sampled}")
    for drm, count in sorted(census.counts.items(), key=lambda kv: -kv[1]):
        flag = "native" if drm in license_svc.NATIVE_CAPABLE_DRM else "NEEDS CDM"
        print(f"    {drm:<12} {count:>5}   {flag}")
    if census.failures:
        print(f"  Failed to license:  {len(census.failures)}")
        for asin, err in census.failures[:5]:
            print(f"    {asin}: {err}")
        if len(census.failures) > 5:
            print(f"    ...and {len(census.failures) - 5} more")

    print(f"\n  Natively downloadable: {census.native_capable}")
    print(f"  Needs a CDM:           {census.cdm_required}")
    print(f"\n  {census.verdict()}\n")

    if census.unreachable:
        print("  Titles a Python engine could not fetch:")
        for asin in census.unreachable[:20]:
            print(f"    {asin}")
        if len(census.unreachable) > 20:
            print(f"    ...and {len(census.unreachable) - 20} more")


def cmd_accounts(args) -> int:
    with SessionLocal() as db:
        rows = db.query(AudibleAccount).order_by(AudibleAccount.account_id).all()
        if not rows:
            print("No Audible accounts connected.")
            return 0
        for a in rows:
            books = db.query(Book).filter(Book.account_id == a.account_id).count()
            state = "needs re-auth" if a.needs_reauth else ("active" if a.is_active else "disabled")
            print(f"{a.account_id}  {a.account_name or '-'}  [{a.locale}]  {state}  {books} books")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser("login", help="connect an Audible account")
    p_login.add_argument(
        "--marketplace", default="us",
        help="one of: " + ", ".join(sorted(VALID_MARKETPLACES)),
    )
    p_login.add_argument("--email", default=None, help="optional, for labelling only")
    p_login.set_defaults(func=cmd_login)

    sub.add_parser("accounts", help="list connected accounts").set_defaults(func=cmd_accounts)
    sub.add_parser("sync", help="pull libraries into the database").set_defaults(func=cmd_sync)

    p_census = sub.add_parser("census", help="measure DRM exposure")
    p_census.add_argument(
        "--sample", type=int, default=25,
        help="titles to check per account; 0 checks everything (default: 25)",
    )
    p_census.add_argument(
        "--consumption-type", default="Download", choices=["Download", "Streaming"],
        help="Download reflects what a real download would get (default)",
    )
    p_census.set_defaults(func=cmd_census)

    args = parser.parse_args()
    run_migrations()
    return args.func(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130)
