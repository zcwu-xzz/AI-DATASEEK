from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

pysam = pytest.importorskip("pysam")


ROOT = Path(__file__).resolve().parents[2]
SPEC = spec_from_file_location("alignment_advanced_operations", ROOT / "tools/alignment_advanced/operations.py")
operations = module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(operations)


def write_bam(path: Path) -> None:
    header = {"HD": {"VN": "1.6", "SO": "coordinate"}, "SQ": [{"SN": "chr1", "LN": 1000}], "RG": [{"ID": "rg1", "SM": "sample"}]}
    with pysam.AlignmentFile(path, "wb", header=header) as output:
        first = pysam.AlignedSegment()
        first.query_name = "pair1"; first.query_sequence = "A" * 50; first.flag = 99
        first.reference_id = 0; first.reference_start = 100; first.mapping_quality = 60
        first.cigar = ((0, 50),); first.next_reference_id = 0; first.next_reference_start = 180; first.template_length = 130
        first.query_qualities = pysam.qualitystring_to_array("I" * 50); first.set_tag("NM", 0); first.set_tag("MD", "50"); first.set_tag("RG", "rg1")
        output.write(first)
        splice = pysam.AlignedSegment()
        splice.query_name = "splice"; splice.query_sequence = "C" * 40; splice.flag = 0
        splice.reference_id = 0; splice.reference_start = 150; splice.mapping_quality = 45
        splice.cigar = ((0, 20), (3, 100), (0, 20)); splice.query_qualities = pysam.qualitystring_to_array("I" * 40)
        splice.set_tag("NM", 0); splice.set_tag("MD", "40"); output.write(splice)
        second = pysam.AlignedSegment()
        second.query_name = "pair1"; second.query_sequence = "T" * 50; second.flag = 147
        second.reference_id = 0; second.reference_start = 180; second.mapping_quality = 55
        second.cigar = ((0, 50),); second.next_reference_id = 0; second.next_reference_start = 100; second.template_length = -130
        second.query_qualities = pysam.qualitystring_to_array("I" * 50); second.set_tag("NM", 0); second.set_tag("MD", "50"); second.set_tag("RG", "rg1")
        output.write(second)
    pysam.index(str(path))


def test_flagstat_coverage_and_pileup_include_full_reference(tmp_path: Path):
    bam = tmp_path / "sample.bam"; write_bam(bam)
    stats = operations.alignment_flagstat({"input_path": str(bam)})
    assert stats["total"] == 3
    assert stats["properly_paired"] == 2
    coverage = operations.alignment_coverage_accurate({"input_path": str(bam), "reference": "chr1"})
    assert coverage["total_bases"] == 1000
    assert 0 < coverage["covered_percent"] < 100
    assert coverage["zero_coverage_regions"][0]["start"] == 0
    pileup = operations.alignment_pileup({"input_path": str(bam), "reference": "chr1", "start": 90, "end": 250, "output_path": str(tmp_path / "pileup.csv")})
    assert pileup["positions"] > 0
    assert Path(pileup["output_path"]).is_file()


def test_region_product_and_rna_splice_profile(tmp_path: Path):
    bam = tmp_path / "sample.bam"; write_bam(bam)
    source_mode = bam.stat().st_mode
    region = operations.alignment_region_product({"input_path": str(bam), "reference": "chr1", "start": 90, "end": 260, "output_path": str(tmp_path / "region.bam")})
    assert region["records_written"] == 3
    assert Path(region["index_path"]).is_file()
    assert bam.stat().st_mode == source_mode
    splice = operations.alignment_rna_splice_profile({"input_path": str(bam), "output_path": str(tmp_path / "splice.csv")})
    assert splice["junction_count"] == 1
    assert splice["junctions"][0]["start"] == 170
    assert splice["junctions"][0]["end"] == 270


def test_read_group_conversion_and_workflows(tmp_path: Path):
    bam = tmp_path / "sample.bam"; write_bam(bam)
    changed = operations.alignment_read_group_manage({"input_path": str(bam), "output_path": str(tmp_path / "rg.bam"), "id": "new", "sample": "sample2"})
    with pysam.AlignmentFile(changed["output_path"], "rb") as alignment:
        assert alignment.header.to_dict()["RG"][-1]["ID"] == "new"
        assert all(read.get_tag("RG") == "new" for read in alignment.fetch(until_eof=True))
    converted = operations.alignment_format_convert({"input_path": str(bam), "output_path": str(tmp_path / "sample.sam")})
    assert converted["records_written"] == 3
    workflow = operations.alignment_region_analysis_workflow({"input_path": str(bam), "reference": "chr1", "start": 90, "end": 260, "output_dir": str(tmp_path / "workflow")})
    assert Path(workflow["output_path"]).is_file()
    assert Path(workflow["interactive_output_path"]).is_file()
    assert len(workflow["attachments"]) >= 6
