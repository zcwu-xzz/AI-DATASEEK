from pathlib import Path

import pysam
import pytest

from app.application.services.alignment_preview import (
    AlignmentPreviewError,
    AlignmentRegionOptions,
    extract_region,
    inspect_alignment,
    AlignmentPreviewCache,
)


def _write_bam(path: Path) -> None:
    header = {
        "HD": {"VN": "1.6", "SO": "coordinate"},
        "SQ": [{"SN": "chr1", "LN": 10_000}, {"SN": "chr2", "LN": 5_000}],
        "RG": [{"ID": "sample-1", "SM": "sample"}],
    }
    with pysam.AlignmentFile(path, "wb", header=header) as output:
        for index, start in enumerate((100, 130, 500)):
            read = pysam.AlignedSegment()
            read.query_name = f"read-{index}"
            read.query_sequence = "A" * 50
            read.flag = 16 if index == 1 else 0
            read.reference_id = 0
            read.reference_start = start
            read.mapping_quality = 60 - index
            read.cigar = ((0, 50),)
            read.query_qualities = pysam.qualitystring_to_array("I" * 50)
            output.write(read)


def test_inspect_alignment_returns_public_header_metadata(tmp_path: Path):
    path = tmp_path / "sample.bam"
    _write_bam(path)

    result = inspect_alignment(path)

    assert result == {
        "format": "BAM",
        "references": [
            {"name": "chr1", "length": 10_000},
            {"name": "chr2", "length": 5_000},
        ],
        "sort_order": "coordinate",
        "read_groups": 1,
        "suggested_reference": "chr1",
        "suggested_start": 100,
    }
    assert str(path) not in str(result)


def test_sam_inspection_and_region_extraction(tmp_path: Path):
    path = tmp_path / "sample.sam"
    header = {"HD": {"VN": "1.6", "SO": "coordinate"}, "SQ": [{"SN": "chr1", "LN": 10_000}]}
    with pysam.AlignmentFile(path, "w", header=header) as output:
        read = pysam.AlignedSegment()
        read.query_name = "sam-read"
        read.query_sequence = "A" * 40
        read.flag = 0
        read.reference_id = 0
        read.reference_start = 1_250
        read.mapping_quality = 50
        read.cigar = ((0, 20), (3, 100), (0, 20))
        read.query_qualities = pysam.qualitystring_to_array("I" * 40)
        output.write(read)

    metadata = inspect_alignment(path)
    assert metadata["format"] == "SAM"
    assert metadata["suggested_reference"] == "chr1"
    assert metadata["suggested_start"] == 1_250
    region = extract_region(path, AlignmentRegionOptions(reference="chr1", start=1_200, end=1_500))
    assert region["returned_reads"] == 1
    assert region["reads"][0]["splices"] == [{"start": 1270, "end": 1370, "length": 100}]


def test_extract_region_returns_bounded_reads_and_coverage_without_index(tmp_path: Path):
    path = tmp_path / "sample.bam"
    _write_bam(path)

    result = extract_region(
        path,
        AlignmentRegionOptions(reference="chr1", start=90, end=220, max_reads=10, bin_count=20),
    )

    assert [item["name"] for item in result["reads"]] == ["read-0", "read-1"]
    assert result["reads"][1]["reverse"] is True
    assert max(item["depth"] for item in result["coverage"]) == 2
    assert result["truncated"] is False


def test_extract_region_rejects_missing_reference_and_oversized_window(tmp_path: Path):
    path = tmp_path / "sample.bam"
    _write_bam(path)

    with pytest.raises(AlignmentPreviewError, match="reference does not exist"):
        extract_region(path, AlignmentRegionOptions(reference="chrX", start=0, end=100))
    with pytest.raises(AlignmentPreviewError, match="exceeds"):
        extract_region(path, AlignmentRegionOptions(reference="chr1", start=0, end=2_000_001))


def test_preview_cache_is_user_scoped_and_releasable(tmp_path: Path):
    source = tmp_path / "sample.bam"
    _write_bam(source)
    cache = AlignmentPreviewCache(ttl_seconds=60)
    with source.open("rb") as stream:
        item = cache.create("user-a", "file-a", ".bam", stream)
    assert cache.get(item.preview_id, "user-a", "file-a").path.exists()
    with pytest.raises(AlignmentPreviewError, match="失效"):
        cache.get(item.preview_id, "user-b", "file-a")
    cache.delete(item.preview_id, "user-a", "file-a")
    assert not item.directory.exists()


def test_preview_cache_bounds_per_user_entries(tmp_path: Path):
    source = tmp_path / "sample.bam"
    _write_bam(source)
    cache = AlignmentPreviewCache(ttl_seconds=60, max_entries=3, max_entries_per_user=2)
    items = []
    for index in range(3):
        with source.open("rb") as stream:
            items.append(cache.create("user-a", f"file-{index}", ".bam", stream))
    assert not items[0].directory.exists()
    assert items[1].directory.exists()
    assert items[2].directory.exists()


def test_preview_cache_stops_copy_at_size_limit(tmp_path: Path):
    source = tmp_path / "large.bam"
    source.write_bytes(b"12345")
    cache = AlignmentPreviewCache()
    with source.open("rb") as stream, pytest.raises(AlignmentPreviewError, match="1 GB"):
        cache.create("user-a", "file-a", ".bam", stream, max_bytes=4)
