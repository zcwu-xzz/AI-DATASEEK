from __future__ import annotations

import base64
import csv
import gzip
import json
import math
import random
import re
import shutil
import sys
from collections import Counter, defaultdict
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator

import numpy as np
from Bio import AlignIO, SeqIO
from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord
from Bio.SeqUtils.ProtParam import ProteinAnalysis


def fail(message: str) -> None:
    print(json.dumps({"success": False, "error": message}, ensure_ascii=False))
    raise SystemExit(1)


def emit(data: dict) -> None:
    print(json.dumps({"success": True, **data}, ensure_ascii=False, default=_json_default))


def _json_default(value):
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(type(value).__name__)


def ensure_parent(path: str | Path) -> Path:
    result = Path(path)
    result.parent.mkdir(parents=True, exist_ok=True)
    return result


def sequence_format(path: str | Path) -> str:
    name = str(path).lower()
    return "fastq" if any(token in name for token in (".fastq", ".fq")) else "fasta"


@contextmanager
def open_text(path: str | Path):
    target = Path(path)
    if not target.is_file():
        fail(f"文件不存在: {target.name}")
    handle = gzip.open(target, "rt", encoding="utf-8", errors="replace") if target.suffix.lower() == ".gz" else target.open("r", encoding="utf-8", errors="replace")
    try:
        yield handle
    finally:
        handle.close()


def iter_records(path: str | Path) -> Iterator[SeqRecord]:
    with open_text(path) as handle:
        yield from SeqIO.parse(handle, sequence_format(path))


def write_records(records: Iterable[SeqRecord], path: str | Path, fmt: str | None = None) -> int:
    target = ensure_parent(path)
    output_format = fmt or sequence_format(target)
    with target.open("w", encoding="utf-8") as handle:
        return SeqIO.write(records, handle, output_format)


def read_name(record: SeqRecord) -> str:
    return re.sub(r"(?:/1|/2)$", "", record.id.split()[0])


def qmean(record: SeqRecord) -> float:
    values = record.letter_annotations.get("phred_quality", [])
    return float(np.mean(values)) if values else 0.0


def trimmed(record: SeqRecord, min_quality: int, window: int, poly_x_min: int = 0) -> SeqRecord:
    qualities = record.letter_annotations.get("phred_quality", [])
    left, right = 0, len(record)
    while left < right and qualities and qualities[left] < min_quality:
        left += 1
    while right > left and qualities and qualities[right - 1] < min_quality:
        right -= 1
    window = max(1, window)
    while right - left >= window and qualities and np.mean(qualities[right - window:right]) < min_quality:
        right -= 1
    sequence = str(record.seq[left:right]).upper()
    if poly_x_min > 0:
        match = re.search(rf"([ACGTN])\1{{{poly_x_min - 1},}}$", sequence)
        if match:
            right = left + match.start()
    return record[left:right]


def basic_metrics(path: str | Path) -> dict:
    count = bases = gc = n_count = quality_bases = 0
    lengths: list[int] = []
    quality_sum = 0
    for record in iter_records(path):
        sequence = str(record.seq).upper()
        length = len(sequence)
        count += 1
        bases += length
        gc += sequence.count("G") + sequence.count("C")
        n_count += sequence.count("N")
        lengths.append(length)
        qualities = record.letter_annotations.get("phred_quality", [])
        quality_sum += sum(qualities)
        quality_bases += len(qualities)
    ordered = sorted(lengths, reverse=True)
    cumulative = 0
    n50 = 0
    for length in ordered:
        cumulative += length
        if cumulative >= bases / 2:
            n50 = length
            break
    return {
        "records": count,
        "total_bases": bases,
        "min_length": min(lengths) if lengths else 0,
        "max_length": max(lengths) if lengths else 0,
        "mean_length": bases / count if count else 0,
        "n50": n50,
        "gc_percent": gc / bases * 100 if bases else 0,
        "n_percent": n_count / bases * 100 if bases else 0,
        "mean_quality": quality_sum / quality_bases if quality_bases else None,
    }


def nx(lengths: list[int], fraction: float, denominator: int | None = None) -> tuple[int, int]:
    total = denominator or sum(lengths)
    cumulative = 0
    for index, length in enumerate(sorted(lengths, reverse=True), 1):
        cumulative += length
        if cumulative >= total * fraction:
            return length, index
    return 0, 0


def fasta_index_query(args: dict) -> dict:
    import pysam

    source = Path(args["input_path"])
    prepared = ensure_parent(args["output_path"])
    shutil.copyfile(source, prepared)
    pysam.faidx(str(prepared))
    result = {"output_path": str(prepared), "index_path": f"{prepared}.fai", "attachments": [str(prepared), f"{prepared}.fai"]}
    sequence_id = args.get("sequence_id")
    if sequence_id:
        with pysam.FastaFile(str(prepared)) as fasta:
            if sequence_id not in fasta.references:
                fail(f"未找到序列标识: {sequence_id}")
            start = max(1, int(args.get("start", 1)))
            end = int(args.get("end") or fasta.get_reference_length(sequence_id))
            result.update({"sequence_id": sequence_id, "start": start, "end": end, "sequence": fasta.fetch(sequence_id, start - 1, end)})
    return result


def fasta_id_subset(args: dict) -> dict:
    ids = {str(item).strip() for item in args.get("ids", []) if str(item).strip()}
    if args.get("ids_path"):
        ids.update(line.strip().split()[0] for line in Path(args["ids_path"]).read_text(encoding="utf-8").splitlines() if line.strip())
    if not ids:
        fail("必须提供 ids 或 ids_path")
    exclude = bool(args.get("exclude", False))
    selected = (record for record in iter_records(args["input_path"]) if (record.id in ids) != exclude)
    count = write_records(selected, args["output_path"], "fasta")
    return {"output_path": args["output_path"], "records_written": count, "requested_ids": len(ids), "exclude": exclude}


def fasta_split_merge(args: dict) -> dict:
    if args["mode"] == "merge":
        inputs = args.get("input_paths") or ([args["input_path"]] if args.get("input_path") else [])
        if not inputs:
            fail("合并模式需要 input_paths")
        count = write_records((record for path in inputs for record in iter_records(path)), args["output_path"], "fasta")
        return {"output_path": args["output_path"], "records_written": count, "source_files": len(inputs)}
    source = args.get("input_path")
    if not source:
        fail("拆分模式需要 input_path")
    output_dir = Path(args["output_path"])
    output_dir.mkdir(parents=True, exist_ok=True)
    size = max(1, int(args.get("records_per_file", 1000)))
    chunk: list[SeqRecord] = []
    files: list[str] = []
    for record in iter_records(source):
        chunk.append(record)
        if len(chunk) == size:
            path = output_dir / f"part_{len(files) + 1:04d}.fasta"
            write_records(chunk, path, "fasta"); files.append(str(path)); chunk = []
    if chunk:
        path = output_dir / f"part_{len(files) + 1:04d}.fasta"
        write_records(chunk, path, "fasta"); files.append(str(path))
    manifest = output_dir / "split_manifest.json"
    manifest.write_text(
        json.dumps(
            {"source": Path(source).name, "records_per_file": size, "files": files},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {"output_path": str(manifest), "files": files, "attachments": [str(manifest), *files], "records_per_file": size}


def fasta_id_normalize(args: dict) -> dict:
    seen: Counter[str] = Counter()
    mapping: list[dict] = []
    output: list[SeqRecord] = []
    prefix = re.sub(r"\W+", "_", args.get("prefix", "sequence"), flags=re.UNICODE).strip("_") or "sequence"
    for index, record in enumerate(iter_records(args["input_path"]), 1):
        base = re.sub(r"[^A-Za-z0-9_.:-]+", "_", record.id).strip("_") or f"{prefix}_{index}"
        seen[base] += 1
        new_id = base if seen[base] == 1 else f"{base}_{seen[base]}"
        mapping.append({"original_id": record.id, "normalized_id": new_id})
        record.id = new_id; record.name = new_id; record.description = ""
        output.append(record)
    write_records(output, args["output_path"], "fasta")
    mapping_path = args.get("mapping_path") or str(Path(args["output_path"]).with_suffix(".id_mapping.csv"))
    ensure_parent(mapping_path)
    with Path(mapping_path).open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["original_id", "normalized_id"]); writer.writeheader(); writer.writerows(mapping)
    return {"output_path": args["output_path"], "mapping_path": mapping_path, "records_written": len(output), "attachments": [args["output_path"], mapping_path]}


def fasta_assembly_metrics(args: dict) -> dict:
    lengths, gc, n_count = [], 0, 0
    for record in iter_records(args["input_path"]):
        sequence = str(record.seq).upper(); lengths.append(len(sequence)); gc += sequence.count("G") + sequence.count("C"); n_count += sequence.count("N")
    total = sum(lengths); n50, l50 = nx(lengths, .5); n90, l90 = nx(lengths, .9)
    result = {"sequence_count": len(lengths), "total_length": total, "largest_sequence": max(lengths, default=0), "smallest_sequence": min(lengths, default=0), "mean_length": total / len(lengths) if lengths else 0, "N50": n50, "L50": l50, "N90": n90, "L90": l90, "GC_percent": gc / total * 100 if total else 0, "N_percent": n_count / total * 100 if total else 0}
    if args.get("genome_size"):
        result["NG50"], result["LG50"] = nx(lengths, .5, int(args["genome_size"]))
    return result


def fasta_gap_profile(args: dict) -> dict:
    minimum = max(1, int(args.get("min_gap", 1)))
    gaps: list[dict] = []; total = 0; bases = 0
    pattern = re.compile(rf"N{{{minimum},}}", re.I)
    for record in iter_records(args["input_path"]):
        sequence = str(record.seq); bases += len(sequence)
        for match in pattern.finditer(sequence):
            length = match.end() - match.start(); total += length
            gaps.append({"sequence_id": record.id, "start": match.start() + 1, "end": match.end(), "length": length})
    output = args.get("output_path")
    if output:
        ensure_parent(output)
        with Path(output).open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=["sequence_id", "start", "end", "length"]); writer.writeheader(); writer.writerows(gaps)
    return {"output_path": output, "gap_count": len(gaps), "gap_bases": total, "gap_percent": total / bases * 100 if bases else 0, "effective_bases": bases - total, "largest_gap": max((row["length"] for row in gaps), default=0), "gaps": gaps[:1000], "truncated": len(gaps) > 1000}


def fasta_window_composition(args: dict) -> dict:
    window = max(1, int(args.get("window_size", 10000))); step = max(1, int(args.get("step_size") or window))
    rows: list[dict] = []
    for record in iter_records(args["input_path"]):
        sequence = str(record.seq).upper()
        for start in range(0, len(sequence), step):
            piece = sequence[start:start + window]
            if not piece: continue
            counts = Counter(piece); entropy = -sum((value / len(piece)) * math.log2(value / len(piece)) for value in counts.values() if value)
            rows.append({"sequence_id": record.id, "start": start + 1, "end": start + len(piece), "gc_percent": (counts["G"] + counts["C"]) / len(piece) * 100, "n_percent": counts["N"] / len(piece) * 100, "gc_skew": (counts["G"] - counts["C"]) / max(counts["G"] + counts["C"], 1), "entropy": entropy})
    ensure_parent(args["output_path"])
    with Path(args["output_path"]).open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["sequence_id", "start", "end", "gc_percent", "n_percent", "gc_skew", "entropy"]); writer.writeheader(); writer.writerows(rows)
    return {"output_path": args["output_path"], "window_count": len(rows), "window_size": window, "step_size": step}


def fasta_msa_profile(args: dict) -> dict:
    alignment = AlignIO.read(args["input_path"], "fasta")
    if len(alignment) < 2:
        fail("多序列比对至少需要两条序列")
    length = alignment.get_alignment_length(); consensus = []; positions = []
    for index in range(length):
        column = [char.upper() for char in alignment[:, index]]; non_gap = [char for char in column if char not in "-."]
        counts = Counter(non_gap); base, count = counts.most_common(1)[0] if counts else ("N", 0); consensus.append(base)
        entropy = -sum((value / len(non_gap)) * math.log2(value / len(non_gap)) for value in counts.values()) if non_gap else 0
        positions.append({"position": index + 1, "consensus": base, "conservation": count / len(non_gap) if non_gap else 0, "gap_rate": 1 - len(non_gap) / len(column), "entropy": entropy, "variant": len(counts) > 1})
    output = args.get("output_path")
    if output:
        ensure_parent(output)
        with Path(output).open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(positions[0])); writer.writeheader(); writer.writerows(positions)
    return {"output_path": output, "sequence_count": len(alignment), "alignment_length": length, "consensus": "".join(consensus), "variant_positions": sum(row["variant"] for row in positions), "mean_conservation": float(np.mean([row["conservation"] for row in positions])) if positions else 0}


def parse_attributes(value: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in value.strip().strip(";").split(";"):
        item = item.strip()
        if not item: continue
        if "=" in item: key, val = item.split("=", 1)
        elif " " in item: key, val = item.split(" ", 1)
        else: continue
        result[key.strip()] = val.strip().strip('"')
    return result


def fasta_annotation_extract(args: dict) -> dict:
    references = SeqIO.to_dict(iter_records(args["fasta_path"])); wanted = args.get("feature_type", "CDS").lower(); upstream = max(0, int(args.get("upstream", 0)))
    output: list[SeqRecord] = []; missing = 0
    groups: dict[str, list[tuple[str, int, int, str, int, dict[str, str]]]] = defaultdict(list)
    with open_text(args["annotation_path"]) as handle:
        for line in handle:
            if not line.strip() or line.startswith("#"): continue
            fields = line.rstrip().split("\t")
            if len(fields) < 9 or fields[2].lower() != wanted: continue
            chrom, start, end, strand = fields[0], int(fields[3]), int(fields[4]), fields[6]
            if chrom not in references: missing += 1; continue
            attrs = parse_attributes(fields[8]); identifier = attrs.get("ID") or attrs.get("transcript_id") or attrs.get("gene_id") or f"{chrom}_{start}_{end}_{strand}"
            parent = attrs.get("Parent") or attrs.get("transcript_id") or identifier
            phase = int(fields[7]) if fields[7] in {"0", "1", "2"} else 0
            groups[parent if wanted == "cds" else identifier].append((chrom, start, end, strand, phase, attrs))
    for identifier, segments in groups.items():
        chrom, strand = segments[0][0], segments[0][3]
        if upstream:
            left = min(item[1] for item in segments); right = max(item[2] for item in segments)
            start, end = (max(1, left - upstream), left - 1) if strand != "-" else (right + 1, min(len(references[chrom]), right + upstream))
            sequence = references[chrom].seq[start - 1:end]
            if strand == "-": sequence = sequence.reverse_complement()
            description = f"{chrom}:{start}-{end}({strand}) promoter"
        else:
            ordered = sorted(segments, key=lambda item: item[1], reverse=strand == "-")
            pieces = []
            for _chrom, start, end, _strand, phase, _attrs in ordered:
                piece = references[chrom].seq[start - 1:end]
                if strand == "-": piece = piece.reverse_complement()
                if wanted == "cds" and phase:
                    piece = piece[phase:]
                pieces.append(piece)
            sequence = sum(pieces, Seq(""))
            description = f"{chrom}:{min(item[1] for item in segments)}-{max(item[2] for item in segments)}({strand}) {wanted}; segments={len(segments)}"
        output.append(SeqRecord(sequence, id=identifier, description=description))
    count = write_records(output, args["output_path"], "fasta")
    return {"output_path": args["output_path"], "records_written": count, "missing_reference_features": missing, "feature_type": wanted}


def protein_fasta_properties(args: dict) -> dict:
    rows: list[dict] = []
    for record in iter_records(args["input_path"]):
        sequence = re.sub(r"[^ACDEFGHIKLMNPQRSTVWY]", "", str(record.seq).upper())
        if not sequence: continue
        analysis = ProteinAnalysis(sequence)
        rows.append({"sequence_id": record.id, "length": len(sequence), "molecular_weight": analysis.molecular_weight(), "isoelectric_point": analysis.isoelectric_point(), "aromaticity": analysis.aromaticity(), "instability_index": analysis.instability_index(), "gravy": analysis.gravy(), "amino_acid_percent": analysis.amino_acids_percent})
    output = args.get("output_path")
    if output: ensure_parent(output).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"output_path": output, "protein_count": len(rows), "proteins": rows[:1000], "truncated": len(rows) > 1000}


COMMON_ADAPTERS = {
    "Illumina universal": "AGATCGGAAGAGC",
    "Nextera transposase": "CTGTCTCTTATACACATCT",
    "small RNA 3prime": "TGGAATTCTCGGGTGCCAAGG",
    "BGI": "AAGTCGGAGGCCAAGCGGTCTTAGGAAGACAA",
}


def fastq_adapter_detect(args: dict) -> dict:
    limit = max(1, int(args.get("max_reads", 200000))); k = max(6, min(31, int(args.get("k", 12))))
    tails: Counter[str] = Counter(); common: Counter[str] = Counter(); processed = 0
    for record in iter_records(args["input_path"]):
        sequence = str(record.seq).upper(); processed += 1
        if len(sequence) >= k: tails[sequence[-k:]] += 1
        for name, adapter in COMMON_ADAPTERS.items():
            if adapter[:min(k, len(adapter))] in sequence: common[name] += 1
        if processed >= limit: break
    candidates = [{"sequence": sequence, "count": count, "fraction": count / processed} for sequence, count in tails.most_common(30) if count / processed >= .001]
    return {"reads_sampled": processed, "k": k, "known_adapters": [{"name": name, "hits": count, "fraction": count / processed} for name, count in common.most_common()], "candidate_tail_kmers": candidates}


def quality_trim_file(input_path: str, output_path: str, min_quality: int, window: int, min_length: int, max_n: float, poly_x: int = 0) -> dict:
    total = kept = bases_before = bases_after = 0
    def selected():
        nonlocal total, kept, bases_before, bases_after
        for record in iter_records(input_path):
            total += 1; bases_before += len(record)
            result = trimmed(record, min_quality, window, poly_x)
            if len(result) >= min_length and str(result.seq).upper().count("N") / max(len(result), 1) <= max_n:
                kept += 1; bases_after += len(result); yield result
    write_records(selected(), output_path, "fastq")
    return {"total_reads": total, "kept_reads": kept, "discarded_reads": total - kept, "retention_rate": kept / total if total else 0, "bases_before": bases_before, "bases_after": bases_after}


def fastq_quality_trim(args: dict) -> dict:
    stats = quality_trim_file(args["input_path"], args["output_path"], int(args.get("min_quality", 20)), int(args.get("window_size", 4)), int(args.get("min_length", 30)), float(args.get("max_n_fraction", .1)), int(args.get("poly_x_min", 10)))
    return {"output_path": args["output_path"], **stats}


def fastq_paired_clean(args: dict) -> dict:
    total = paired = orphans = 0; orphan_records: list[SeqRecord] = []
    out1: list[SeqRecord] = []; out2: list[SeqRecord] = []
    minimum = int(args.get("min_length", 30)); quality = int(args.get("min_quality", 20))
    for first, second in zip(iter_records(args["read1_path"]), iter_records(args["read2_path"])):
        total += 1
        if read_name(first) != read_name(second): fail(f"双端名称不同步: {first.id} / {second.id}")
        a, b = trimmed(first, quality, 4, 10), trimmed(second, quality, 4, 10)
        good_a, good_b = len(a) >= minimum, len(b) >= minimum
        if good_a and good_b: out1.append(a); out2.append(b); paired += 1
        else:
            if good_a: orphan_records.append(a); orphans += 1
            if good_b: orphan_records.append(b); orphans += 1
    write_records(out1, args["output_read1"], "fastq"); write_records(out2, args["output_read2"], "fastq")
    attachments = [args["output_read1"], args["output_read2"]]
    if args.get("orphan_path"): write_records(orphan_records, args["orphan_path"], "fastq"); attachments.append(args["orphan_path"])
    return {"output_path": args["output_read1"], "output_read2": args["output_read2"], "attachments": attachments, "input_pairs": total, "retained_pairs": paired, "orphan_reads": orphans}


def fastq_pair_repair(args: dict) -> dict:
    maximum = max(1, int(args.get("max_records", 2000000)))
    right: dict[str, SeqRecord] = {}
    for index, record in enumerate(iter_records(args["read2_path"])):
        if index >= maximum: fail("R2 超过 max_records，避免无界内存使用")
        right[read_name(record)] = record
    paired1: list[SeqRecord] = []; paired2: list[SeqRecord] = []; orphan: list[SeqRecord] = []
    for first in iter_records(args["read1_path"]):
        second = right.pop(read_name(first), None)
        if second is None: orphan.append(first)
        else: paired1.append(first); paired2.append(second)
    orphan.extend(right.values())
    write_records(paired1, args["output_read1"], "fastq"); write_records(paired2, args["output_read2"], "fastq"); write_records(orphan, args["orphan_path"], "fastq")
    return {"output_path": args["output_read1"], "attachments": [args["output_read1"], args["output_read2"], args["orphan_path"]], "repaired_pairs": len(paired1), "orphan_reads": len(orphan)}


def fastq_interleave_convert(args: dict) -> dict:
    if args["mode"] == "interleave":
        if not all(args.get(key) for key in ("read1_path", "read2_path", "output_path")): fail("交错模式需要 read1_path、read2_path 和 output_path")
        def records():
            for first, second in zip(iter_records(args["read1_path"]), iter_records(args["read2_path"])):
                if read_name(first) != read_name(second): fail("双端名称不同步")
                yield first; yield second
        count = write_records(records(), args["output_path"], "fastq")
        return {"output_path": args["output_path"], "records_written": count, "pairs": count // 2}
    if not all(args.get(key) for key in ("input_path", "output_read1", "output_read2")): fail("拆分模式需要 input_path、output_read1 和 output_read2")
    first, second = [], []
    for index, record in enumerate(iter_records(args["input_path"])): (first if index % 2 == 0 else second).append(record)
    if len(first) != len(second): fail("交错 FASTQ 的记录数不是偶数")
    write_records(first, args["output_read1"], "fastq"); write_records(second, args["output_read2"], "fastq")
    return {"output_path": args["output_read1"], "attachments": [args["output_read1"], args["output_read2"]], "pairs": len(first)}


def merge_pair(first: SeqRecord, second: SeqRecord, minimum: int, mismatch_rate: float) -> SeqRecord | None:
    right = str(second.seq.reverse_complement()).upper(); left = str(first.seq).upper()
    q1 = first.letter_annotations.get("phred_quality", [40] * len(first)); q2 = list(reversed(second.letter_annotations.get("phred_quality", [40] * len(second))))
    for overlap in range(min(len(left), len(right)), minimum - 1, -1):
        a, b = left[-overlap:], right[:overlap]; mismatches = sum(x != y for x, y in zip(a, b))
        if mismatches / overlap > mismatch_rate: continue
        merged_seq = left[:-overlap] + "".join(x if x == y or q1[len(left)-overlap+i] >= q2[i] else y for i, (x, y) in enumerate(zip(a, b))) + right[overlap:]
        merged_q = q1[:-overlap] + [max(q1[len(left)-overlap+i], q2[i]) for i in range(overlap)] + q2[overlap:]
        result = SeqRecord(Seq(merged_seq), id=read_name(first), description="merged_pair"); result.letter_annotations["phred_quality"] = merged_q; return result
    return None


def fastq_pair_merge(args: dict) -> dict:
    merged: list[SeqRecord] = []; left: list[SeqRecord] = []; right: list[SeqRecord] = []
    for first, second in zip(iter_records(args["read1_path"]), iter_records(args["read2_path"])):
        if read_name(first) != read_name(second): fail("双端名称不同步")
        result = merge_pair(first, second, int(args.get("min_overlap", 20)), float(args.get("max_mismatch_rate", .1)))
        if result is None: left.append(first); right.append(second)
        else: merged.append(result)
    write_records(merged, args["output_path"], "fastq"); attachments = [args["output_path"]]
    if args.get("unmerged_read1"): write_records(left, args["unmerged_read1"], "fastq"); attachments.append(args["unmerged_read1"])
    if args.get("unmerged_read2"): write_records(right, args["unmerged_read2"], "fastq"); attachments.append(args["unmerged_read2"])
    return {"output_path": args["output_path"], "attachments": attachments, "merged_pairs": len(merged), "unmerged_pairs": len(left), "merge_rate": len(merged) / max(len(merged) + len(left), 1)}


def hamming(a: str, b: str) -> int:
    return sum(x != y for x, y in zip(a, b)) + abs(len(a) - len(b))


def fastq_demultiplex(args: dict) -> dict:
    output_dir = Path(args["output_dir"]); output_dir.mkdir(parents=True, exist_ok=True); barcodes = {str(name): str(code).upper() for name, code in args["barcodes"].items()}; maximum = int(args.get("max_mismatches", 0)); position = args.get("barcode_position", "start")
    handles = {name: (output_dir / f"{name}.fastq").open("w", encoding="utf-8") for name in barcodes}; unmatched = (output_dir / "unmatched.fastq").open("w", encoding="utf-8"); counts = Counter()
    try:
        for record in iter_records(args["input_path"]):
            sequence = str(record.seq).upper(); candidates = []
            for name, barcode in barcodes.items():
                observed = sequence[:len(barcode)] if position == "start" else sequence[-len(barcode):]
                distance = hamming(observed, barcode)
                if distance <= maximum: candidates.append((distance, name, len(barcode)))
            if candidates:
                _, name, length = min(candidates); trimmed_record = record[length:] if position == "start" else record[:-length]; SeqIO.write(trimmed_record, handles[name], "fastq"); counts[name] += 1
            else: SeqIO.write(record, unmatched, "fastq"); counts["unmatched"] += 1
    finally:
        for handle in handles.values(): handle.close()
        unmatched.close()
    files = [str(output_dir / f"{name}.fastq") for name in barcodes] + [str(output_dir / "unmatched.fastq")]
    return {"output_path": str(output_dir / "unmatched.fastq"), "attachments": files, "sample_counts": dict(counts), "files": files}


def fastq_umi_extract(args: dict) -> dict:
    length = max(1, int(args["umi_length"])); source = args.get("source", "sequence_start"); count = 0
    def records():
        nonlocal count
        for record in iter_records(args["input_path"]):
            if source == "name":
                match = re.search(rf"([ACGTN]{{{length}}})", record.description.upper()); umi = match.group(1) if match else "N" * length; result = record
            elif source == "sequence_end": umi = str(record.seq[-length:]); result = record[:-length]
            else: umi = str(record.seq[:length]); result = record[length:]
            result.id = f"{read_name(record)}:UMI_{umi}"; result.name = result.id; result.description = ""; count += 1; yield result
    write_records(records(), args["output_path"], "fastq")
    return {"output_path": args["output_path"], "records_written": count, "umi_length": length, "source": source}


def fastq_downsample(args: dict) -> dict:
    fraction = args.get("fraction"); target = args.get("target_reads"); rng = random.Random(int(args.get("seed", 42)))
    if fraction is None and target is None: fail("必须提供 fraction 或 target_reads")
    paired = bool(args.get("other_path"))
    if paired and not args.get("other_output_path"): fail("双端降采样需要 other_output_path")
    source = zip(iter_records(args["input_path"]), iter_records(args["other_path"])) if paired else ((record, None) for record in iter_records(args["input_path"]))
    selected: list[tuple[SeqRecord, SeqRecord | None]] = []
    total = 0
    if target is not None:
        target = max(0, int(target))
        if target > 5_000_000: fail("target_reads 不能超过 5,000,000，避免无界内存使用")
        for total, pair in enumerate(source, 1):
            if paired and read_name(pair[0]) != read_name(pair[1]): fail("双端名称不同步")
            if len(selected) < target: selected.append(pair)
            else:
                slot = rng.randrange(total)
                if slot < target: selected[slot] = pair
    else:
        fraction = float(fraction)
        if not 0 <= fraction <= 1: fail("fraction 必须在 0 到 1 之间")
        for total, pair in enumerate(source, 1):
            if paired and read_name(pair[0]) != read_name(pair[1]): fail("双端名称不同步")
            if rng.random() < fraction: selected.append(pair)
    first = [pair[0] for pair in selected]; second = [pair[1] for pair in selected if pair[1] is not None]
    write_records(first, args["output_path"], "fastq"); attachments = [args["output_path"]]
    if second:
        write_records(second, args["other_output_path"], "fastq"); attachments.append(args["other_output_path"])
    return {"output_path": args["output_path"], "attachments": attachments, "input_reads_or_pairs": total, "selected_reads_or_pairs": len(first), "fraction": len(first) / total if total else 0, "seed": int(args.get("seed", 42))}


def fastq_overrepresented_sequences(args: dict) -> dict:
    limit = int(args.get("max_reads", 500000)); top = int(args.get("top_n", 50)); sequences: Counter[str] = Counter(); prefixes: Counter[str] = Counter(); suffixes: Counter[str] = Counter(); count = 0
    for record in iter_records(args["input_path"]):
        sequence = str(record.seq).upper(); sequences[sequence] += 1; prefixes[sequence[:12]] += 1; suffixes[sequence[-12:]] += 1; count += 1
        if count >= limit: break
    duplicate_reads = sum(value for value in sequences.values() if value > 1)
    return {"reads_sampled": count, "unique_sequences": len(sequences), "duplicate_read_fraction": duplicate_reads / count if count else 0, "overrepresented_sequences": [{"sequence": key, "count": value, "fraction": value / count} for key, value in sequences.most_common(top)], "prefixes": prefixes.most_common(top), "suffixes": suffixes.most_common(top)}


def fastq_contamination_screen(args: dict) -> dict:
    k = max(8, min(31, int(args.get("k", 21)))); threshold = max(1, int(args.get("min_matches", 2))); index: dict[str, set[str]] = defaultdict(set)
    for reference in iter_records(args["reference_path"]):
        sequence = str(reference.seq).upper()
        for pos in range(0, len(sequence) - k + 1, max(1, k // 2)): index[sequence[pos:pos+k]].add(reference.id)
    total = contaminated = 0; sources = Counter(); selected: list[SeqRecord] = []
    for record in iter_records(args["input_path"]):
        total += 1; hits = Counter()
        sequence = str(record.seq).upper()
        for pos in range(0, len(sequence) - k + 1, max(1, k // 2)):
            for reference in index.get(sequence[pos:pos+k], ()): hits[reference] += 1
        if hits and hits.most_common(1)[0][1] >= threshold: contaminated += 1; sources[hits.most_common(1)[0][0]] += 1; selected.append(record)
    if args.get("output_path"): write_records(selected, args["output_path"], "fastq")
    return {"output_path": args.get("output_path"), "total_reads": total, "contaminated_reads": contaminated, "contamination_rate": contaminated / total if total else 0, "matched_references": dict(sources), "k": k, "min_matches": threshold}


def fastq_long_read_qc(args: dict) -> dict:
    lengths: list[int] = []; qualities: list[float] = []
    for record in iter_records(args["input_path"]): lengths.append(len(record)); qualities.append(qmean(record))
    total = sum(lengths); n50, l50 = nx(lengths, .5); result = {"read_count": len(lengths), "total_bases": total, "read_N50": n50, "read_L50": l50, "mean_length": float(np.mean(lengths)) if lengths else 0, "median_length": float(np.median(lengths)) if lengths else 0, "max_length": max(lengths, default=0), "mean_quality": float(np.mean(qualities)) if qualities else 0, "quality_length_correlation": float(np.corrcoef(lengths, qualities)[0,1]) if len(lengths) > 1 and np.std(lengths) and np.std(qualities) else None}
    if args.get("output_path"):
        import plotly.express as px
        figure = px.scatter(x=lengths[:200000], y=qualities[:200000], labels={"x":"读长 (bp)","y":"平均 Phred 质量"}, title="长读长质量与长度关系", opacity=.45)
        figure.write_html(args["output_path"], include_plotlyjs="inline", full_html=True); result.update({"output_path": args["output_path"], "interactive_output_path": args["output_path"]})
    return result


def fastq_qc_clean_workflow(args: dict) -> dict:
    output_dir = Path(args["output_dir"]); output_dir.mkdir(parents=True, exist_ok=True); before = basic_metrics(args["input_path"]); attachments: list[str] = []
    if args.get("other_path"):
        clean1, clean2 = output_dir / "clean_R1.fastq", output_dir / "clean_R2.fastq"
        result = fastq_paired_clean({"read1_path": args["input_path"], "read2_path": args["other_path"], "output_read1": str(clean1), "output_read2": str(clean2), "orphan_path": str(output_dir / "orphans.fastq"), "min_quality": args.get("min_quality",20), "min_length": args.get("min_length",30)})
        attachments.extend(result["attachments"]); after = basic_metrics(clean1)
    else:
        clean1 = output_dir / "clean.fastq"; result = quality_trim_file(args["input_path"], str(clean1), int(args.get("min_quality",20)), 4, int(args.get("min_length",30)), .1, 10); attachments.append(str(clean1)); after = basic_metrics(clean1)
    report = output_dir / "fastq_qc_report.json"; payload = {"before": before, "after": after, "cleaning": result}; report.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"); attachments.append(str(report))
    import plotly.graph_objects as go
    html = output_dir / "fastq_qc_dashboard.html"; figure = go.Figure([go.Bar(name="清洗前", x=["读段数","碱基数","平均读长","平均质量"], y=[before["records"],before["total_bases"],before["mean_length"],before["mean_quality"] or 0]), go.Bar(name="清洗后", x=["读段数","碱基数","平均读长","平均质量"], y=[after["records"],after["total_bases"],after["mean_length"],after["mean_quality"] or 0])]); figure.update_layout(barmode="group", title="FASTQ 清洗前后质量对比"); figure.write_html(html, include_plotlyjs="inline", full_html=True); attachments.append(str(html))
    return {"output_path": str(report), "interactive_output_path": str(html), "attachments": attachments, **payload}


def reference_prepare_workflow(args: dict) -> dict:
    import pysam

    output_dir = Path(args["output_dir"]); output_dir.mkdir(parents=True, exist_ok=True); prepared = output_dir / "reference.fasta"
    if args.get("normalize_ids"):
        normalize = fasta_id_normalize({"input_path": args["input_path"], "output_path": str(prepared), "mapping_path": str(output_dir / "id_mapping.csv")}); attachments = [normalize["mapping_path"]]
    else: shutil.copyfile(args["input_path"], prepared); attachments = []
    pysam.faidx(str(prepared)); dictionary = output_dir / "reference.dict"
    with pysam.FastaFile(str(prepared)) as fasta, dictionary.open("w", encoding="utf-8") as handle:
        handle.write("@HD\tVN:1.6\tSO:unsorted\n")
        for name, length in zip(fasta.references, fasta.lengths): handle.write(f"@SQ\tSN:{name}\tLN:{length}\n")
    report = output_dir / "reference_report.json"; metrics = fasta_assembly_metrics({"input_path": str(prepared)}); report.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    attachments.extend([str(prepared), f"{prepared}.fai", str(dictionary), str(report)])
    return {"output_path": str(report), "attachments": attachments, "reference_path": str(prepared), "index_path": f"{prepared}.fai", "dictionary_path": str(dictionary), "metrics": metrics}


OPERATIONS = {
    "fasta_index_query": fasta_index_query,
    "fasta_id_subset": fasta_id_subset,
    "fasta_split_merge": fasta_split_merge,
    "fasta_id_normalize": fasta_id_normalize,
    "fasta_assembly_metrics": fasta_assembly_metrics,
    "fasta_gap_profile": fasta_gap_profile,
    "fasta_window_composition": fasta_window_composition,
    "fasta_msa_profile": fasta_msa_profile,
    "fasta_annotation_extract": fasta_annotation_extract,
    "protein_fasta_properties": protein_fasta_properties,
    "fastq_adapter_detect": fastq_adapter_detect,
    "fastq_quality_trim": fastq_quality_trim,
    "fastq_paired_clean": fastq_paired_clean,
    "fastq_pair_repair": fastq_pair_repair,
    "fastq_interleave_convert": fastq_interleave_convert,
    "fastq_pair_merge": fastq_pair_merge,
    "fastq_demultiplex": fastq_demultiplex,
    "fastq_umi_extract": fastq_umi_extract,
    "fastq_downsample": fastq_downsample,
    "fastq_overrepresented_sequences": fastq_overrepresented_sequences,
    "fastq_contamination_screen": fastq_contamination_screen,
    "fastq_long_read_qc": fastq_long_read_qc,
    "fastq_qc_clean_workflow": fastq_qc_clean_workflow,
    "reference_prepare_workflow": reference_prepare_workflow,
}


def main() -> None:
    if len(sys.argv) != 3:
        fail("参数错误")
    try:
        payload = sys.argv[2] + "=" * (-len(sys.argv[2]) % 4)
        args = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
        operation = OPERATIONS[sys.argv[1]]
        emit(operation(args))
    except SystemExit:
        raise
    except Exception as exc:
        fail(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
