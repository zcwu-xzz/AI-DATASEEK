from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

pytest.importorskip("Bio")

ROOT = Path(__file__).resolve().parents[2]
SPEC = spec_from_file_location("sequence_advanced_operations", ROOT / "tools/sequence_advanced/operations.py")
operations = module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(operations)


def test_fasta_metrics_split_and_gap_profile(tmp_path: Path):
    source = tmp_path / "sample.fasta"
    source.write_text(">one\nAAAANNNNCCCC\n>two\nGGGGTTTT\n", encoding="utf-8")
    metrics = operations.fasta_assembly_metrics({"input_path": str(source)})
    assert metrics["sequence_count"] == 2
    assert metrics["N50"] == 12
    split = operations.fasta_split_merge({"mode": "split", "input_path": str(source), "output_path": str(tmp_path / "parts"), "records_per_file": 1})
    assert Path(split["output_path"]).is_file()
    assert len(split["files"]) == 2
    gaps = operations.fasta_gap_profile({"input_path": str(source), "min_gap": 2})
    assert gaps["gap_count"] == 1
    assert gaps["gap_bases"] == 4


def test_cds_segments_are_joined_by_transcript_and_strand(tmp_path: Path):
    fasta = tmp_path / "reference.fasta"; gff = tmp_path / "genes.gff3"; output = tmp_path / "cds.fasta"
    fasta.write_text(">chr1\nAAACCCGGGTTTAAACCCGGGTTT\n", encoding="utf-8")
    gff.write_text("chr1\ttest\tCDS\t1\t3\t.\t+\t0\tID=c1;Parent=tx1\nchr1\ttest\tCDS\t7\t9\t.\t+\t0\tID=c2;Parent=tx1\nchr1\ttest\tCDS\t13\t15\t.\t-\t0\tID=c3;Parent=tx2\nchr1\ttest\tCDS\t19\t21\t.\t-\t0\tID=c4;Parent=tx2\n", encoding="utf-8")
    result = operations.fasta_annotation_extract({"fasta_path": str(fasta), "annotation_path": str(gff), "output_path": str(output), "feature_type": "CDS"})
    assert result["records_written"] == 2
    records = list(operations.iter_records(output))
    assert str(records[0].seq) == "AAAGGG"
    assert str(records[1].seq) == "CCCTTT"


def test_fastq_fraction_downsample_is_deterministic(tmp_path: Path):
    source = tmp_path / "reads.fastq"
    source.write_text("".join(f"@r{i}\nACGT\n+\nIIII\n" for i in range(100)), encoding="utf-8")
    first = tmp_path / "first.fastq"; second = tmp_path / "second.fastq"
    one = operations.fastq_downsample({"input_path": str(source), "output_path": str(first), "fraction": 0.25, "seed": 7})
    two = operations.fastq_downsample({"input_path": str(source), "output_path": str(second), "fraction": 0.25, "seed": 7})
    assert one["selected_reads_or_pairs"] == two["selected_reads_or_pairs"]
    assert first.read_bytes() == second.read_bytes()
