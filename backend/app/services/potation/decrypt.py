"""Turn a downloaded, encrypted file into a tagged, chaptered `.m4b`.

One ffmpeg invocation does the decryption, the remux, the chapters, the tags and
the cover. `-c copy` throughout: it is faster, lossless, and avoids the
chapter-offset shift that re-encoding introduces — Libation's own
`AllowLibationFixup` transforms (brand-audio stripping, LAME transcode,
downsampling) are deliberately not reproduced, because Chaptarr and
Audiobookshelf own presentation and none of them are worth a shifted timeline.

Three things here are load-bearing.

**The last chapter is extended to the end of the file.** Audible's `chapter_info`
describes the *content*; the delivered file is longer, by a brand outro. Libation
pads the final chapter by `fileDuration - EndOffset` for this reason. Inside a
single `.m4b` that is cosmetic — but **split-by-chapter is a toggle in Settings**,
and there the missing tail is not a chapter boundary being slightly off, it is
the end of the book silently absent from the last file.

**The ASIN tag is written unconditionally.** Libation's `FillMissingTags` assigns
`tags.Asin = book.AudibleProductId` with `=`, not `??=`, and that is right: the
embedded ASIN is the only identity that survives Chaptarr relocating and
renaming the file, and it is what reconciliation matches on first. A file that
loses its ASIN becomes unattributable the moment `import_mode=move` runs.

**ffmpeg is a child process, so cancellation means killing it.** The download
stage can stop between chunks; this one cannot, so `should_cancel` is polled
while ffmpeg runs and terminates it. Without that, cancelling a job would leave
ffmpeg chewing through a 20-hour book with nothing watching.
"""

from __future__ import annotations

import asyncio
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from ...models.potation import ERR_FFMPEG, ERR_UNKNOWN
from ..logger import get_logger
from .drm import DeliveryPlan, ffmpeg_decrypt_args

FFMPEG = "ffmpeg"

#: How often `should_cancel` is polled while ffmpeg runs.
POLL_INTERVAL = 0.5

#: Characters no filesystem we target will take, plus the ones that make a path
#: awkward to hand to a shell or a scanner.
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

#: ffmetadata treats these as syntax; they have to be escaped in every value.
_FFMETA_ESCAPE = re.compile(r"([=;#\\\n])")


class DecryptError(Exception):
    def __init__(self, message: str, error_code: str = ERR_FFMPEG):
        super().__init__(message)
        self.error_code = error_code


class DecryptCancelled(Exception):
    """The caller asked for this to stop. Not a failure."""


@dataclass(frozen=True)
class Chapter:
    start_ms: int
    end_ms: int
    title: str

    @property
    def length_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)


@dataclass
class BookMetadata:
    """What gets written into the file. `asin` is the one that must not be lost."""

    asin: str
    title: str
    subtitle: Optional[str] = None
    authors: list[str] = field(default_factory=list)
    narrators: list[str] = field(default_factory=list)
    series_name: Optional[str] = None
    series_sequence: Optional[str] = None
    year: Optional[str] = None
    publisher: Optional[str] = None
    description: Optional[str] = None
    chapters: list[Chapter] = field(default_factory=list)


def safe_filename(title: str, asin: str, suffix: str = ".m4b") -> str:
    """`<Title> [<ASIN>]<suffix>`.

    The bracketed ASIN is not decoration. Chaptarr's `DownloadedBooksScan`
    fallback and Audiobookshelf's `getASIN()` both read it out of the path —
    ABS matches `/(?: |^)\\[([A-Z0-9]{10})](?= |$)/` and puts `folderStructure`
    first in its metadata precedence — so this is the second identity carrier
    alongside the embedded tag.
    """
    cleaned = _UNSAFE.sub("_", title or "").strip().rstrip(".")
    cleaned = re.sub(r"\s+", " ", cleaned) or "Untitled"
    # Leave room for the suffix and the bracketed ASIN within a 255-byte name.
    budget = 255 - len(suffix) - len(asin) - 4
    if len(cleaned) > budget:
        cleaned = cleaned[:budget].rstrip()
    return f"{cleaned} [{asin}]{suffix}"


# ── Chapters ─────────────────────────────────────────────────────────────────

def parse_chapters(
    chapter_info: Optional[dict],
    *,
    file_duration_ms: Optional[int] = None,
) -> list[Chapter]:
    """Flatten Audible's `chapter_info` into a timeline, tail included.

    Audible nests chapters (parts containing chapters), and describes the
    *content* rather than the delivered file — which carries a brand outro past
    the last chapter's end. When `file_duration_ms` says the file is longer, the
    final chapter is extended to cover it. Skipping that is what makes a
    split-by-chapter export lose the end of the book.
    """
    if not chapter_info:
        return []

    flat: list[Chapter] = []
    _walk_chapters(chapter_info.get("chapters") or [], flat)
    flat.sort(key=lambda c: c.start_ms)

    if not flat:
        return []

    # Audible's own runtime is the fallback when the file has not been probed.
    end_of_file = file_duration_ms or chapter_info.get("runtime_length_ms") or 0
    if end_of_file and end_of_file > flat[-1].end_ms:
        last = flat[-1]
        flat[-1] = Chapter(last.start_ms, int(end_of_file), last.title)
    return flat


def _walk_chapters(raw: Iterable[dict], out: list[Chapter]) -> None:
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        start = entry.get("start_offset_ms")
        length = entry.get("length_ms")
        title = (entry.get("title") or "").strip()
        if start is not None and length:
            out.append(Chapter(int(start), int(start) + int(length), title or "Chapter"))
        # A part wrapping chapters contributes its children, not itself twice.
        nested = entry.get("chapters")
        if nested:
            _walk_chapters(nested, out)


def _escape(value: Any) -> str:
    return _FFMETA_ESCAPE.sub(r"\\\1", str(value))


def build_ffmetadata(meta: BookMetadata) -> str:
    """The `-f ffmetadata` document: tags first, then one block per chapter."""
    lines = [";FFMETADATA1"]

    def put(key: str, value: Any) -> None:
        if value not in (None, "", []):
            lines.append(f"{key}={_escape(value)}")

    put("title", meta.title)
    put("subtitle", meta.subtitle)
    put("artist", ", ".join(meta.authors))
    put("album_artist", ", ".join(meta.authors))
    put("album", meta.series_name or meta.title)
    put("composer", ", ".join(meta.narrators))
    put("date", meta.year)
    put("publisher", meta.publisher)
    put("description", meta.description)
    put("comment", meta.description)
    put("genre", "Audiobook")
    if meta.series_name:
        put("series", meta.series_name)
        put("series-part", meta.series_sequence)
    # Correct for containers that take arbitrary keys, and harmless for those
    # that do not — but **the mp4 muxer silently drops these**, which is why
    # `write_identity_tags()` exists. Do not read this as the ASIN being handled.
    put("ASIN", meta.asin)
    put("AUDIBLE_ASIN", meta.asin)

    for chapter in meta.chapters:
        lines += [
            "",
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={chapter.start_ms}",
            f"END={chapter.end_ms}",
            f"title={_escape(chapter.title)}",
        ]
    return "\n".join(lines) + "\n"


#: The freeform atom `reconcile._tag_asin` looks for. Written by Libation too,
#: and read by Audiobookshelf's `prober.js` as `file_tag_asin`.
MP4_ASIN_ATOMS = (
    "----:com.apple.iTunes:ASIN",
    "----:com.apple.iTunes:AUDIBLE_ASIN",
)


def write_identity_tags(path: Path, asin: str) -> None:
    """Write the ASIN as an MP4 freeform atom, after ffmpeg has finished.

    **ffmpeg cannot do this.** Its mp4 muxer maps a fixed set of metadata keys
    onto MP4 atoms and drops the rest without a warning, so `ASIN=` in the
    ffmetadata document produces a file with no ASIN in it at all. The tag is
    the only identity that survives Chaptarr moving and renaming the file — and
    the thing `reconcile.py` matches on first — so it is written here directly,
    in the `----:com.apple.iTunes:ASIN` shape that reconciliation reads back.
    """
    if not asin:
        return
    try:
        from mutagen.mp4 import MP4, MP4FreeForm
    except ImportError:  # pragma: no cover — mutagen is a hard dependency
        raise DecryptError("mutagen is not installed; cannot write the ASIN tag.")

    try:
        audio = MP4(str(path))
        if audio.tags is None:
            audio.add_tags()
        payload = MP4FreeForm(asin.encode("utf-8"))
        for atom in MP4_ASIN_ATOMS:
            audio.tags[atom] = [payload]
        audio.save()
    except Exception as exc:
        # Losing the ASIN is not cosmetic: the file becomes unattributable the
        # moment import_mode=move runs. Fail the job rather than ship it.
        raise DecryptError(
            f"Could not write the ASIN tag to {path.name}: {exc}", ERR_FFMPEG
        ) from exc


# ── Running ffmpeg ───────────────────────────────────────────────────────────

def build_command(
    source: Path,
    dest: Path,
    plan: DeliveryPlan,
    *,
    metadata_path: Optional[Path] = None,
    cover_path: Optional[Path] = None,
) -> list[str]:
    """The full argument vector, decryption flags first.

    The decryption flags must precede `-i`: they configure the *demuxer* reading
    that input. Placed after, ffmpeg accepts them silently and reads the file
    undecrypted, which is one more way to produce a plausible corrupt file.
    """
    cmd = [FFMPEG, "-hide_banner", "-nostdin", "-y"]
    cmd += ffmpeg_decrypt_args(plan)
    cmd += ["-i", str(source)]

    inputs = 1
    if metadata_path is not None:
        cmd += ["-i", str(metadata_path)]
        meta_index = inputs
        inputs += 1
    else:
        meta_index = None

    if cover_path is not None:
        cmd += ["-i", str(cover_path)]
        cover_index = inputs
        inputs += 1
    else:
        cover_index = None

    cmd += ["-map", "0:a"]
    if cover_index is not None:
        cmd += ["-map", f"{cover_index}:v", "-disposition:v:0", "attached_pic"]
    if meta_index is not None:
        cmd += ["-map_metadata", str(meta_index), "-map_chapters", str(meta_index)]

    # Lossless remux. Re-encoding costs time, quality and a shifted timeline.
    cmd += ["-c:a", "copy"]
    if cover_index is not None:
        cmd += ["-c:v", "copy"]
    cmd += ["-movflags", "+faststart", "-f", "mp4"]
    cmd += ["-progress", "pipe:1", "-nostats", str(dest)]
    return cmd


_OUT_TIME = re.compile(rb"out_time_us=(\d+)")


async def transcode(
    source: Path,
    dest: Path,
    plan: DeliveryPlan,
    meta: Optional[BookMetadata] = None,
    *,
    cover_path: Optional[Path] = None,
    duration_ms: Optional[int] = None,
    on_progress: Optional[Callable[[int], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> Path:
    """Decrypt and remux `source` into `dest`, cancellably.

    Writes to `<dest>.partial` and renames on success, for the same reason the
    download stage does: a half-written `.m4b` that looks finished is worse than
    no file at all, because everything downstream will happily import it.
    """
    if not plan.usable:
        raise DecryptError(plan.reason or "Unsupported delivery plan.", ERR_UNKNOWN)
    if shutil.which(FFMPEG) is None:
        raise DecryptError("ffmpeg is not installed.", ERR_FFMPEG)

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    working = dest.with_name(dest.name + ".partial")
    metadata_path = None

    if meta is not None:
        metadata_path = working.with_suffix(working.suffix + ".ffmeta")
        metadata_path.write_text(build_ffmetadata(meta), encoding="utf-8")

    cmd = build_command(
        source, working, plan, metadata_path=metadata_path, cover_path=cover_path
    )

    try:
        await _run(cmd, duration_ms, on_progress, should_cancel, working)
    finally:
        if metadata_path is not None:
            metadata_path.unlink(missing_ok=True)

    # Tagged before the rename, so the file that appears at `dest` is complete
    # and attributable in one step rather than briefly missing its ASIN.
    if meta is not None:
        try:
            write_identity_tags(working, meta.asin)
        except DecryptError:
            working.unlink(missing_ok=True)
            raise

    working.replace(dest)
    if on_progress is not None:
        on_progress(100)
    return dest


async def _run(
    cmd: list[str],
    duration_ms: Optional[int],
    on_progress: Optional[Callable[[int], None]],
    should_cancel: Optional[Callable[[], bool]],
    working: Path,
) -> None:
    logger = get_logger()
    process = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )

    async def pump() -> None:
        assert process.stdout is not None
        async for line in process.stdout:
            if on_progress is None or not duration_ms:
                continue
            match = _OUT_TIME.search(line)
            if match:
                done_ms = int(match.group(1)) // 1000
                on_progress(min(100, int(done_ms * 100 / duration_ms)))

    reader = asyncio.create_task(pump())
    try:
        while True:
            try:
                await asyncio.wait_for(process.wait(), timeout=POLL_INTERVAL)
                break
            except asyncio.TimeoutError:
                if should_cancel is not None and should_cancel():
                    await _terminate(process)
                    working.unlink(missing_ok=True)
                    raise DecryptCancelled("Cancelled while decrypting.")
    finally:
        reader.cancel()
        stderr = b""
        if process.stderr is not None:
            try:
                stderr = await process.stderr.read()
            except Exception:  # the pipe is gone after a kill; nothing to add
                pass

    if process.returncode != 0:
        working.unlink(missing_ok=True)
        tail = stderr.decode("utf-8", "replace").strip().splitlines()[-6:]
        logger.error("[potation-decrypt] ffmpeg exited %s", process.returncode)
        raise DecryptError(
            f"ffmpeg exited {process.returncode}: " + " / ".join(tail), ERR_FFMPEG
        )


async def _terminate(process) -> None:
    """Ask, then insist. A 20-hour remux will not stop on its own."""
    try:
        process.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except asyncio.TimeoutError:
        try:
            process.kill()
        except ProcessLookupError:
            pass
        await process.wait()
