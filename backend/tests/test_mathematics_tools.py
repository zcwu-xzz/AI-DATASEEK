from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from scipy import io, sparse


ROOT = Path(__file__).resolve().parents[2]
OPERATIONS = ROOT / "tools" / "mathematics" / "operations.py"
TOOL_PYTHON = ROOT / "sandbox" / ".venv" / "bin" / "python"


def run_tool(name: str, arguments: dict) -> dict:
    encoded = base64.urlsafe_b64encode(json.dumps(arguments).encode()).decode()
    executable = str(TOOL_PYTHON) if TOOL_PYTHON.is_file() else sys.executable
    completed = subprocess.run([executable, str(OPERATIONS), name, encoded], capture_output=True, text=True, check=True)
    return json.loads(completed.stdout.strip().splitlines()[-1])


@pytest.fixture
def math_files(tmp_path):
    vector = np.array([1.0, 2.0, 3.0, 4.0])
    vector2 = np.array([2.0, 1.0, 4.0, 3.0])
    rhs = np.array([1.0, 2.0])
    matrix = np.array([[4.0, 1.0], [1.0, 3.0]])
    matrix2 = np.array([[1.0, 2.0], [3.0, 4.0]])
    tensor = np.arange(24.0).reshape(2, 3, 4)
    paths = {}
    for name, value in (("vector", vector), ("vector2", vector2), ("rhs", rhs), ("matrix", matrix), ("matrix2", matrix2), ("tensor", tensor)):
        path = tmp_path / f"{name}.npy"; np.save(path, value); paths[name] = path
    paths["sparse"] = tmp_path / "sparse.mtx"
    io.mmwrite(paths["sparse"], sparse.csr_matrix([[1, 0, 0], [0, 2, 0], [3, 0, 4]]))
    paths["npz"] = tmp_path / "arrays.npz"; np.savez(paths["npz"], vector=vector, matrix=matrix)
    paths["mat"] = tmp_path / "arrays.mat"; io.savemat(paths["mat"], {"matrix": matrix})
    paths["tmp"] = tmp_path
    return paths


def test_array_formats_selection_validation_and_conversion(math_files):
    inspect = run_tool("math_array_inspect", {"input_path": str(math_files["npz"]), "variable": "matrix"})
    assert inspect["success"] and inspect["shape"] == [2, 2] and inspect["variables"] == ["matrix", "vector"]
    ambiguous = run_tool("math_array_inspect", {"input_path": str(math_files["npz"])})
    assert ambiguous["success"] is False and "variable" in ambiguous["error"]
    validation = run_tool("math_array_validate", {"input_path": str(math_files["mat"]), "variable": "matrix", "expected_ndim": 2})
    assert validation["valid"] is True
    output = math_files["tmp"] / "converted.h5"
    converted = run_tool("math_array_convert", {"input_path": str(math_files["matrix"]), "output_path": str(output)})
    assert converted["success"] and output.is_file()
    hdf = run_tool("math_array_inspect", {"input_path": str(output), "variable": "array"})
    assert hdf["shape"] == [2, 2]


def test_all_vector_tools(math_files):
    root = math_files["tmp"]
    base = {"input_path": str(math_files["vector"])}
    calls = {
        "math_vector_statistics": base,
        "math_vector_norm": {**base, "order": 2},
        "math_vector_normalize": {**base, "method": "zscore", "output_path": str(root / "normalized.npy")},
        "math_vector_distance": {**base, "other_input_path": str(math_files["vector2"]), "metric": "manhattan"},
        "math_vector_similarity": {**base, "other_input_path": str(math_files["vector2"])},
        "math_vector_arithmetic": {**base, "other_input_path": str(math_files["vector2"]), "operation": "add", "output_path": str(root / "sum.npy")},
        "math_vector_projection": {**base, "basis_input_path": str(math_files["vector2"]), "output_path": str(root / "projection.npy")},
        "math_vector_visualize": {**base, "style": "line", "output_path": str(root / "vector.png")},
    }
    results = {name: run_tool(name, args) for name, args in calls.items()}
    assert all(result["success"] for result in results.values())
    assert results["math_vector_norm"]["norm"] == pytest.approx(np.sqrt(30))
    assert results["math_vector_distance"]["distance"] == 4
    assert (root / "vector.png").stat().st_size > 0


def test_all_matrix_tools_and_numerical_evidence(math_files):
    root = math_files["tmp"]
    base = {"input_path": str(math_files["matrix"])}
    other = str(math_files["matrix2"])
    calls = {
        "math_matrix_statistics": base,
        "math_matrix_rank_condition": base,
        "math_matrix_determinant_trace": base,
        "math_matrix_transpose": {**base, "output_path": str(root / "transpose.npy")},
        "math_matrix_arithmetic": {**base, "other_input_path": other, "operation": "subtract", "output_path": str(root / "difference.npy")},
        "math_matrix_multiply": {**base, "other_input_path": other, "output_path": str(root / "product.npy")},
        "math_linear_system_solve": {**base, "rhs_input_path": str(math_files["rhs"]), "output_path": str(root / "solution.npy")},
        "math_matrix_inverse": {**base, "output_path": str(root / "inverse.npy")},
        "math_matrix_eigen": {**base, "output_path": str(root / "eigen.npz")},
        "math_matrix_svd": {**base, "components": 2, "output_path": str(root / "svd.npz")},
        "math_matrix_qr": {**base, "output_path": str(root / "qr.npz")},
        "math_matrix_lu": {**base, "output_path": str(root / "lu.npz")},
        "math_matrix_cholesky": {**base, "output_path": str(root / "cholesky.npy")},
        "math_sparse_matrix_profile": {"input_path": str(math_files["sparse"])},
        "math_matrix_visualize": {**base, "style": "heatmap", "output_path": str(root / "matrix.png")},
    }
    results = {name: run_tool(name, args) for name, args in calls.items()}
    assert all(result["success"] for result in results.values()), {name: result for name, result in results.items() if not result["success"]}
    assert results["math_matrix_rank_condition"]["rank"] == 2
    assert results["math_matrix_inverse"]["relative_residual"] < 1e-12
    assert results["math_matrix_svd"]["relative_residual"] < 1e-12
    assert results["math_sparse_matrix_profile"]["nnz"] == 4
    assert (root / "matrix.png").stat().st_size > 0


def test_all_tensor_tools(math_files):
    root = math_files["tmp"]
    base = {"input_path": str(math_files["tensor"])}
    calls = {
        "math_tensor_statistics": base,
        "math_tensor_slice": {**base, "axis": 0, "index": 1, "output_path": str(root / "slice.npy")},
        "math_tensor_transpose": {**base, "axes": [2, 0, 1], "output_path": str(root / "tensor_t.npy")},
        "math_tensor_reshape": {**base, "shape": [4, 6], "output_path": str(root / "reshape.npy")},
        "math_tensor_reduce": {**base, "axes": [2], "operation": "mean", "output_path": str(root / "reduced.npy")},
        "math_tensor_contract": {**base, "other_input_path": str(math_files["tensor"]), "axes": [[2], [2]], "output_path": str(root / "contract.npy")},
        "math_tensor_unfold": {**base, "mode": 1, "output_path": str(root / "unfold.npy")},
        "math_tensor_visualize": {**base, "slice_indices": [1], "output_path": str(root / "tensor.png")},
    }
    results = {name: run_tool(name, args) for name, args in calls.items()}
    assert all(result["success"] for result in results.values())
    assert results["math_tensor_slice"]["shape"] == [3, 4]
    assert results["math_tensor_contract"]["shape"] == [2, 3, 2, 3]
    assert np.load(root / "unfold.npy").shape == (3, 8)


def test_safety_failures_are_explicit(math_files, tmp_path):
    bad = tmp_path / "bad.npy"; np.save(bad, np.array([1.0, np.nan]))
    result = run_tool("math_vector_normalize", {"input_path": str(bad), "output_path": str(tmp_path / "out.npy")})
    assert result["success"] is False and "有限" in result["error"]
    singular = tmp_path / "singular.npy"; np.save(singular, np.array([[1.0, 2.0], [2.0, 4.0]]))
    result = run_tool("math_matrix_inverse", {"input_path": str(singular), "output_path": str(tmp_path / "inverse.npy")})
    assert result["success"] is False
    huge = tmp_path / "huge.mtx"
    io.mmwrite(huge, sparse.coo_matrix(([1.0], ([0], [0])), shape=(100_000, 100_000)))
    result = run_tool("math_matrix_statistics", {"input_path": str(huge)})
    assert result["success"] and result["rank"] is None
    result = run_tool("math_matrix_rank_condition", {"input_path": str(huge)})
    assert result["success"] is False and "安全上限" in result["error"]
