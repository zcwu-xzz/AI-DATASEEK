import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.domain.models.tool_result import ToolResult
from app.domain.services.tools.plugin import PluginToolkit


ROOT = Path(__file__).resolve().parents[2]


def _tool_names(toolkit: PluginToolkit) -> set[str]:
    return {
        schema["function"]["name"]
        for schema in toolkit.get_tools()
    }


def test_builtin_plugins_discover_scientific_and_geoscience_tools():
    toolkit = PluginToolkit(
        AsyncMock(),
        session_id="session-1",
        plugins_dir=ROOT / "tools",
    )

    names = _tool_names(toolkit)
    assert len(names) == 383
    assert "scientific_inspect" in names
    assert "scientific_netcdf_visualize" in names
    assert "geoscience_collection_inspect" in names
    assert "geoscience_zonal_statistics" in names
    assert "data_format_inspect" in names
    assert "cf_semantics_validate" in names
    assert "spatial_grid_diagnose" in names
    assert "raster_compatibility_validate" in names
    assert "eo_product_resolve" in names
    assert "artifact_scientific_validate" in names
    assert "workbook_inspect" in names
    assert "tabular_visualize" in names
    assert "document_inspect" in names
    assert "document_visual_validate" in names
    assert "pdf_ocr_text" in names
    assert "presentation_inspect" in names
    assert "hierarchical_array_extract" in names
    assert "geoscience_vector_visualize" in names
    assert "geodata_product_package" in names
    assert "netcdf_multi_file_concat" in names
    assert "raster_calculator" in names
    assert "pxp_inspect" in names
    assert "pxp_extract_wave" in names
    assert "pxp_peak_fit" in names
    assert "pxp_visualize" in names
    assert "pxp_extract_experiment_conditions" in names
    assert "pxp_multipeak_deconvolution" in names
    assert "pxp_reproducible_package" in names
    assert "space_fits_inspect" in names
    assert "space_tle_propagate" in names
    assert "sequence_inspect" in names
    assert "sequence_fastqc_report" in names
    assert "blast_hit_visualize" in names
    assert "sequence_quality_heatmap" in names
    assert "fasta_assembly_metrics" in names
    assert "fastq_qc_clean_workflow" in names
    assert "alignment_coverage_accurate" in names
    assert "alignment_region_analysis_workflow" in names
    assert names == toolkit.dataset_fast_path_tool_names
    inspect = next(
        item for item in toolkit.get_tools()
        if item["function"]["name"] == "scientific_inspect"
    )
    assert inspect["function"]["parameters"]["required"] == ["input_path"]
    assert "id" not in inspect["function"]["parameters"]["properties"]


@pytest.mark.asyncio
async def test_plugin_tool_invocation_uses_registry_runner_and_session_id():
    sandbox = AsyncMock()
    sandbox.exec_command.return_value = ToolResult(
        success=True,
        data={"status": "completed", "returncode": 0, "output": "{}"},
    )
    toolkit = PluginToolkit(
        sandbox,
        session_id="session-42",
        plugins_dir=ROOT / "tools",
    )

    message = await toolkit.get_tool("scientific_inspect").ainvoke({
        "id": "call-1",
        "args": {"input_path": "/home/ubuntu/datasets/example.nc"},
    })

    assert message.tool_call_id == "call-1"
    assert message.artifact.success is True
    session_id, exec_dir, command = sandbox.exec_command.await_args.args
    assert session_id == "session-42"
    assert exec_dir == "/home/ubuntu"
    assert command.startswith("ai-dataseek-tool run scientific_inspect --arguments-base64 ")
    encoded = command.rsplit(" ", 1)[-1]
    payload = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
    assert payload == {"input_path": "/home/ubuntu/datasets/example.nc"}


def test_duplicate_plugin_tool_names_fail_startup(tmp_path):
    for plugin in ("one", "two"):
        directory = tmp_path / plugin
        directory.mkdir()
        (directory / "manifest.json").write_text(json.dumps({
            "plugin": plugin,
            "tools": [{
                "name": "duplicate",
                "description": "Duplicate test tool",
                "parameters": {"type": "object", "properties": {}},
            }],
        }))

    with pytest.raises(ValueError, match="Duplicate plugin tool name"):
        PluginToolkit(AsyncMock(), session_id="session", plugins_dir=tmp_path)


@pytest.mark.asyncio
async def test_workflow_declared_attachments_are_bounded_and_path_safe(tmp_path):
    plugin = tmp_path / "workflow"
    plugin.mkdir()
    (plugin / "manifest.json").write_text(json.dumps({
        "plugin": "workflow",
        "tools": [{
            "name": "workflow_tool",
            "description": "workflow",
            "timeout_seconds": 300,
            "parameters": {"type": "object", "properties": {}},
        }],
    }))
    sandbox = AsyncMock()
    sandbox.exec_command.return_value = ToolResult(success=True, data={"status": "running"})
    sandbox.wait_for_process.return_value = ToolResult(success=True, data={"status": "completed", "returncode": 0})
    payload = {
        "success": True,
        "output_path": "/home/ubuntu/output/report.json",
        "attachments": [
            "/home/ubuntu/output/report.json",
            "/home/ubuntu/output/chart.html",
            "/home/ubuntu/datasets/private.fastq",
            "/etc/passwd",
            "relative.txt",
        ],
    }
    sandbox.view_shell.return_value = ToolResult(success=True, data={"output": json.dumps(payload)})
    toolkit = PluginToolkit(sandbox, session_id="session", plugins_dir=tmp_path)

    result = await toolkit.call_tool("workflow_tool", {})

    assert result.data["attachments"] == [
        "/home/ubuntu/output/report.json",
        "/home/ubuntu/output/chart.html",
    ]
    sandbox.wait_for_process.assert_awaited_once_with("session", 300)
