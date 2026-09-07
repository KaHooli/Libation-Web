#!/usr/bin/env python3
"""The decrypt/remux stage: chapters, tags, cover, cancellation.

Phase B3. Runs real ffmpeg against real audio and reads the result back with
mutagen, because the claims worth making here are about a file that exists:
"the ASIN is in the tags", "the last chapter reaches the end", "cancelling
leaves nothing behind". Asserting on an argument vector would prove only that
the code builds the vector it builds.

The AAXC arm cannot be exercised without a real encrypted file and a real
licence, so it is not claimed here: `scripts/test-drm.py` covers which flags are
produced, and this covers that the runner works. What is *not* covered is stated
rather than papered over.

Three failures these guard:

  * **A truncated last chapter.** Audible's chapter_info describes the content;
    the file is longer. Inside one .m4b that is cosmetic — with split-by-chapter
    on, which is a toggle in Settings, the end of the book is simply missing.
  * **A lost ASIN.** The embedded tag is the only identity that survives
    Chaptarr moving and renaming the file. Without it a book becomes
    unattributable the moment import_mode=move runs.
  * **A half-written .m4b that looks finished.** Everything downstream imports
    happily; a person finds out during playback.

Needs ffmpeg on PATH (it is in the runtime image) and `backend/requirements.txt`.

Usage:
    PYTHONPATH=backend scripts/test-decrypt.py
"""
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

WORKDIR = Path(tempfile.mkdtemp(prefix="decrypt-test-"))
CONFIG = WORKDIR / "config"
DATA = WORKDIR / "data"
BOOKS = WORKDIR / "audiobooks"
for d in (DATA, CONFIG, BOOKS):
    d.mkdir(parents=True, exist_ok=True)

os.environ.setdefault("DATABASE_URL", f"sqlite:///{DATA / 'app.db'}")
os.environ.setdefault("LIBATION_CONFIG", str(CONFIG))
os.environ.setdefault("AUDIOBOOKS_DIR", str(BOOKS))
os.environ.setdefault("SECRET_KEY", "decrypt-test-only-not-a-real-secret")

PRODUCTION_PATHS = [Path("/data"), Path("/config"), Path("/audiobooks")]
PREEXISTING = {p for p in PRODUCTION_PATHS if p.exists()}

#: Seconds of synthetic audio. Long enough for chapters to be distinguishable,
#: short enough that a remux is instant.
AUDIO_SECONDS = 6


def assert_no_stray_dirs() -> None:
    created = sorted(str(p) for p in PRODUCTION_PATHS if p.exists() and p not in PREEXISTING)
    assert not created, (
        f"app created {created} instead of using its configured paths — "
        "this fails on an unprivileged host"
    )


def make_source(path: Path, seconds: int = AUDIO_SECONDS) -> Path:
    """A real AAC-in-MP4 file, which is the shape an AAXC download decrypts to."""
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:a", "aac", "-b:a", "32k", str(path)],
        check=True,
    )
    return path


def make_cover(path: Path) -> Path:
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "color=c=navy:s=64x64:d=1", "-frames:v", "1", str(path)],
        check=True,
    )
    return path


# ── Chapters: the truncation guard ───────────────────────────────────────────

def test_last_chapter_is_extended_to_the_end_of_the_file() -> None:
    from app.services.potation.decrypt import parse_chapters

    info = {
        "runtime_length_ms": 30_000,
        "chapters": [
            {"start_offset_ms": 0, "length_ms": 10_000, "title": "One"},
            {"start_offset_ms": 10_000, "length_ms": 10_000, "title": "Two"},
        ],
    }
    # The delivered file is longer than the chapters describe — a brand outro.
    chapters = parse_chapters(info, file_duration_ms=25_000)
    assert len(chapters) == 2
    assert chapters[-1].end_ms == 25_000, (
        f"the last chapter ends at {chapters[-1].end_ms}, not the file's 25000 — "
        "with split-by-chapter on, the end of the book would be missing"
    )
    assert chapters[0].end_ms == 10_000, "an earlier chapter was disturbed"

    # Without a probed duration, Audible's own runtime is the fallback.
    fallback = parse_chapters(info)
    assert fallback[-1].end_ms == 30_000, fallback[-1]

    # A file *shorter* than the chapters must not have its last chapter shrunk
    # by this rule — that would be inventing a truncation rather than fixing one.
    shorter = parse_chapters(info, file_duration_ms=15_000)
    assert shorter[-1].end_ms == 20_000, shorter[-1]
    print("✓ the last chapter is extended to the end of the file, never shortened")


def test_nested_chapters_are_flattened_and_ordered() -> None:
    from app.services.potation.decrypt import parse_chapters

    info = {
        "runtime_length_ms": 40_000,
        "chapters": [
            {"start_offset_ms": 20_000, "length_ms": 10_000, "title": "Part Two",
             "chapters": [
                 {"start_offset_ms": 20_000, "length_ms": 5_000, "title": "Ch 3"},
                 {"start_offset_ms": 25_000, "length_ms": 5_000, "title": "Ch 4"},
             ]},
            {"start_offset_ms": 0, "length_ms": 20_000, "title": "Part One",
             "chapters": [
                 {"start_offset_ms": 0, "length_ms": 10_000, "title": "Ch 1"},
                 {"start_offset_ms": 10_000, "length_ms": 10_000, "title": "Ch 2"},
             ]},
        ],
    }
    chapters = parse_chapters(info, file_duration_ms=40_000)
    titles = [c.title for c in chapters]
    assert titles == ["Part One", "Ch 1", "Ch 2", "Part Two", "Ch 3", "Ch 4"], titles
    starts = [c.start_ms for c in chapters]
    assert starts == sorted(starts), starts
    print("✓ nested chapters are flattened and ordered by start time")


def test_missing_chapter_info_is_not_an_error() -> None:
    from app.services.potation.decrypt import parse_chapters

    assert parse_chapters(None) == []
    assert parse_chapters({}) == []
    assert parse_chapters({"chapters": []}) == []
    # Entries missing the fields we need are dropped, not guessed at.
    assert parse_chapters({"chapters": [{"title": "No offsets"}]}) == []
    print("✓ absent or unusable chapter data yields no chapters rather than raising")


def test_ffmetadata_escaping() -> None:
    """An unescaped '=' or ';' in a title silently truncates the tag."""
    from app.services.potation.decrypt import BookMetadata, Chapter, build_ffmetadata

    meta = BookMetadata(
        asin="B0TEST0001",
        title="Trouble: =, ; and # walk in",
        authors=["Ann; Author"],
        chapters=[Chapter(0, 1000, "Chapter 1 = the first")],
    )
    doc = build_ffmetadata(meta)
    assert doc.startswith(";FFMETADATA1")
    assert r"title=Trouble: \=, \; and \# walk in" in doc, doc
    assert r"artist=Ann\; Author" in doc
    assert r"title=Chapter 1 \= the first" in doc
    assert "START=0\nEND=1000" in doc
    print("✓ ffmetadata escapes the characters it treats as syntax")


# ── The runner, against real ffmpeg ──────────────────────────────────────────

async def test_remux_writes_tags_and_chapters(tmp) -> None:
    from mutagen.mp4 import MP4
    from app.services.potation import drm
    from app.services.potation.decrypt import BookMetadata, Chapter, transcode

    source = make_source(tmp / "source.m4a")
    dest = tmp / "out.m4b"
    meta = BookMetadata(
        asin="B0REALTEST",
        title="A Test Recording",
        authors=["Ann Author"],
        narrators=["Nick Narrator"],
        series_name="The Series",
        series_sequence="2",
        year="2026",
        chapters=[Chapter(0, 3000, "First"), Chapter(3000, 6000, "Second")],
    )
    plan = drm.plan_delivery("Mpeg")

    seen = []
    out = await transcode(
        source, dest, plan, meta,
        duration_ms=AUDIO_SECONDS * 1000, on_progress=seen.append,
    )
    assert out.exists() and out.stat().st_size > 0
    assert not dest.with_name(dest.name + ".partial").exists(), "the working file survived"
    assert not list(tmp.glob("*.ffmeta")), "the ffmetadata scratch file was left behind"
    assert seen and seen[-1] == 100, seen[-1:]

    tags = MP4(str(out))
    assert tags.tags["\xa9nam"] == ["A Test Recording"], tags.tags.get("\xa9nam")
    assert tags.tags["\xa9ART"] == ["Ann Author"]
    print("✓ a remux produces a real .m4b with the expected title and artist")

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_chapters", str(out)],
        capture_output=True, text=True, check=True,
    )
    import json
    chapters = json.loads(probe.stdout)["chapters"]
    assert len(chapters) == 2, chapters
    assert chapters[0]["tags"]["title"] == "First"
    assert abs(float(chapters[-1]["end_time"]) - 6.0) < 0.2, chapters[-1]
    print("✓ chapters survive into the output file with the right titles and bounds")


async def test_the_asin_reaches_the_file(tmp) -> None:
    """The tag reconciliation matches on first, and the only one that survives a move."""
    from mutagen.mp4 import MP4
    from app.services.potation import drm
    from app.services.potation.decrypt import BookMetadata, transcode

    source = make_source(tmp / "asin-source.m4a", seconds=2)
    dest = tmp / "asin.m4b"
    await transcode(
        source, dest, drm.plan_delivery("Mpeg"),
        BookMetadata(asin="B0ASINTEST", title="Identity"),
    )

    raw = MP4(str(dest)).tags
    found = {
        key: value for key, value in raw.items()
        if "ASIN" in key.upper()
    }
    assert found, (
        "no ASIN tag reached the output; the file is unattributable once "
        f"Chaptarr renames it. tags present: {sorted(raw.keys())}"
    )
    assert any("B0ASINTEST" in str(v) for v in found.values()), found
    print("✓ the ASIN is written into the output file's tags")


async def test_reconciliation_can_read_back_what_we_wrote(tmp) -> None:
    """The contract that matters, across the module boundary.

    `decrypt` writing *an* ASIN-ish tag and `reconcile` reading *an* ASIN-ish tag
    are two separate beliefs; nothing makes them agree unless something checks.
    This is the pairing that lets a book survive `import_mode=move`.
    """
    from mutagen import File as MutagenFile
    from app.services.potation import drm
    from app.services.potation.decrypt import BookMetadata, transcode
    from app.services.potation.reconcile import _tag_asin

    source = make_source(tmp / "roundtrip-source.m4a", seconds=2)
    dest = tmp / "roundtrip.m4b"
    await transcode(
        source, dest, drm.plan_delivery("Mpeg"),
        BookMetadata(asin="B0015T963C", title="Round Trip"),
    )

    tags = MutagenFile(str(dest))
    recovered = _tag_asin(tags.tags if tags is not None else None)
    assert recovered == "B0015T963C", (
        f"reconciliation read {recovered!r} from a file this module tagged — "
        "the writer and the reader disagree, so a moved file is unattributable"
    )
    print("✓ reconciliation reads back the exact ASIN the decrypt stage wrote")


async def test_cover_art_is_embedded(tmp) -> None:
    from mutagen.mp4 import MP4
    from app.services.potation import drm
    from app.services.potation.decrypt import BookMetadata, transcode

    source = make_source(tmp / "cover-source.m4a", seconds=2)
    cover = make_cover(tmp / "cover.png")
    dest = tmp / "cover.m4b"
    await transcode(
        source, dest, drm.plan_delivery("Mpeg"),
        BookMetadata(asin="B0COVERTST", title="With A Cover"),
        cover_path=cover,
    )
    art = MP4(str(dest)).tags.get("covr")
    assert art and len(bytes(art[0])) > 0, "no cover art was embedded"
    print("✓ a cover image is embedded rather than left as a sidecar")


async def test_cancellation_kills_ffmpeg_and_leaves_nothing(tmp) -> None:
    from app.services.potation import drm
    from app.services.potation.decrypt import DecryptCancelled, transcode

    # Long enough that ffmpeg is still running at the first cancel poll.
    source = make_source(tmp / "long-source.m4a", seconds=900)
    dest = tmp / "cancelled.m4b"

    try:
        await transcode(
            source, dest, drm.plan_delivery("Mpeg"),
            should_cancel=lambda: True,
        )
        raise AssertionError("the transcode was not cancelled")
    except DecryptCancelled:
        pass

    assert not dest.exists(), "a cancelled transcode produced a finished file"
    assert not dest.with_name(dest.name + ".partial").exists(), (
        "the half-written file was left behind; everything downstream would "
        "import it as though it were complete"
    )
    assert not list(tmp.glob("*.ffmeta"))
    print("✓ a cancel kills ffmpeg and leaves no partial or scratch file")


async def test_ffmpeg_failure_is_classified_and_leaves_nothing(tmp) -> None:
    from app.models.potation import ERR_FFMPEG
    from app.services.potation import drm
    from app.services.potation.decrypt import DecryptError, transcode

    dest = tmp / "broken.m4b"
    garbage = tmp / "not-audio.m4a"
    garbage.write_bytes(b"this is not an audio file" * 100)

    try:
        await transcode(garbage, dest, drm.plan_delivery("Mpeg"))
        raise AssertionError("ffmpeg accepted a file that is not audio")
    except DecryptError as exc:
        assert exc.error_code == ERR_FFMPEG, exc.error_code
        assert str(exc).strip(), "the error carried no diagnostic from ffmpeg"

    assert not dest.exists() and not dest.with_name(dest.name + ".partial").exists()
    print("✓ an ffmpeg failure is classified, explained, and leaves no file")


async def test_an_unsupported_plan_never_reaches_ffmpeg(tmp) -> None:
    from app.services.potation import drm
    from app.services.potation.decrypt import DecryptError, transcode

    source = make_source(tmp / "wv-source.m4a", seconds=1)
    try:
        await transcode(source, tmp / "wv.m4b", drm.plan_delivery("Widevine"))
        raise AssertionError("a Widevine plan was handed to ffmpeg")
    except DecryptError as exc:
        assert "decryption module" in str(exc), str(exc)
    print("✓ an unsupported delivery plan is refused before ffmpeg is invoked")


# ── Command shape ────────────────────────────────────────────────────────────

def test_decryption_flags_precede_the_input() -> None:
    """Placed after -i, ffmpeg accepts them and reads the file undecrypted."""
    from app.services.potation import drm
    from app.services.potation.decrypt import build_command

    plan = drm.plan_delivery("Adrm", key="0" * 32, iv="1" * 32)
    cmd = build_command(Path("in.aaxc"), Path("out.m4b"), plan)
    assert cmd.index("-audible_key") < cmd.index("-i"), cmd
    assert cmd.index("-audible_iv") < cmd.index("-i"), cmd
    assert "-c:a" in cmd and cmd[cmd.index("-c:a") + 1] == "copy", (
        "the remux is not lossless; re-encoding shifts the chapter timeline"
    )

    plain = build_command(Path("in.mp3"), Path("out.m4b"), drm.plan_delivery("Mpeg"))
    assert "-audible_key" not in plain and "-activation_bytes" not in plain
    print("✓ decryption flags precede -i, and an unencrypted input gets none")


def test_safe_filename() -> None:
    from app.services.potation.decrypt import safe_filename

    assert safe_filename("The Final Empire", "B0015T963C") == "The Final Empire [B0015T963C].m4b"
    # ABS reads the bracketed ASIN out of the path, so the shape matters.
    import re
    assert re.search(r"(?: |^)\[([A-Z0-9]{10})](?= |$)",
                     Path(safe_filename("A Book", "B0015T963C")).stem)

    messy = safe_filename('Bad/Name: "quoted" | piped?', "B0TEST0001")
    assert not set(messy) & set('<>:"/\\|?*'), messy
    assert safe_filename("", "B0TEST0001").startswith("Untitled")
    assert len(safe_filename("x" * 400, "B0TEST0001")) <= 255
    print("✓ filenames are made safe, keep the bracketed ASIN, and stay in bounds")


# ── Runner ───────────────────────────────────────────────────────────────────

async def run_async(tmp) -> None:
    await test_remux_writes_tags_and_chapters(tmp)
    await test_the_asin_reaches_the_file(tmp)
    await test_reconciliation_can_read_back_what_we_wrote(tmp)
    await test_cover_art_is_embedded(tmp)
    await test_cancellation_kills_ffmpeg_and_leaves_nothing(tmp)
    await test_ffmpeg_failure_is_classified_and_leaves_nothing(tmp)
    await test_an_unsupported_plan_never_reaches_ffmpeg(tmp)


def main() -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        print("FAILED: ffmpeg/ffprobe not on PATH — this suite tests real output",
              file=sys.stderr)
        raise SystemExit(1)

    test_last_chapter_is_extended_to_the_end_of_the_file()
    test_nested_chapters_are_flattened_and_ordered()
    test_missing_chapter_info_is_not_an_error()
    test_ffmetadata_escaping()
    test_decryption_flags_precede_the_input()
    test_safe_filename()

    tmp = WORKDIR / "work"
    tmp.mkdir(parents=True, exist_ok=True)
    asyncio.run(run_async(tmp))

    assert_no_stray_dirs()
    print("✓ no stray top-level directories created")
    print("\nAll decrypt checks passed.")
    print("\nNot covered here: the aaxc and aax arms end to end, which need a "
          "real encrypted file and licence. test-drm.py covers which flags they "
          "produce; this covers that the runner works.")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    try:
        main()
    except AssertionError as exc:
        print(f"\nFAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
    finally:
        shutil.rmtree(WORKDIR, ignore_errors=True)
