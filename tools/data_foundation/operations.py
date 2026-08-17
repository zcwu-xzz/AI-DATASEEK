#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np


def clean(value: Any) -> Any:
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    return value


def result(operation: str, *, summary: dict[str, Any], warnings: list[str] | None = None, evidence: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return clean({"success": True, "answer_ready": True, "operation": operation, "summary": summary, "evidence": evidence or [], "artifacts": [], "warnings": warnings or [], "provenance": {"tool": operation, "version": "1.0.0"}, "recommended_next_tools": []})


def suffix_format(path: Path) -> str:
    name = path.name.lower()
    if path.is_dir() and (path / ".zgroup").exists() or path.is_dir() and (path / "zarr.json").exists(): return "zarr"
    for suffix, label in ((".nc", "netcdf"), (".nc4", "netcdf"), (".cdf", "netcdf"), (".hdf", "hdf"), (".h5", "hdf5"), (".hdf5", "hdf5"), (".grib", "grib"), (".grb", "grib"), (".grib2", "grib2"), (".tif", "geotiff"), (".tiff", "geotiff"), (".gpkg", "geopackage"), (".parquet", "geoparquet"), (".geojson", "geojson"), (".shp", "shapefile")):
        if name.endswith(suffix): return label
    if name.endswith(".safe"): return "safe"
    return "unknown"


def format_inspect(paths: list[str]) -> dict[str, Any]:
    items = []
    for raw in paths:
        path, fmt = Path(raw), suffix_format(Path(raw))
        item: dict[str, Any] = {"name": path.name, "format": fmt, "is_directory": path.is_dir()}
        if not path.exists(): item["error"] = "path does not exist"
        elif fmt in {"geotiff", "hdf", "hdf5"}:
            try:
                import rasterio
                with rasterio.open(path) as src:
                    item.update(driver=src.driver, subdatasets=list(src.subdatasets), band_count=src.count)
            except Exception as exc: item["open_warning"] = str(exc)
        elif fmt in {"geopackage", "geojson", "shapefile", "geoparquet"}:
            try:
                import pyogrio
                item["layers"] = [row[0] for row in pyogrio.list_layers(path)]
            except Exception as exc: item["open_warning"] = str(exc)
        items.append(item)
    warnings = [f"{i['name']}: {i.get('error') or i.get('open_warning')}" for i in items if i.get("error") or i.get("open_warning")]
    return result("data_format_inspect", summary={"file_count": len(items), "items": items}, warnings=warnings)


def open_dataset(path: str):
    import xarray as xr
    p = Path(path)
    if suffix_format(p) == "zarr": return xr.open_zarr(p, consolidated=None, decode_cf=False)
    return xr.open_dataset(p, decode_cf=False)


def coordinate_role(name: str, var: Any) -> str | None:
    std, axis, lower = str(var.attrs.get("standard_name", "")).lower(), str(var.attrs.get("axis", "")).upper(), name.lower()
    if axis == "X" or std == "longitude" or lower in {"lon", "longitude"}: return "longitude"
    if axis == "Y" or std == "latitude" or lower in {"lat", "latitude"}: return "latitude"
    if axis == "T" or std == "time" or lower == "time": return "time"
    return None


def cf_validate(path: str) -> dict[str, Any]:
    warnings, checks = [], []
    with open_dataset(path) as ds:
        convention = str(ds.attrs.get("Conventions", ""))
        if "CF-" not in convention: warnings.append("global Conventions does not declare CF compliance")
        roles = {role: name for name, var in ds.coords.items() if (role := coordinate_role(name, var))}
        for role in ("latitude", "longitude"):
            if role not in roles: warnings.append(f"{role} coordinate was not identified")
        for name, var in ds.variables.items():
            attrs = var.attrs
            checks.append({"variable": name, "dimensions": list(var.dims), "units": attrs.get("units"), "fill_value": attrs.get("_FillValue"), "scale_factor": attrs.get("scale_factor"), "add_offset": attrs.get("add_offset"), "grid_mapping": attrs.get("grid_mapping")})
            if "scale_factor" in attrs and not np.issubdtype(var.dtype, np.number): warnings.append(f"{name}: scale_factor is declared on a non-numeric variable")
            if coordinate_role(name, var) == "time" and "units" not in attrs: warnings.append(f"{name}: time coordinate has no units")
        summary = {"conventions": convention or None, "dimensions": dict(ds.sizes), "coordinate_roles": roles, "variables": checks}
    return result("cf_semantics_validate", summary=summary, warnings=warnings)


def grid_diagnose(path: str) -> dict[str, Any]:
    p = Path(path)
    if suffix_format(p) == "geotiff":
        import rasterio
        with rasterio.open(p) as src:
            summary = {"grid_type":"projected_raster" if src.crs and not src.crs.is_geographic else "geographic_raster", "crs":str(src.crs) if src.crs else None, "shape":[src.height,src.width], "resolution":list(src.res), "bounds":list(src.bounds), "north_up":src.transform.b == 0 and src.transform.d == 0}
        return result("spatial_grid_diagnose", summary=summary, warnings=[] if summary["crs"] else ["raster has no CRS"])
    with open_dataset(path) as ds:
        roles = {role: ds[name] for name, var in ds.coords.items() if (role := coordinate_role(name, var))}
        lat, lon = roles.get("latitude"), roles.get("longitude")
        if lat is None or lon is None: return result("spatial_grid_diagnose", summary={"grid_type":"unknown"}, warnings=["latitude/longitude coordinates were not identified"])
        grid_type = "rectilinear" if lat.ndim == lon.ndim == 1 else "curvilinear" if lat.ndim == lon.ndim == 2 else "mixed"
        lon_values = np.asarray(lon.values, dtype=float)
        summary = {"grid_type":grid_type, "latitude_dimensions":list(lat.dims), "longitude_dimensions":list(lon.dims), "longitude_domain":"0_360" if np.nanmax(lon_values) > 180 else "-180_180", "crosses_antimeridian":bool(np.nanmax(lon_values)-np.nanmin(lon_values) > 300), "latitude_ascending":bool(lat.ndim == 1 and lat.size > 1 and lat.values[-1] > lat.values[0]), "longitude_ascending":bool(lon.ndim == 1 and lon.size > 1 and lon.values[-1] > lon.values[0])}
    return result("spatial_grid_diagnose", summary=summary)


def raster_compatibility(paths: list[str], operation: str) -> dict[str, Any]:
    import rasterio
    signatures = []
    for raw in paths:
        with rasterio.open(raw) as src:
            signatures.append({"name":Path(raw).name,"crs":str(src.crs),"shape":[src.height,src.width],"transform":list(src.transform),"resolution":list(src.res),"band_count":src.count,"dtype":list(src.dtypes),"nodata":src.nodata,"bounds":list(src.bounds)})
    first = signatures[0]
    strict = operation in {"stack", "difference", "overlay"}
    fields = ["crs", "shape", "transform", "resolution"] if strict else ["crs", "resolution"]
    differences = [
        {"file": s["name"], "fields": [field for field in fields if s[field] != first[field]]}
        for s in signatures[1:]
    ]
    differences = [d for d in differences if d["fields"]]
    return result("raster_compatibility_validate", summary={"operation":operation,"compatible":not differences,"signatures":signatures,"differences":differences}, warnings=[] if not differences else ["rasters require alignment before this operation"])


PRODUCTS = {
    "landsat": {"markers":["landsat","lc08","lc09","le07"],"quality_band":"QA_PIXEL","scale":"use collection metadata; Collection 2 surface reflectance commonly uses 0.0000275 and -0.2 offset"},
    "sentinel-2": {"markers":["sentinel-2","s2a_","s2b_","msil1c","msil2a"],"quality_band":"SCL or QA60","scale":"resolve from product metadata"},
    "modis": {"markers":["modis","mod09","myd09","mod13","myd13"],"quality_band":"product-specific QA layer","scale":"resolve from HDF-EOS metadata"},
    "viirs": {"markers":["viirs","vnp","vjn"],"quality_band":"product-specific QF layer","scale":"resolve from product metadata"},
}


def product_resolve(path: str) -> dict[str, Any]:
    p, probe, metadata = Path(path), Path(path).name.lower(), {}
    try:
        import rasterio
        with rasterio.open(p) as src: metadata = src.tags(); probe += " " + " ".join(f"{k}={v}" for k,v in metadata.items()).lower()
    except Exception: pass
    matches = [(name, model) for name, model in PRODUCTS.items() if any(marker in probe for marker in model["markers"])]
    if len(matches) != 1: return result("eo_product_resolve", summary={"product":None,"metadata":metadata}, warnings=["product could not be resolved unambiguously; do not infer band or QA semantics"])
    name, model = matches[0]
    return result("eo_product_resolve", summary={"product":name,"quality_band":model["quality_band"],"scale_rule":model["scale"],"metadata":metadata})


def artifact_validate(path: str) -> dict[str, Any]:
    p, warnings = Path(path), []
    if not p.is_file() or p.stat().st_size == 0: raise ValueError("artifact is missing or empty")
    fmt, summary = suffix_format(p), {"name":p.name,"format":suffix_format(p),"size_bytes":p.stat().st_size,"readable":True}
    if fmt == "geotiff":
        import rasterio
        with rasterio.open(p) as src:
            summary.update(shape=[src.height,src.width],band_count=src.count,crs=str(src.crs) if src.crs else None,nodata=src.nodata)
            sample = src.read(1, out_shape=(1,min(src.height,256),min(src.width,256)), masked=True)
            summary["valid_sample_count"] = int(sample.count())
            if not src.crs: warnings.append("raster has no CRS")
            if sample.count() == 0: warnings.append("raster sample contains no valid pixels")
    elif fmt == "netcdf":
        with open_dataset(path) as ds:
            summary.update(dimensions=dict(ds.sizes),variables=list(ds.data_vars))
            if not ds.data_vars: warnings.append("dataset has no data variables")
    elif p.suffix.lower() == ".png":
        from PIL import Image
        with Image.open(p) as image:
            arr = np.asarray(image.convert("RGB").resize((64,64)))
            summary.update(width=image.width,height=image.height,pixel_standard_deviation=float(arr.std()))
            if arr.std() < 1: warnings.append("image appears blank or constant")
    return result("artifact_scientific_validate", summary=summary, warnings=warnings)


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("tool"); parser.add_argument("--input-path"); parser.add_argument("--input-paths-json"); parser.add_argument("--operation")
    args = parser.parse_args()
    try:
        paths = json.loads(args.input_paths_json) if args.input_paths_json else []
        functions = {"data_format_inspect":lambda:format_inspect(paths),"cf_semantics_validate":lambda:cf_validate(args.input_path),"spatial_grid_diagnose":lambda:grid_diagnose(args.input_path),"raster_compatibility_validate":lambda:raster_compatibility(paths,args.operation),"eo_product_resolve":lambda:product_resolve(args.input_path),"artifact_scientific_validate":lambda:artifact_validate(args.input_path)}
        output = functions[args.tool]()
    except Exception as exc:
        output = {"success":False,"answer_ready":True,"operation":args.tool,"error":f"{type(exc).__name__}: {exc}","warnings":[]}
    print(json.dumps(clean(output), ensure_ascii=False, allow_nan=False)); return 0 if output["success"] else 1


if __name__ == "__main__": raise SystemExit(main())
