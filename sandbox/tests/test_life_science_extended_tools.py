import base64
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def _run(plugin: str, tool: str, arguments: dict) -> dict:
    payload = base64.urlsafe_b64encode(json.dumps(arguments).encode()).decode()
    process = subprocess.run(
        [sys.executable, str(ROOT / "tools" / plugin / "operations.py"), tool, payload],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(process.stdout)


def test_sequence_motif_orf_translation_and_subsequence_tools(tmp_path: Path):
    fasta = tmp_path / "genes.fasta"
    fasta.write_text(">gene1\nCCCATGAAATAGGGGATGCCCTAA\n", encoding="utf-8")

    motif = _run("sequence", "sequence_motif_search", {
        "input_path": str(fasta), "motif": "ATG", "both_strands": True,
    })
    assert motif["hit_count"] == 3
    assert [item["start"] for item in motif["hits"] if item["strand"] == "+"] == [4, 16]

    translated = tmp_path / "translated.fasta"
    result = _run("sequence", "sequence_translate", {
        "input_path": str(fasta), "output_path": str(translated), "frame": 1,
    })
    assert result["protein_count"] == 1
    assert translated.exists()

    extracted = tmp_path / "region.fasta"
    result = _run("sequence", "sequence_subsequence_extract", {
        "input_path": str(fasta), "sequence_id": "gene1", "start": 4,
        "end": 12, "strand": "+", "output_path": str(extracted),
    })
    assert result["length"] == 9
    assert "ATGAAATAG" in extracted.read_text()


def test_vcf_sample_qc_frequency_titv_and_matrix_tools(tmp_path: Path):
    vcf = tmp_path / "sample.vcf"
    vcf.write_text(
        "##fileformat=VCFv4.2\n"
        "#CHROM\tPOS\tID\tREF\tALT\tQUAL\tFILTER\tINFO\tFORMAT\ts1\ts2\n"
        "chr1\t10\trs1\tA\tG\t60\tPASS\t.\tGT\t0/1\t1/1\n"
        "chr1\t20\trs2\tC\tA\t50\tPASS\t.\tGT\t0/0\t./.\n",
        encoding="utf-8",
    )

    qc = _run("genome_annotation", "vcf_sample_qc", {"input_path": str(vcf)})
    assert qc["samples"][0]["call_rate"] == 1
    assert qc["samples"][1]["missing_rate"] == 0.5

    frequency = _run("genome_annotation", "vcf_allele_frequency", {"input_path": str(vcf)})
    assert frequency["variants"][0]["alternate_allele_frequency"] == 0.75

    titv = _run("genome_annotation", "vcf_titv_profile", {"input_path": str(vcf)})
    assert titv["transitions"] == 1
    assert titv["transversions"] == 1

    matrix = tmp_path / "genotypes.csv"
    exported = _run("genome_annotation", "vcf_genotype_matrix_export", {
        "input_path": str(vcf), "output_path": str(matrix), "encoding": "dosage",
    })
    assert exported["sample_count"] == 2
    assert "variant_id,chromosome,position,ref,alt,s1,s2" in matrix.read_text(encoding="utf-8-sig")
