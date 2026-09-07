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

    # 4. Phase B's gate: download and decrypt one real book, end to end
    PYTHONPATH=backend python scripts/potation-census.py download

    # 5. Release the device registration once you are FINISHED
    PYTHONPATH=backend python scripts/potation-census.py disconnect

Step 5 is not optional housekeeping: `login` registers a device on the Amazon
account, Amazon caps how many an account may hold, and deleting the scratch
directory does *not* release one — the registration lives at Amazon.

But do it **last**, not after every run. `disconnect` removes the account row,
so the next census needs a fresh `login` — which spends another registration.
While you are still iterating, leave the account connected and just re-run
`census`; the synced library is reused and costs nothing.

Point DATABASE_URL, LIBATION_CONFIG and AUDIOBOOKS_DIR at your real install to
use accounts you have already connected. Left unset, this keeps its own
database, credential key and any downloaded audio under `.potation-census/`
(gitignored, and override with POTATION_CENSUS_DIR) — that directory holds live
Audible credentials, so treat it like any other secret and delete it when you
are done.

**What `census` and `download` each prove.** The census establishes that Audible
will *offer* a natively-decryptable licence; it deliberately does not decrypt
the voucher, so it cannot overclaim. `download` is the separate question — that
the voucher → key/iv → ffmpeg path yields a file that plays, carries its
chapters, and can still be identified as this book after Chaptarr renames it.
One is not evidence for the other.

**On quota:** a Download license counts against Audible's daily download
allowance, so this samples rather than sweeping. `--sample 0` checks everything
and is a lot of requests — think before using it on a large library. Licences
fetched here are stored and reusable by a later download, so a probe is not
purely spent.
"""
import argparse
import asyncio
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

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
# `download` writes a real audiobook. The packaged default is `/audiobooks`,
# which is a container path — outside one it either does not exist or belongs to
# something else, so scratch mode keeps the audio with the rest of the scratch
# state. An explicit AUDIOBOOKS_DIR still wins, which is how you point this at a
# real install.
os.environ.setdefault("AUDIOBOOKS_DIR", str(SCRATCH / "audiobooks"))
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
            # Owned titles: standalone books and multi-part parents. The parts
            # themselves are not separately licensable, so counting them here
            # would promise a sample size the census cannot deliver.
            total = (
                db.query(Book)
                .filter(Book.account_id == account.account_id,
                        Book.parent_asin.is_(None))
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
    print(f"  Answered:           {census.answered}")
    for drm, count in sorted(census.counts.items(), key=lambda kv: -kv[1]):
        flag = "native" if drm in license_svc.NATIVE_CAPABLE_DRM else "NEEDS CDM"
        print(f"    {drm:<12} {count:>5}   {flag}")
    if census.failures:
        print(f"  Failed to license:  {len(census.failures)}")
        for asin, err in census.failures[:5]:
            print(f"    {asin}: {err}")
        if len(census.failures) > 5:
            print(f"    ...and {len(census.failures) - 5} more")

    parts = census.part_asin_failures
    if parts:
        print(
            f"\n  ! {len(parts)} of those are part ASINs of multi-part titles, which\n"
            f"    Audible no longer licenses individually. That is a fault in the\n"
            f"    sample, not in the library — the numbers below are not a\n"
            f"    measurement until it is fixed."
        )

    print(f"\n  Natively downloadable: {census.native_capable}")
    if census.fell_back_to_native:
        print(
            f"    of which {len(census.fell_back_to_native)} only after asking as a native\n"
            f"    engine would — Audible offered a CDM scheme first, then served\n"
            f"    these natively when we stopped claiming to support one."
        )
    print(f"  Needs a CDM:           {census.cdm_required}")
    print(f"\n  {census.verdict()}\n")

    if census.unreachable:
        print("  Titles a Python engine could not fetch:")
        for asin in census.unreachable[:20]:
            print(f"    {asin}")
        if len(census.unreachable) > 20:
            print(f"    ...and {len(census.unreachable) - 20} more")


# ── Phase B's gate ───────────────────────────────────────────────────────────

def cmd_download(args) -> int:
    """Download and decrypt one real book, then check what came out.

    This is the gate the census cannot be: Audible *offering* a native licence
    and the pipeline *producing a playable file* are two different claims, and
    only a real encrypted file exercises the `aaxc` arm at all.

    It drives the production path — `enqueue` → `claim_next` → `run_job` with
    the default pipeline — rather than re-implementing it here. A gate that
    tests a parallel copy of the code proves something about the copy.
    """
    from app.services.potation import queue as queue_svc

    with SessionLocal() as db:
        accounts = active_accounts(db)
        if not accounts:
            print("No usable Audible accounts. Run `login` first.", file=sys.stderr)
            return 1

        book = _pick_book(db, args.asin)
        if book is None:
            if args.asin:
                print(f"No book {args.asin!r} in the library. Run `sync`?", file=sys.stderr)
            else:
                print("No books synced yet — run `sync` first.", file=sys.stderr)
            return 1

        length = f"{book.length_minutes} min" if book.length_minutes else "unknown length"
        print(f"\nBook:   {book.title or '(untitled)'}")
        print(f"ASIN:   {book.asin}   ({length})")
        if not args.asin:
            print(f"        picked as the shortest title over {REAL_BOOK_MINUTES} min —")
            print("        smallest transfer that is still a real audiobook rather")
            print("        than a sample. Override with --asin.")
        print(f"Output: {os.environ['AUDIOBOOKS_DIR']}")
        print(
            "\nThis spends one Download licence against Audible's daily allowance\n"
            "(a stored licence is reused, so a re-run of the same title is free)."
        )
        if not args.yes and input("Continue? [y/N] ").strip().lower() != "y":
            print("Nothing downloaded.")
            return 0

        # Everything needed after the session closes, read out now: the session
        # expires its instances on every commit, and the pipeline commits
        # repeatedly, so `book.title` would be a detached-instance error by the
        # time verification wants it.
        wanted = _BookFacts(
            asin=book.asin, title=book.title, length_minutes=book.length_minutes
        )

        job = queue_svc.enqueue(db, wanted.asin, book_title=wanted.title)
        claimed = queue_svc.claim_next(db)
        if claimed is None or claimed.id != job.id:
            # Something else holds it: a worker, or a previous run that died
            # mid-flight and left the row in a working state.
            print(
                f"\nJob {job.id} is in state {job.state!r} and could not be claimed.\n"
                "Another run may still hold it.",
                file=sys.stderr,
            )
            return 1

        print()
        state = asyncio.run(_run_with_progress(db, claimed, queue_svc))
        failure = (claimed.error_code, claimed.error_message)
        # Which of drm.py's four arms actually ran. The gate exists for `aaxc`,
        # and a title Audible served unencrypted would otherwise pass it while
        # proving nothing about decryption.
        delivery = _delivery_kind(db, wanted.asin)

    if state != queue_svc.JOB_COMPLETE:
        print(f"\n✗ Job ended {state}: [{failure[0]}] {failure[1]}")
        return 1

    return _verify(wanted, args, delivery)


def _delivery_kind(db, asin) -> str:
    """The delivery arm the stored licence implies, or `unknown`."""
    from app.services.potation.drm import plan_delivery

    info = license_svc.stored_license(db, asin)
    if info is None:
        return "unknown"
    return plan_delivery(info.drm_type, key=info.key, iv=info.iv).kind


@dataclass(frozen=True)
class _BookFacts:
    """The book, as plain values that outlive the session it was read from."""
    asin: str
    title: Optional[str]
    length_minutes: Optional[int]


#: Shorter than this and a title is more likely a sample, a bonus interview or a
#: podcast episode than a purchased audiobook — and those are exactly the things
#: Audible tends to serve unencrypted, which would not exercise the arm the gate
#: is for. Not a hard filter: it is a preference, with a fallback below.
REAL_BOOK_MINUTES = 60


def _pick_book(db, asin):
    """The named book, or the shortest *plausible* audiobook.

    Shortest because the gate's evidence does not scale with the transfer: a
    two-hour book proves exactly what a twenty-hour one does, for a tenth of the
    bytes. But shortest-overall would reliably pick a sample, and a sample is
    the one thing that can pass every check while leaving AAXC decryption
    untouched — so the floor comes first, and the fallback only applies when
    nothing in the library clears it.

    Parts are excluded (`parent_asin IS NULL`): Audible refuses to license them
    individually.
    """
    query = db.query(Book).filter(Book.parent_asin.is_(None))
    if asin:
        return query.filter(Book.asin == asin).first()

    known = query.filter(Book.length_minutes.isnot(None), Book.length_minutes > 0)
    return (
        known.filter(Book.length_minutes >= REAL_BOOK_MINUTES)
        .order_by(Book.length_minutes.asc())
        .first()
        or known.order_by(Book.length_minutes.desc()).first()
    )


async def _run_with_progress(db, job, queue_svc):
    """Run the job, reporting the row's progress from a second session.

    The reporter deliberately does not read the job object the pipeline is
    using: a SQLAlchemy session is not built to be shared, and reading through
    a session of its own is also the more honest observation — it sees what any
    other reader of the queue would, which is the whole point of the row
    existing.
    """
    from app.models.potation import DownloadJob

    done = asyncio.Event()
    job_id = job.id

    async def report():
        last = None
        while not done.is_set():
            with SessionLocal() as watcher:
                row = watcher.query(DownloadJob).filter(DownloadJob.id == job_id).first()
                now = (row.state, row.stage_progress or 0) if row else None
            if now and now != last:
                print(f"\r  {now[0]:<12} {now[1]:>3}%   ", end="", flush=True)
                last = now
            await asyncio.sleep(0.5)

    reporter = asyncio.create_task(report())
    try:
        return await queue_svc.run_job(db, job)
    finally:
        done.set()
        await reporter
        print()


def _verify(book, args, delivery: str = "unknown") -> int:
    """Check the artifact, and say plainly which claims it does and does not support.

    Each check is one thing that has to be true for the file to be usable
    downstream. They are reported individually because "it failed" is not
    actionable — *which* of decryption, chapters or identity failed is.
    """
    from app.services.potation.decrypt import safe_filename

    dest = Path(os.environ["AUDIOBOOKS_DIR"]) / safe_filename(
        book.title or book.asin, book.asin
    )
    checks: list[tuple[bool, str]] = []

    def check(ok: bool, label: str) -> bool:
        checks.append((bool(ok), label))
        return bool(ok)

    print(f"\nVerifying {dest.name}\n")

    if not check(dest.exists(), "the file exists"):
        return _summarise(checks, dest, delivery)

    size = dest.stat().st_size
    check(size > 64 * 1024, f"it is not a stub ({size / 1_048_576:.1f} MB)")

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_chapters", str(dest)],
        capture_output=True, text=True,
    )
    if not check(probe.returncode == 0, "ffprobe parses the container"):
        print(f"    {probe.stderr.strip()[:400]}")
        return _summarise(checks, dest, delivery)

    meta = json.loads(probe.stdout or "{}")
    duration = float(meta.get("format", {}).get("duration") or 0)
    check(duration > 0, f"it has a duration ({duration / 60:.1f} min)")

    if book.length_minutes:
        # Audible's own minute count against the delivered file. This catches a
        # wrong *file*; it does not catch a truncated one, because the declared
        # duration lives in the `moov` atom and survives a missing tail. That is
        # the decode pass's job, below.
        drift = abs(duration / 60 - book.length_minutes)
        check(
            drift <= max(3, book.length_minutes * 0.05),
            f"the duration matches the library ({book.length_minutes} min, off by {drift:.1f})",
        )

    chapters = meta.get("chapters") or []
    check(len(chapters) > 0, f"chapters are in the file ({len(chapters)})")
    if chapters:
        last_end = float(chapters[-1].get("end_time") or 0)
        check(
            duration - last_end <= 1.0,
            "the last chapter reaches the end of the file "
            f"(ends at {last_end / 60:.1f} of {duration / 60:.1f} min)",
        )

    # The identity carrier. Read back through the *reconciler's* reader, not a
    # local re-implementation, so this pins the writer and the reader together —
    # the ASIN is what survives Chaptarr moving and renaming the file.
    from mutagen import File as MutagenFile

    from app.services.potation.reconcile import _tag_asin

    tagged = None
    try:
        tagged = _tag_asin(MutagenFile(str(dest)))
    except Exception as exc:
        print(f"    reading tags failed: {exc}")
    check(tagged == book.asin, f"reconciliation reads the ASIN back ({tagged or 'absent'})")
    check(f"[{book.asin}]" in dest.name, "the filename carries the ASIN")

    # The check none of the others can make. With `-c copy` nothing decodes
    # during the remux, so a *wrong* key of the right length yields a file
    # ffprobe describes quite happily and no player can play. Decoding is what
    # tells the difference — and it is also the only check here that catches a
    # truncated transfer, since the duration ffprobe reports comes from the
    # `moov` atom and survives a missing tail.
    #
    # The whole file by default: a book truncated near the end passes any
    # prefix-sized slice, and the CPU is worth more than a gate that says PASSED
    # about a file whose last hour is missing.
    if not args.skip_decode:
        window = ["-t", str(args.decode_seconds)] if args.decode_seconds > 0 else []
        scope = f"first {args.decode_seconds}s" if args.decode_seconds > 0 else "whole file"
        print(f"  … decoding ({scope}) — this takes a moment", flush=True)
        decode = subprocess.run(
            ["ffmpeg", "-v", "error", *window, "-i", str(dest), "-f", "null", "-"],
            capture_output=True, text=True,
        )
        noise = decode.stderr.strip()
        if not check(
            decode.returncode == 0 and not noise,
            f"the audio decodes cleanly ({scope})",
        ):
            print(f"    {noise[:400]}")

    return _summarise(checks, dest, delivery)


def _summarise(checks, dest, delivery: str = "unknown") -> int:
    for ok, label in checks:
        print(f"  {'✓' if ok else '✗'} {label}")

    failed = [label for ok, label in checks if not ok]
    print(f"\n  Delivered as: {delivery}")
    print()
    if failed:
        print(f"✗ Phase B's gate FAILED — {len(failed)} of {len(checks)} checks did not pass.")
        print(f"  The file is at {dest} if it helps to look at it.")
        return 1

    if delivery != "aaxc":
        # The gate exists for the decryption path. A title Audible served
        # unencrypted exercises the queue, the remux, the chapters and the tags
        # — everything except the one branch that was unproven — so reporting it
        # as PASSED would be the same overclaim the census was careful to avoid.
        print("~ The pipeline worked, but this does NOT clear Phase B's gate.")
        print(f"  Audible delivered this title as `{delivery}`, so the AAXC")
        print("  decryption arm — the part that was unproven — never ran.")
        print("  Re-run against a normal purchased audiobook:")
        print("      ... download --asin B0XXXXXXXX")
        print(f"\n  {dest}")
        return 1

    print("✓ Phase B's gate PASSED. A real AAXC-encrypted book downloaded,")
    print("  decrypted, kept its chapters, decoded cleanly, and can still be")
    print("  identified by ASIN after a rename.")
    print(f"\n  {dest}")
    print(
        "\n  What this does not cover: the `aax` (activation-bytes) arm is a\n"
        "  different branch and stays unproven until a title arrives that way."
    )
    return 0


def cmd_disconnect(args) -> int:
    """Release the device registration this script created.

    `login` registers a device on the Amazon account and Amazon caps how many
    an account may hold, so a census that leaves one stranded costs a slot for
    nothing. Removing the scratch directory alone would *not* release it — the
    registration lives at Amazon, not on disk.

    The account row goes with it, so this is a one-way door for the session:
    another census means another `login`, which spends another registration.
    Run it when the measuring is finished, not between runs.
    """
    with SessionLocal() as db:
        rows = db.query(AudibleAccount).order_by(AudibleAccount.account_id).all()
        if not rows:
            print("No Audible accounts connected.")
            return 0

        targets = [a for a in rows if a.account_id == args.account_id] if args.account_id else rows
        if not targets:
            print(f"No account {args.account_id!r}. Try `accounts`.", file=sys.stderr)
            return 1

        if not args.yes:
            print(
                "This removes the account and releases its device registration.\n"
                "Another census afterwards needs a fresh `login`, which spends\n"
                "another registration — so only do this once you are finished."
            )
            if input("Continue? [y/N] ").strip().lower() != "y":
                print("Left connected.")
                return 0

        for account in targets:
            print(f"Disconnecting {account.account_id} ({account.account_name or '-'}) ...")
            auth_svc.disconnect_account(db, account.account_id)
            print("  ✓ removed locally; device deregistration attempted at Amazon")

    print("\nSafe to delete the scratch directory now:")
    print(f"  rm -rf {SCRATCH}")
    return 0


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

    p_dl = sub.add_parser(
        "download", help="download and decrypt one real book (Phase B's gate)"
    )
    p_dl.add_argument(
        "--asin", default=None,
        help=f"which book; omitted picks the shortest title over "
             f"{REAL_BOOK_MINUTES} minutes, so the gate does not land on a "
             f"sample Audible would serve unencrypted",
    )
    p_dl.add_argument(
        "--decode-seconds", type=int, default=0,
        help="seconds of audio to decode as a correctness check; "
             "0 decodes the whole file (default)",
    )
    p_dl.add_argument(
        "--skip-decode", action="store_true",
        help="skip the decode check entirely — it is the only one that catches "
             "a wrong decryption key or a truncated transfer, so prefer "
             "--decode-seconds over this",
    )
    p_dl.add_argument(
        "-y", "--yes", action="store_true",
        help="skip the confirmation prompt",
    )
    p_dl.set_defaults(func=cmd_download)

    p_disc = sub.add_parser(
        "disconnect", help="deregister the device and remove the account"
    )
    p_disc.add_argument(
        "account_id", nargs="?", default=None,
        help="which account; omitted disconnects all of them",
    )
    p_disc.add_argument(
        "-y", "--yes", action="store_true",
        help="skip the confirmation prompt",
    )
    p_disc.set_defaults(func=cmd_disconnect)

    args = parser.parse_args()
    run_migrations()
    return args.func(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        raise SystemExit(130)
