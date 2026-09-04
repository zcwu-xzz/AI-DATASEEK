"""Bounded, path-safe BAM/CRAM preview extraction.

The HTTP layer owns authorization and temporary-file lifecycle.  This module
only turns a local, short-lived alignment file into compact JSON-ready data.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import secrets
import shutil
import tempfile
import threading
import time
from typing import Any

import pysam


MAX_REGION_WIDTH = 2_000_000
MAX_READS = 2_000
MAX_BINS = 1_000
PREVIEW_TTL_SECONDS = 30 * 60


class AlignmentPreviewError(ValueError):
    """A user-actionable alignment preview error."""


@dataclass
class CachedAlignmentPreview:
    preview_id: str
    user_id: str
    file_id: str
    directory: Path
    path: Path
    expires_at: float


class AlignmentPreviewCache:
    """Process-local, bounded-lifetime files for repeated region navigation."""

    def __init__(self, ttl_seconds: int = PREVIEW_TTL_SECONDS, max_entries: int = 16, max_entries_per_user: int = 3):
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.max_entries_per_user = max_entries_per_user
        self._items: dict[str, CachedAlignmentPreview] = {}
        self._lock = threading.Lock()

    def _cleanup_locked(self) -> None:
        now = time.monotonic()
        expired = [key for key, item in self._items.items() if item.expires_at <= now]
        for key in expired:
            item = self._items.pop(key)
            shutil.rmtree(item.directory, ignore_errors=True)

    def _evict_locked(self, user_id: str) -> None:
        owned = sorted(
            (item for item in self._items.values() if item.user_id == user_id),
            key=lambda item: item.expires_at,
        )
        victims = owned[:max(0, len(owned) - self.max_entries_per_user + 1)]
        remaining = [item for item in self._items.values() if item not in victims]
        if len(remaining) >= self.max_entries:
            victims.extend(sorted(remaining, key=lambda item: item.expires_at)[:len(remaining) - self.max_entries + 1])
        for item in victims:
            self._items.pop(item.preview_id, None)
            shutil.rmtree(item.directory, ignore_errors=True)

    def create(self, user_id: str, file_id: str, suffix: str, stream, max_bytes: int | None = None) -> CachedAlignmentPreview:
        directory = Path(tempfile.mkdtemp(prefix="dataseek-alignment-preview-"))
        path = directory / f"alignment{suffix}"
        try:
            total = 0
            with path.open("wb") as destination:
                while chunk := stream.read(1024 * 1024):
                    total += len(chunk)
                    if max_bytes is not None and total > max_bytes:
                        raise AlignmentPreviewError("比对文件超过 1 GB 预览上限")
                    destination.write(chunk)
            preview = CachedAlignmentPreview(
                preview_id=secrets.token_urlsafe(24),
                user_id=user_id,
                file_id=file_id,
                directory=directory,
                path=path,
                expires_at=time.monotonic() + self.ttl_seconds,
            )
            with self._lock:
                self._cleanup_locked()
                self._evict_locked(user_id)
                self._items[preview.preview_id] = preview
            return preview
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise

    def get(self, preview_id: str, user_id: str, file_id: str) -> CachedAlignmentPreview:
        with self._lock:
            self._cleanup_locked()
            item = self._items.get(preview_id)
            if item is None or item.user_id != user_id or item.file_id != file_id:
                raise AlignmentPreviewError("预览会话已失效，请重新打开文件")
            item.expires_at = time.monotonic() + self.ttl_seconds
            return item

    def delete(self, preview_id: str, user_id: str, file_id: str) -> None:
        with self._lock:
            item = self._items.get(preview_id)
            if item is None or item.user_id != user_id or item.file_id != file_id:
                return
            self._items.pop(preview_id, None)
        shutil.rmtree(item.directory, ignore_errors=True)


alignment_preview_cache = AlignmentPreviewCache()


@dataclass(frozen=True)
class AlignmentRegionOptions:
    reference: str
    start: int
    end: int
    max_reads: int = 500
    bin_count: int = 500

    def validate(self) -> "AlignmentRegionOptions":
        if not self.reference.strip():
            raise AlignmentPreviewError("reference is required")
        if self.start < 0 or self.end <= self.start:
            raise AlignmentPreviewError("region coordinates are invalid")
        if self.end - self.start > MAX_REGION_WIDTH:
            raise AlignmentPreviewError(
                f"preview region exceeds the {MAX_REGION_WIDTH:,} base limit"
            )
        if not 1 <= self.max_reads <= MAX_READS:
            raise AlignmentPreviewError(f"max_reads must be between 1 and {MAX_READS}")
        if not 20 <= self.bin_count <= MAX_BINS:
            raise AlignmentPreviewError(f"bin_count must be between 20 and {MAX_BINS}")
        return self


def _open_alignment(path: Path):
    suffix = path.suffix.casefold()
    mode = "rc" if suffix == ".cram" else "r" if suffix == ".sam" else "rb"
    try:
        return pysam.AlignmentFile(str(path), mode, check_sq=False)
    except (ValueError, OSError) as exc:
        if suffix == ".cram":
            raise AlignmentPreviewError(
                "CRAM 文件无法解码；该文件可能需要未随文件提供的参考序列"
            ) from exc
        raise AlignmentPreviewError("SAM/BAM 文件无法打开或文件已损坏") from exc


def inspect_alignment(path: Path) -> dict[str, Any]:
    """Read only the alignment header and compact reference metadata."""

    with _open_alignment(path) as alignment:
        references = [
            {"name": name, "length": int(length)}
            for name, length in zip(alignment.references, alignment.lengths)
        ]
        header = alignment.header.to_dict()
        read_groups = header.get("RG", []) if isinstance(header, dict) else []
        sort_order = None
        if isinstance(header, dict) and isinstance(header.get("HD"), dict):
            sort_order = header["HD"].get("SO")
        suggested_reference = None
        suggested_start = None
        try:
            for index, read in enumerate(alignment.fetch(until_eof=True)):
                if not read.is_unmapped and read.reference_id >= 0 and read.reference_start >= 0:
                    suggested_reference = alignment.get_reference_name(read.reference_id)
                    suggested_start = int(read.reference_start)
                    break
                if index >= 9_999:
                    break
        except (ValueError, OSError):
            # Some CRAM files expose their header without the external
            # reference required to decode records. Header preview must remain
            # available; the region endpoint reports the actionable error.
            pass
        suffix = path.suffix.casefold()
        return {
            "format": "CRAM" if suffix == ".cram" else "SAM" if suffix == ".sam" else "BAM",
            "references": references,
            "sort_order": sort_order,
            "read_groups": len(read_groups) if isinstance(read_groups, list) else 0,
            "suggested_reference": suggested_reference,
            "suggested_start": suggested_start,
        }


def _iter_region(alignment, options: AlignmentRegionOptions):
    """Use an index when present, otherwise stream safely without creating one."""

    try:
        if alignment.has_index():
            yield from alignment.fetch(options.reference, options.start, options.end)
            return
    except (ValueError, OSError):
        pass

    reference_id = alignment.get_tid(options.reference)
    if reference_id < 0:
        raise AlignmentPreviewError(f"reference does not exist: {options.reference}")
    try:
        for read in alignment.fetch(until_eof=True):
            if read.reference_id != reference_id or read.is_unmapped:
                continue
            read_end = read.reference_end or read.reference_start
            if read_end <= options.start:
                continue
            if read.reference_start >= options.end:
                # Coordinate-sorted files can stop early; unsorted files keep scanning.
                if (alignment.header.to_dict().get("HD") or {}).get("SO") == "coordinate":
                    break
                continue
            yield read
    except (ValueError, OSError) as exc:
        if bool(getattr(alignment, "is_cram", False)):
            raise AlignmentPreviewError("CRAM 文件需要参考序列才能读取当前区域") from exc
        raise AlignmentPreviewError("比对记录读取失败") from exc


def extract_region(path: Path, raw_options: AlignmentRegionOptions) -> dict[str, Any]:
    options = raw_options.validate()
    window = options.end - options.start
    bin_width = max(1, (window + options.bin_count - 1) // options.bin_count)
    bin_count = (window + bin_width - 1) // bin_width
    # Store covered bases in each bin and normalize at the end. Incrementing a
    # bin once per read exaggerates short edge overlaps and is not depth.
    covered_bases = [0] * bin_count
    reads: list[dict[str, Any]] = []
    total_overlapping = 0
    truncated = False

    with _open_alignment(path) as alignment:
        if options.reference not in alignment.references:
            raise AlignmentPreviewError(f"reference does not exist: {options.reference}")
        for read in _iter_region(alignment, options):
            total_overlapping += 1
            read_start = max(options.start, int(read.reference_start))
            read_end = min(options.end, int(read.reference_end or read.reference_start + 1))
            for block_start, block_end in read.get_blocks():
                overlap_start = max(options.start, block_start)
                overlap_end = min(options.end, block_end)
                if overlap_end <= overlap_start:
                    continue
                first = max(0, (overlap_start - options.start) // bin_width)
                last = min(bin_count - 1, (overlap_end - 1 - options.start) // bin_width)
                for index in range(first, last + 1):
                    bin_start = options.start + index * bin_width
                    bin_end = min(options.end, bin_start + bin_width)
                    covered_bases[index] += max(0, min(overlap_end, bin_end) - max(overlap_start, bin_start))
            if len(reads) < options.max_reads:
                mismatches = []
                if read.has_tag("MD") and read.query_sequence:
                    try:
                        for query_pos, reference_pos, reference_base in read.get_aligned_pairs(with_seq=True):
                            if query_pos is None or reference_pos is None or not reference_base:
                                continue
                            query_base = read.query_sequence[query_pos]
                            if query_base.upper() != reference_base.upper():
                                mismatches.append({"position": int(reference_pos), "query_base": query_base.upper(), "reference_base": reference_base.upper()})
                    except (ValueError, TypeError):
                        pass
                insertions = []
                deletions = []
                splices = []
                reference_position = int(read.reference_start)
                query_position = 0
                for operation, length in read.cigartuples or []:
                    if operation in {0, 7, 8}:
                        reference_position += length; query_position += length
                    elif operation == 1:
                        sequence = (read.query_sequence or "")[query_position:query_position + length]
                        insertions.append({"position": reference_position, "length": int(length), "sequence": sequence[:100]})
                        query_position += length
                    elif operation == 2:
                        deletions.append({"start": reference_position, "end": reference_position + length, "length": int(length)})
                        reference_position += length
                    elif operation == 3:
                        splices.append({"start": reference_position, "end": reference_position + length, "length": int(length)})
                        reference_position += length
                    elif operation == 4:
                        query_position += length
                reads.append(
                    {
                        "name": read.query_name or "(unnamed)",
                        "start": read_start,
                        "end": read_end,
                        "reverse": bool(read.is_reverse),
                        "mapq": int(read.mapping_quality),
                        "cigar": read.cigarstring or "*",
                        "paired": bool(read.is_paired),
                        "duplicate": bool(read.is_duplicate),
                        "secondary": bool(read.is_secondary),
                        "supplementary": bool(read.is_supplementary),
                        "read1": bool(read.is_read1),
                        "read2": bool(read.is_read2),
                        "mate_start": int(read.next_reference_start) if read.next_reference_start >= 0 else None,
                        "mate_reference": alignment.get_reference_name(read.next_reference_id) if read.next_reference_id >= 0 else None,
                        "template_length": int(read.template_length),
                        "read_group": read.get_tag("RG") if read.has_tag("RG") else None,
                        "nm": int(read.get_tag("NM")) if read.has_tag("NM") else None,
                        "md": str(read.get_tag("MD")) if read.has_tag("MD") else None,
                        "blocks": [{"start": int(start), "end": int(end)} for start, end in read.get_blocks()],
                        "mismatches": mismatches[:500],
                        "insertions": insertions[:100],
                        "deletions": deletions[:100],
                        "splices": splices[:100],
                    }
                )
            else:
                truncated = True
                # Depth has already been computed from enough records for an
                # interactive overview. Avoid an unbounded full-file scan.
                if total_overlapping >= options.max_reads * 4:
                    break

    bins = [
        {
            "start": options.start + index * bin_width,
            "end": min(options.end, options.start + (index + 1) * bin_width),
            "depth": round(value / max(1, min(bin_width, options.end - (options.start + index * bin_width))), 3),
        }
        for index, value in enumerate(covered_bases)
    ]
    return {
        "reference": options.reference,
        "start": options.start,
        "end": options.end,
        "bin_width": bin_width,
        "coverage": bins,
        "reads": reads,
        "returned_reads": len(reads),
        "scanned_overlapping_reads": total_overlapping,
        "truncated": truncated,
    }
