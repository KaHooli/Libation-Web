"""Pulling an Audible library into our own tables.

Replaces `libationcli scan` and the direct reads of `LibationContext.db`. What
comes back from Audible is upserted into `books`, so the schema is ours and the
column names stop drifting with someone else's releases.

Multi-part titles need care: a `MultiPartBook` parent has no downloadable
content of its own, and its parts are separate products. They are stored as
child rows pointing back at the parent, with `part_index` set, so a later
download works on the parts and file ordering is numeric rather than lexical.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from sqlalchemy.orm import Session

from ...models.potation import AudibleAccount, Book
from ..logger import get_logger
from .client import client_for, mark_synced

#: Everything needed to render the Liberate page and to tag a finished file.
RESPONSE_GROUPS = ",".join([
    "contributors",
    "media",
    "price",
    "product_attrs",
    "product_desc",
    "product_extended_attrs",
    "product_plan_details",
    "product_plans",
    "rating",
    "series",
    "relationships",
    "customer_rights",
])

#: Audible caps this; 1000 is the documented maximum.
PAGE_SIZE = 1000

#: Relationship kind that links a multi-part parent to its parts.
_PART_RELATIONSHIP = "component"


@dataclass
class SyncResult:
    account_id: str
    fetched: int = 0
    added: int = 0
    updated: int = 0
    parts: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total_written(self) -> int:
        return self.added + self.updated


def _names(items: Optional[Iterable[dict]]) -> Optional[list[str]]:
    if not items:
        return None
    out = [str(i.get("name")).strip() for i in items if isinstance(i, dict) and i.get("name")]
    return out or None


def _series(item: dict) -> tuple[Optional[str], Optional[str]]:
    series = item.get("series") or []
    if not series:
        return None, None
    first = series[0] if isinstance(series[0], dict) else {}
    return (first.get("title") or None), (first.get("sequence") or None)


def _parse_date(value: Any) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    for candidate in (text, text[:10]):
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _cover_url(item: dict) -> Optional[str]:
    images = item.get("product_images") or {}
    if not isinstance(images, dict) or not images:
        return None
    # Keys are pixel widths as strings; take the largest available.
    def _width(key: str) -> int:
        try:
            return int(key)
        except (TypeError, ValueError):
            return 0

    best = max(images, key=_width)
    return images.get(best) or None


def _is_audible_plus(item: dict) -> Optional[bool]:
    # `is_ayce` is the "any you can eat" (Audible Plus / Escape) flag. Falls back
    # to inspecting the plan list, which is where older responses carry it.
    if "is_ayce" in item:
        return bool(item.get("is_ayce"))
    plans = item.get("plans") or item.get("product_plans") or []
    for plan in plans:
        if not isinstance(plan, dict):
            continue
        name = str(plan.get("plan_name") or "")
        if "AYCE" in name.upper() or "US Minerva" in name:
            return True
    return None


def _apply(book: Book, item: dict, account_id: str) -> None:
    book.account_id = account_id
    book.title = (item.get("title") or "").strip()
    book.subtitle = (item.get("subtitle") or "").strip() or None
    book.authors = _names(item.get("authors"))
    book.narrators = _names(item.get("narrators"))
    book.series_name, book.series_sequence = _series(item)

    runtime = item.get("runtime_length_min")
    book.length_minutes = int(runtime) if isinstance(runtime, (int, float)) else None

    book.language = item.get("language") or None
    book.is_abridged = (
        str(item.get("format_type") or "").lower() == "abridged"
        if item.get("format_type") is not None else None
    )
    book.content_type = item.get("content_type") or None
    book.content_delivery_type = item.get("content_delivery_type") or None
    book.is_audible_plus = _is_audible_plus(item)
    book.purchase_date = _parse_date(item.get("purchase_date"))
    book.release_date = _parse_date(item.get("release_date") or item.get("issue_date"))
    book.publisher = item.get("publisher_name") or None
    book.description = (
        item.get("merchandising_summary")
        or item.get("publisher_summary")
        or None
    )
    book.cover_url = _cover_url(item)
    book.synced_at = datetime.now(timezone.utc)


def _upsert(db: Session, item: dict, account_id: str, result: SyncResult,
            *, parent_asin: Optional[str] = None, part_index: Optional[int] = None) -> None:
    asin = (item.get("asin") or "").strip()
    if not asin:
        return

    book = db.query(Book).filter(Book.asin == asin).first()
    if book is None:
        book = Book(asin=asin)
        db.add(book)
        result.added += 1
    else:
        result.updated += 1

    _apply(book, item, account_id)
    book.parent_asin = parent_asin
    book.part_index = part_index

    relationships = item.get("relationships") or []
    book.is_multipart_parent = any(
        isinstance(r, dict)
        and r.get("relationship_type") == _PART_RELATIONSHIP
        and r.get("relationship_to_product") == "child"
        for r in relationships
    )


def _child_parts(item: dict) -> list[dict]:
    """Parts of a multi-part title, in order.

    A `MultiPartBook` parent carries no downloadable content; the parts are the
    products that do. `sort` is what Audible orders them by, and using it is what
    stops "Part 10" sorting before "Part 2".
    """
    parts = [
        r for r in (item.get("relationships") or [])
        if isinstance(r, dict)
        and r.get("relationship_type") == _PART_RELATIONSHIP
        and r.get("relationship_to_product") == "child"
        and r.get("asin")
    ]

    def _order(rel: dict) -> int:
        for key in ("sort", "sequence"):
            try:
                return int(rel.get(key))
            except (TypeError, ValueError):
                continue
        return 0

    return sorted(parts, key=_order)


def sync_account(db: Session, account: AudibleAccount) -> SyncResult:
    """Fetch one account's library and upsert it."""
    logger = get_logger()
    result = SyncResult(account_id=account.account_id)
    logger.info("[potation-sync] Starting library sync for %s", account.account_id)

    items: list[dict] = []
    with client_for(db, account) as client:
        page = 1
        while True:
            response = client.get(
                "library",
                response_groups=RESPONSE_GROUPS,
                num_results=PAGE_SIZE,
                page=page,
                sort_by="-PurchaseDate",
            )
            batch = response.get("items") or []
            items.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
            page += 1

    result.fetched = len(items)

    for item in items:
        try:
            _upsert(db, item, account.account_id, result)
            for index, part in enumerate(_child_parts(item), start=1):
                # The relationship carries only ids, so the part inherits the
                # parent's metadata until a later sync fetches it in its own
                # right. Enough to queue and order a download.
                merged = {**{k: v for k, v in item.items() if k != "relationships"},
                          **{"asin": part["asin"], "title": part.get("title") or item.get("title")}}
                _upsert(
                    db, merged, account.account_id, result,
                    parent_asin=item.get("asin"), part_index=index,
                )
                result.parts += 1
        except Exception as exc:
            result.errors.append(f"{item.get('asin')}: {exc}")

    db.commit()
    mark_synced(db, account)
    logger.info(
        "[potation-sync] %s: fetched %d, added %d, updated %d, parts %d, errors %d",
        account.account_id, result.fetched, result.added, result.updated,
        result.parts, len(result.errors),
    )
    return result


def sync_all(db: Session) -> list[SyncResult]:
    from .client import active_accounts

    return [sync_account(db, account) for account in active_accounts(db)]
