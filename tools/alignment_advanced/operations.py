from __future__ import annotations

import base64
import csv
import json
import math
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Iterator

import numpy as np
import pysam


MAX_REGION_BASES = 5_000_000
MAX_REFERENCE_CHUNK = 1_000_000


def fail(message: str) -> None:
    print(json.dumps({"success": False, "error": message}, ensure_ascii=False))
    raise SystemExit(1)


def emit(data: dict) -> None:
    print(json.dumps({"success": True, **data}, ensure_ascii=False, default=json_default))


def json_default(value):
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(type(value).__name__)


def ensure_parent(path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def alignment_mode(path: str | Path, write: bool = False) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".bam":
        return "wb" if write else "rb"
    if suffix == ".cram":
        return "wc" if write else "rc"
    return "w" if write else "r"


def open_alignment(path: str | Path, *, write: bool = False, template=None, header=None, reference_path: str | None = None):
    kwargs = {}
    if reference_path:
        kwargs["reference_filename"] = reference_path
    if write:
        if template is not None:
            kwargs["template"] = template
        elif header is not None:
            kwargs["header"] = header
    return pysam.AlignmentFile(str(path), alignment_mode(path, write), **kwargs)


def write_json(path: str | Path, value) -> str:
    target = ensure_parent(path)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=json_default), encoding="utf-8")
    return str(target)


def write_csv(path: str | Path, rows: list[dict], fields: list[str] | None = None) -> str:
    target = ensure_parent(path)
    columns = fields or (list(rows[0]) if rows else [])
    with target.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return str(target)


def iter_all(alignment) -> Iterator:
    yield from alignment.fetch(until_eof=True)


def iter_region(alignment, reference: str, start: int, end: int) -> Iterator:
    try:
        if alignment.has_index():
            yield from alignment.fetch(reference, start, end)
            return
    except (ValueError, OSError):
        pass
    tid = alignment.get_tid(reference)
    if tid < 0:
        fail(f"参考序列不存在: {reference}")
    for read in iter_all(alignment):
        if read.is_unmapped or read.reference_id != tid:
            continue
        read_end = read.reference_end or read.reference_start + 1
        if read_end > start and read.reference_start < end:
            yield read


def index_path(path: str | Path) -> str:
    suffix = Path(path).suffix.lower()
    return f"{path}.crai" if suffix == ".cram" else f"{path}.bai"


def alignment_format_convert(args: dict) -> dict:
    source_path, output_path = args["input_path"], ensure_parent(args["output_path"])
    reference = args.get("reference_path")
    count = 0
    with open_alignment(source_path, reference_path=reference) as source:
        with open_alignment(output_path, write=True, template=source, reference_path=reference) as output:
            for read in iter_all(source):
                output.write(read); count += 1
    return {"output_path": str(output_path), "records_written": count, "source_format": Path(source_path).suffix.lower().lstrip("."), "target_format": output_path.suffix.lower().lstrip(".")}


def alignment_sort(args: dict) -> dict:
    output = ensure_parent(args["output_path"])
    command = ["-o", str(output)]
    if args.get("order", "coordinate") == "queryname": command.insert(0, "-n")
    if args.get("reference_path"): command.extend(["--reference", args["reference_path"]])
    command.append(args["input_path"])
    pysam.sort(*command)
    return {"output_path": str(output), "order": args.get("order", "coordinate")}


def alignment_index(args: dict) -> dict:
    source, output = Path(args["input_path"]), ensure_parent(args["output_path"])
    if source.resolve() != output.resolve():
        shutil.copyfile(source, output)
    explicit_index = args.get("index_path")
    if explicit_index:
        ensure_parent(explicit_index)
        pysam.index("-o", explicit_index, str(output))
        created = explicit_index
    else:
        pysam.index(str(output))
        created = index_path(output)
    with open_alignment(output, reference_path=args.get("reference_path")) as alignment:
        if not alignment.has_index(): fail("索引生成后仍无法随机访问")
    return {"output_path": str(output), "index_path": str(created), "attachments": [str(output), str(created)]}


def flagstat_data(input_path: str, reference_path: str | None = None) -> dict:
    names = ["total", "primary", "secondary", "supplementary", "duplicates", "mapped", "paired", "read1", "read2", "properly_paired", "mate_mapped", "singletons"]
    counts = Counter()
    mapq: list[int] = []
    nm_total = nm_count = 0
    with open_alignment(input_path, reference_path=reference_path) as alignment:
        for read in iter_all(alignment):
            counts["total"] += 1
            if read.is_secondary: counts["secondary"] += 1
            elif read.is_supplementary: counts["supplementary"] += 1
            else: counts["primary"] += 1
            if read.is_duplicate: counts["duplicates"] += 1
            if read.is_unmapped: continue
            counts["mapped"] += 1; mapq.append(int(read.mapping_quality))
            if read.is_paired: counts["paired"] += 1
            if read.is_read1: counts["read1"] += 1
            if read.is_read2: counts["read2"] += 1
            if read.is_proper_pair: counts["properly_paired"] += 1
            if read.is_paired and not read.mate_is_unmapped: counts["mate_mapped"] += 1
            if read.is_paired and read.mate_is_unmapped: counts["singletons"] += 1
            if read.has_tag("NM"): nm_total += int(read.get_tag("NM")); nm_count += 1
    result = {name: counts[name] for name in names}
    result.update({"mapped_percent": counts["mapped"] / counts["total"] * 100 if counts["total"] else 0, "duplicate_percent": counts["duplicates"] / counts["total"] * 100 if counts["total"] else 0, "mean_mapq": float(np.mean(mapq)) if mapq else None, "mean_nm": nm_total / nm_count if nm_count else None, "scope": "full_file"})
    return result


def alignment_flagstat(args: dict) -> dict:
    result = flagstat_data(args["input_path"], args.get("reference_path"))
    if args.get("output_path"): result["output_path"] = write_json(args["output_path"], result)
    return result


def alignment_reference_counts(args: dict) -> dict:
    counts = Counter(); unmapped = total = 0
    with open_alignment(args["input_path"], reference_path=args.get("reference_path")) as alignment:
        lengths = dict(zip(alignment.references, alignment.lengths))
        for read in iter_all(alignment):
            total += 1
            if read.is_unmapped: unmapped += 1
            else: counts[alignment.get_reference_name(read.reference_id)] += 1
    rows = [{"reference": name, "length": int(length), "aligned_records": counts[name]} for name, length in lengths.items()]
    output = write_csv(args["output_path"], rows) if args.get("output_path") else None
    return {"output_path": output, "references": rows, "unmapped_records": unmapped, "total_records": total, "scope": "full_file"}


def _depth_chunks(alignment, reference: str, length: int, min_mapq: int):
    callback = lambda read: (not read.is_unmapped) and read.mapping_quality >= min_mapq and not read.is_secondary and not read.is_supplementary
    for start in range(0, length, MAX_REFERENCE_CHUNK):
        end = min(length, start + MAX_REFERENCE_CHUNK)
        arrays = alignment.count_coverage(reference, start, end, quality_threshold=0, read_callback=callback)
        yield start, np.sum(np.asarray(arrays, dtype=np.uint32), axis=0)


def alignment_coverage_accurate(args: dict) -> dict:
    minimum = max(0, int(args.get("min_mapq", 0))); thresholds = sorted({max(0, int(item)) for item in args.get("thresholds", [1, 10, 20, 30])})
    requested = args.get("reference"); rows = []; totals = Counter(); low_regions: list[dict] = []
    with open_alignment(args["input_path"], reference_path=args.get("reference_path")) as alignment:
        references = [(name, int(length)) for name, length in zip(alignment.references, alignment.lengths) if not requested or name == requested]
        if requested and not references: fail(f"参考序列不存在: {requested}")
        if not alignment.has_index(): fail("精确覆盖度需要 BAM/CRAM 索引；请先调用 alignment_index")
        for name, length in references:
            covered = 0; threshold_counts = Counter(); depth_sum = max_depth = 0; zero_start = None
            for offset, depth in _depth_chunks(alignment, name, length, minimum):
                depth_sum += int(depth.sum()); max_depth = max(max_depth, int(depth.max(initial=0)))
                covered += int(np.count_nonzero(depth))
                for threshold in thresholds: threshold_counts[threshold] += int(np.count_nonzero(depth >= threshold))
                zero = depth == 0
                for index, is_zero in enumerate(zero):
                    position = offset + index
                    if is_zero and zero_start is None: zero_start = position
                    elif not is_zero and zero_start is not None:
                        low_regions.append({"reference": name, "start": zero_start, "end": position, "length": position - zero_start}); zero_start = None
                if zero_start is not None and offset + len(depth) == length:
                    low_regions.append({"reference": name, "start": zero_start, "end": length, "length": length - zero_start}); zero_start = None
            row = {"reference": name, "length": length, "mean_depth": depth_sum / length if length else 0, "max_depth": max_depth, "covered_bases": covered, "covered_percent": covered / length * 100 if length else 0}
            row.update({f"bases_ge_{threshold}x": threshold_counts[threshold] for threshold in thresholds})
            rows.append(row); totals["length"] += length; totals["depth_sum"] += depth_sum; totals["covered"] += covered
    result = {"references": rows, "total_bases": totals["length"], "mean_depth": totals["depth_sum"] / totals["length"] if totals["length"] else 0, "covered_percent": totals["covered"] / totals["length"] * 100 if totals["length"] else 0, "zero_coverage_regions": low_regions[:10000], "zero_coverage_regions_truncated": len(low_regions) > 10000, "scope": "full_reference_including_zero_depth"}
    if args.get("output_path"): result["output_path"] = write_json(args["output_path"], result)
    return result


def alignment_pileup(args: dict) -> dict:
    start, end = int(args["start"]), int(args["end"])
    if start < 0 or end <= start or end - start > MAX_REGION_BASES: fail(f"区域长度必须在 1 到 {MAX_REGION_BASES:,} bp 之间")
    rows = []
    with open_alignment(args["input_path"], reference_path=args.get("reference_path")) as alignment:
        if not alignment.has_index(): fail("pileup 需要 BAM/CRAM 索引")
        for column in alignment.pileup(args["reference"], start, end, truncate=True, min_base_quality=int(args.get("min_base_quality", 13)), min_mapping_quality=int(args.get("min_mapq", 0)), stepper="all"):
            counts = Counter(); insertions = deletions = 0
            for entry in column.pileups:
                if entry.indel > 0: insertions += 1
                if entry.is_del or entry.indel < 0: deletions += 1
                if not entry.is_del and not entry.is_refskip and entry.query_position is not None:
                    base = entry.alignment.query_sequence[entry.query_position].upper(); counts[base if base in "ACGTN" else "N"] += 1
            rows.append({"reference": args["reference"], "position": column.reference_pos + 1, "depth": column.nsegments, **{base: counts[base] for base in "ACGTN"}, "insertions": insertions, "deletions": deletions})
    output = args.get("output_path") or str(Path("/home/ubuntu/output") / "pileup.csv")
    write_csv(output, rows, ["reference", "position", "depth", "A", "C", "G", "T", "N", "insertions", "deletions"])
    return {"output_path": output, "positions": len(rows), "region": f"{args['reference']}:{start + 1}-{end}", "scope": "full_region"}


def alignment_region_product(args: dict) -> dict:
    start, end = int(args["start"]), int(args["end"])
    if start < 0 or end <= start or end - start > MAX_REGION_BASES: fail(f"区域长度必须在 1 到 {MAX_REGION_BASES:,} bp 之间")
    output = ensure_parent(args["output_path"]); temporary = output.parent / f".{output.stem}.unsorted.bam"
    count = 0
    with open_alignment(args["input_path"], reference_path=args.get("reference_path")) as source:
        with open_alignment(temporary, write=True, template=source, reference_path=args.get("reference_path")) as target:
            for read in iter_region(source, args["reference"], start, end): target.write(read); count += 1
    sort_args = ["-o", str(output)]
    if output.suffix.lower() == ".cram" and args.get("reference_path"):
        sort_args.extend(["--reference", args["reference_path"]])
    sort_args.append(str(temporary))
    pysam.sort(*sort_args); temporary.unlink(missing_ok=True); pysam.index(str(output)); idx = index_path(output)
    report = output.with_suffix(output.suffix + ".region.json")
    write_json(report, {"reference": args["reference"], "start": start, "end": end, "records": count})
    return {"output_path": str(output), "index_path": idx, "report_path": str(report), "attachments": [str(output), idx, str(report)], "records_written": count}


def alignment_read_group_manage(args: dict) -> dict:
    source_path, output = args["input_path"], ensure_parent(args["output_path"]); rg_id = str(args["id"])
    with open_alignment(source_path, reference_path=args.get("reference_path")) as source:
        header = source.header.to_dict(); rg = {"ID": rg_id, "SM": str(args["sample"])}
        for source_key, target_key in (("library", "LB"), ("platform", "PL"), ("unit", "PU")):
            if args.get(source_key): rg[target_key] = str(args[source_key])
        existing = [item for item in header.get("RG", []) if item.get("ID") != rg_id]; header["RG"] = [*existing, rg]
        count = 0
        with open_alignment(output, write=True, header=header, reference_path=args.get("reference_path")) as target:
            for read in iter_all(source): read.set_tag("RG", rg_id, value_type="Z"); target.write(read); count += 1
    return {"output_path": str(output), "read_group": rg, "records_written": count}


def alignment_fixmate(args: dict) -> dict:
    output = ensure_parent(args["output_path"]); command = []
    if args.get("add_mate_score", True): command.append("-m")
    command.extend([args["input_path"], str(output)]); pysam.fixmate(*command)
    return {"output_path": str(output), "mate_score_added": bool(args.get("add_mate_score", True))}


def alignment_mark_duplicates(args: dict) -> dict:
    output = ensure_parent(args["output_path"]); command = []
    if args.get("remove_duplicates", False): command.append("-r")
    command.extend([args["input_path"], str(output)]); pysam.markdup(*command)
    stats = flagstat_data(str(output), args.get("reference_path"))
    return {"output_path": str(output), "remove_duplicates": bool(args.get("remove_duplicates", False)), "duplicates": stats["duplicates"], "duplicate_percent": stats["duplicate_percent"]}


def alignment_merge_split(args: dict) -> dict:
    mode, output = args["mode"], Path(args["output_path"])
    if mode == "merge":
        inputs = args.get("input_paths") or []
        if len(inputs) < 2: fail("合并模式至少需要两个 input_paths")
        ensure_parent(output); pysam.merge("-f", str(output), *inputs)
        return {"output_path": str(output), "source_files": len(inputs)}
    source_path = args.get("input_path")
    if not source_path: fail("拆分模式需要 input_path")
    output.mkdir(parents=True, exist_ok=True); handles = {}; counts = Counter()
    with open_alignment(source_path, reference_path=args.get("reference_path")) as source:
        header = source.header
        try:
            for read in iter_all(source):
                if mode == "split_reference": key = "unmapped" if read.is_unmapped else source.get_reference_name(read.reference_id)
                else: key = read.get_tag("RG") if read.has_tag("RG") else "no_read_group"
                safe = "".join(char if char.isalnum() or char in "._-" else "_" for char in key)
                if safe not in handles:
                    path = output / f"{safe}.bam"; handles[safe] = (path, open_alignment(path, write=True, header=header))
                handles[safe][1].write(read); counts[safe] += 1
        finally:
            for _path, handle in handles.values(): handle.close()
    files = [str(item[0]) for item in handles.values()]
    manifest = output / "split_manifest.json"; write_json(manifest, {"mode": mode, "files": files, "counts": counts})
    return {"output_path": str(manifest), "attachments": [str(manifest), *files], "files": files, "counts": dict(counts)}


def fastq_record(read, suffix: str = "") -> str:
    sequence = read.query_sequence or ""
    qualities = read.qual if read.qual not in (None, "*") else "I" * len(sequence)
    return f"@{read.query_name}{suffix}\n{sequence}\n+\n{qualities}\n"


def alignment_fastq_export(args: dict) -> dict:
    out1 = ensure_parent(args["output_read1"]); out2 = ensure_parent(args["output_read2"]) if args.get("output_read2") else None; single = ensure_parent(args["singleton_path"]) if args.get("singleton_path") else None
    selection = args.get("selection", "all"); counts = Counter(); pending = {}
    with out1.open("w", encoding="utf-8") as first_handle, (out2.open("w", encoding="utf-8") if out2 else open("/dev/null", "w")) as second_handle, (single.open("w", encoding="utf-8") if single else open("/dev/null", "w")) as single_handle:
        with open_alignment(args["input_path"], reference_path=args.get("reference_path")) as alignment:
            for read in iter_all(alignment):
                if read.is_secondary or read.is_supplementary: continue
                if selection == "mapped" and read.is_unmapped: continue
                if selection == "unmapped" and not read.is_unmapped: continue
                if not read.is_paired or not out2:
                    first_handle.write(fastq_record(read)); counts["single"] += 1; continue
                mate = pending.pop(read.query_name, None)
                if mate is None:
                    if len(pending) >= 2_000_000: fail("未按名称排序的配对记录超过内存安全上限")
                    pending[read.query_name] = read; continue
                r1, r2 = (read, mate) if read.is_read1 else (mate, read)
                first_handle.write(fastq_record(r1, "/1")); second_handle.write(fastq_record(r2, "/2")); counts["pairs"] += 1
            for read in pending.values(): single_handle.write(fastq_record(read)); counts["singletons"] += 1
    attachments = [str(out1)] + ([str(out2)] if out2 else []) + ([str(single)] if single else [])
    return {"output_path": str(out1), "attachments": attachments, **counts}


def alignment_insert_size_profile(args: dict) -> dict:
    values = []; orientations = Counter(); maximum = max(1, int(args.get("max_records", 2_000_000)))
    with open_alignment(args["input_path"], reference_path=args.get("reference_path")) as alignment:
        for read in iter_all(alignment):
            if len(values) >= maximum: break
            if not read.is_read1 or not read.is_proper_pair or read.is_unmapped or read.mate_is_unmapped: continue
            size = abs(int(read.template_length))
            if size <= 0: continue
            values.append(size); orientations[("R" if read.is_reverse else "F") + ("R" if read.mate_is_reverse else "F")] += 1
    result = {"pairs_sampled": len(values), "sampling_limit": maximum, "mean": float(np.mean(values)) if values else None, "median": float(np.median(values)) if values else None, "standard_deviation": float(np.std(values)) if values else None, "p05": float(np.percentile(values, 5)) if values else None, "p95": float(np.percentile(values, 95)) if values else None, "orientations": dict(orientations)}
    if args.get("output_path"): result["output_path"] = write_json(args["output_path"], result)
    return result


def alignment_rna_splice_profile(args: dict) -> dict:
    junctions = Counter(); lengths = Counter(); supplementary = chimeric = reads = 0
    with open_alignment(args["input_path"], reference_path=args.get("reference_path")) as alignment:
        for read in iter_all(alignment):
            reads += 1
            if read.is_supplementary: supplementary += 1
            if read.has_tag("SA"): chimeric += 1
            if read.is_unmapped or not read.cigartuples: continue
            position = read.reference_start
            for operation, length in read.cigartuples:
                if operation == 3:
                    key = (alignment.get_reference_name(read.reference_id), position, position + length, "-" if read.is_reverse else "+")
                    junctions[key] += 1; lengths[length] += 1; position += length
                elif operation in {0, 2, 7, 8}: position += length
    rows = [{"reference": key[0], "start": key[1], "end": key[2], "strand": key[3], "supporting_reads": count} for key, count in junctions.most_common()]
    output = write_csv(args["output_path"], rows, ["reference", "start", "end", "strand", "supporting_reads"]) if args.get("output_path") else None
    return {"output_path": output, "reads_scanned": reads, "junction_count": len(rows), "junctions": rows[:1000], "junctions_truncated": len(rows) > 1000, "intron_lengths": dict(lengths), "supplementary_records": supplementary, "chimeric_records": chimeric, "scope": "full_file"}


def alignment_multi_sample_compare(args: dict) -> dict:
    inputs = args.get("input_paths") or []
    if len(inputs) < 2: fail("至少需要两个比对文件")
    rows = []
    for path in inputs:
        flag = flagstat_data(path, args.get("reference_path")); insert = alignment_insert_size_profile({"input_path": path, "max_records": args.get("max_records", 500000), "reference_path": args.get("reference_path")})
        rows.append({"sample": Path(path).name, "total": flag["total"], "mapped_percent": flag["mapped_percent"], "duplicate_percent": flag["duplicate_percent"], "mean_mapq": flag["mean_mapq"], "mean_nm": flag["mean_nm"], "median_insert_size": insert["median"]})
    output = write_csv(args["output_path"], rows) if args.get("output_path") else None
    return {"output_path": output, "samples": rows, "scope": "full_file_except_bounded_insert_size"}


def interactive_report(path: Path, title: str, payload: dict) -> str:
    import plotly.graph_objects as go
    labels = []; values = []
    for key, value in payload.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool): labels.append(key); values.append(value)
    figure = go.Figure(go.Bar(x=labels, y=values, marker_color="#2b7659")); figure.update_layout(title=title, xaxis_tickangle=-30)
    figure.write_html(path, include_plotlyjs="inline", full_html=True)
    return str(path)


def alignment_qc_workflow(args: dict) -> dict:
    root = Path(args["output_dir"]); root.mkdir(parents=True, exist_ok=True); attachments = []
    flag = flagstat_data(args["input_path"], args.get("reference_path")); flag_path = write_json(root / "flagstat.json", flag); attachments.append(flag_path)
    references = alignment_reference_counts({"input_path": args["input_path"], "output_path": str(root / "reference_counts.csv"), "reference_path": args.get("reference_path")}); attachments.append(references["output_path"])
    insert = alignment_insert_size_profile({"input_path": args["input_path"], "output_path": str(root / "insert_size.json"), "reference_path": args.get("reference_path")}); attachments.append(insert["output_path"])
    splice = alignment_rna_splice_profile({"input_path": args["input_path"], "output_path": str(root / "splice_junctions.csv"), "reference_path": args.get("reference_path")}); attachments.append(splice["output_path"])
    coverage = None
    try:
        coverage = alignment_coverage_accurate({"input_path": args["input_path"], "output_path": str(root / "coverage.json"), "reference_path": args.get("reference_path")}); attachments.append(coverage["output_path"])
    except (ValueError, OSError, SystemExit):
        coverage = {"warning": "未检测到可用索引，未计算精确覆盖度"}
    report = {"flagstat": flag, "reference_summary": {"reference_count": len(references["references"]), "unmapped_records": references["unmapped_records"]}, "insert_size": insert, "rna_splice": {"junction_count": splice["junction_count"]}, "coverage": coverage}
    report_path = write_json(root / "alignment_qc_report.json", report); html = interactive_report(root / "alignment_qc_dashboard.html", "比对文件质量概览", flag); attachments.extend([report_path, html])
    return {"output_path": report_path, "interactive_output_path": html, "attachments": attachments, **report}


def alignment_region_analysis_workflow(args: dict) -> dict:
    root = Path(args["output_dir"]); root.mkdir(parents=True, exist_ok=True); product = root / "region.bam"
    region = alignment_region_product({**args, "output_path": str(product)})
    pileup = alignment_pileup({**args, "input_path": str(product), "output_path": str(root / "pileup.csv")})
    coverage = alignment_coverage_accurate({"input_path": str(product), "reference": args["reference"], "output_path": str(root / "coverage.json"), "reference_path": args.get("reference_path")})
    summary = {"region": f"{args['reference']}:{int(args['start']) + 1}-{args['end']}", "records": region["records_written"], "pileup_positions": pileup["positions"], "coverage": coverage}
    report = write_json(root / "region_analysis_report.json", summary); html = interactive_report(root / "region_analysis.html", "区域比对分析", {"records": region["records_written"], "pileup_positions": pileup["positions"], "mean_depth": coverage["mean_depth"], "covered_percent": coverage["covered_percent"]})
    attachments = [*region["attachments"], pileup["output_path"], coverage["output_path"], report, html]
    return {"output_path": report, "interactive_output_path": html, "attachments": attachments, **summary}


OPERATIONS = {
    "alignment_format_convert": alignment_format_convert,
    "alignment_sort": alignment_sort,
    "alignment_index": alignment_index,
    "alignment_flagstat": alignment_flagstat,
    "alignment_reference_counts": alignment_reference_counts,
    "alignment_coverage_accurate": alignment_coverage_accurate,
    "alignment_pileup": alignment_pileup,
    "alignment_region_product": alignment_region_product,
    "alignment_read_group_manage": alignment_read_group_manage,
    "alignment_fixmate": alignment_fixmate,
    "alignment_mark_duplicates": alignment_mark_duplicates,
    "alignment_merge_split": alignment_merge_split,
    "alignment_fastq_export": alignment_fastq_export,
    "alignment_insert_size_profile": alignment_insert_size_profile,
    "alignment_rna_splice_profile": alignment_rna_splice_profile,
    "alignment_multi_sample_compare": alignment_multi_sample_compare,
    "alignment_qc_workflow": alignment_qc_workflow,
    "alignment_region_analysis_workflow": alignment_region_analysis_workflow,
}


def main() -> None:
    if len(sys.argv) != 3: fail("参数错误")
    try:
        payload = sys.argv[2] + "=" * (-len(sys.argv[2]) % 4)
        arguments = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
        emit(OPERATIONS[sys.argv[1]](arguments))
    except SystemExit:
        raise
    except Exception as exc:
        fail(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
