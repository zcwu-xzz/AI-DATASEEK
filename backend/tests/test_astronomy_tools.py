from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from astropy.io import fits
from astropy.table import Table


ROOT = Path(__file__).resolve().parents[2]
OPERATIONS = ROOT / "tools" / "astronomy" / "operations.py"


def run_tool(name: str, arguments: dict) -> dict:
    encoded = base64.urlsafe_b64encode(json.dumps(arguments).encode()).decode()
    result = subprocess.run([sys.executable, str(OPERATIONS), name, encoded], check=True, capture_output=True, text=True)
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_fits_foundation_tools_produce_verified_artifacts(tmp_path):
    source = tmp_path / "cube.fits"
    output = tmp_path / "slice.fits"
    data = np.arange(60, dtype=np.float32).reshape(3, 4, 5)
    fits.PrimaryHDU(data=data).writeto(source, checksum=True)

    validation = run_tool("astronomy_fits_validate", {"input_path": str(source)})
    statistics = run_tool("astronomy_fits_image_statistics", {"input_path": str(source), "slice_indices": [2]})
    sliced = run_tool("astronomy_fits_cube_slice", {"input_path": str(source), "slice_indices": [1], "output_path": str(output)})

    assert validation["success"] is True
    assert validation["valid"] is True
    assert statistics["statistics"]["minimum"] == 40
    assert sliced["success"] is True
    assert fits.getdata(output).shape == (4, 5)


def test_lightcurve_and_catalog_tools_return_scientific_results(tmp_path):
    lightcurve = tmp_path / "lightcurve.csv"
    time = np.linspace(0, 20, 500)
    Table({"time": time, "flux": np.sin(2 * np.pi * time / 2.5)}).write(lightcurve, format="ascii.csv")
    periodogram = run_tool("astronomy_lightcurve_periodogram", {
        "input_path": str(lightcurve), "min_period": 1, "max_period": 5,
    })
    assert abs(periodogram["best_period"] - 2.5) < 0.05

    catalog = tmp_path / "catalog.csv"
    Table({"ra": [10.0, 10.01, 30.0], "dec": [20.0, 20.01, -5.0]}).write(catalog, format="ascii.csv")
    filtered = run_tool("astronomy_catalog_cone_filter", {
        "input_path": str(catalog), "ra": 10, "dec": 20, "radius_deg": 0.1,
    })
    assert filtered["row_count"] == 2
