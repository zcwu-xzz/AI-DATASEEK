from __future__ import annotations

import base64
import io
import math
import shutil
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np
from PIL import Image


class AstronomyPreviewError(RuntimeError):
    pass


_FITS_SUFFIXES = {".fit", ".fits", ".fts", ".fz"}
_TIFF_SUFFIXES = {".tif", ".tiff"}
_MAX_PREVIEW_BYTES = 2 * 1024 * 1024 * 1024
_CACHE_TTL_SECONDS = 30 * 60
_MAX_RENDER_SIDE = 1800
_MAX_CACHE_ENTRIES = 64
_MAX_CACHE_ENTRIES_PER_OWNER = 4


@dataclass(slots=True)
class AstronomyPreviewEntry:
    preview_id: str
    owner_id: str
    file_id: str
    source_name: str
    format: str
    path: Path
    directory: Path
    created_at: float
    last_accessed_at: float


class AstronomyPreviewCache:
    def __init__(self, additional_suffixes: set[str] | None = None, max_bytes: int = _MAX_PREVIEW_BYTES) -> None:
        self._entries: dict[str, AstronomyPreviewEntry] = {}
        self._lock = threading.Lock()
        self._additional_suffixes = additional_suffixes or set()
        self._max_bytes = max_bytes

    def _purge_expired(self) -> None:
        cutoff = time.monotonic() - _CACHE_TTL_SECONDS
        expired = [key for key, item in self._entries.items() if item.last_accessed_at < cutoff]
        for key in expired:
            entry = self._entries.pop(key)
            shutil.rmtree(entry.directory, ignore_errors=True)

    def create(
        self,
        owner_id: str,
        file_id: str,
        source_name: str,
        stream: BinaryIO,
        *,
        declared_size: int | None = None,
    ) -> AstronomyPreviewEntry:
        suffix = _normalized_suffix(source_name)
        if suffix in _FITS_SUFFIXES or source_name.casefold().endswith(".fits.gz"):
            file_format = "fits"
            stored_suffix = ".fits.gz" if source_name.casefold().endswith(".fits.gz") else suffix
        elif suffix in _TIFF_SUFFIXES:
            file_format = "tiff"
            stored_suffix = suffix
        elif suffix in self._additional_suffixes:
            file_format = suffix.lstrip(".")
            stored_suffix = suffix
        else:
            raise AstronomyPreviewError("Only FITS and TIFF files can use this preview")
        if declared_size is not None and declared_size > self._max_bytes:
            raise AstronomyPreviewError("File exceeds the interactive preview size limit")

        directory = Path(tempfile.mkdtemp(prefix="dataseek-astronomy-preview-"))
        path = directory / f"source{stored_suffix}"
        copied = 0
        try:
            with path.open("wb") as target:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > self._max_bytes:
                        raise AstronomyPreviewError("File exceeds the interactive preview size limit")
                    target.write(chunk)
            preview_id = uuid.uuid4().hex
            now = time.monotonic()
            entry = AstronomyPreviewEntry(
                preview_id=preview_id,
                owner_id=owner_id,
                file_id=file_id,
                source_name=source_name,
                format=file_format,
                path=path,
                directory=directory,
                created_at=now,
                last_accessed_at=now,
            )
            with self._lock:
                self._purge_expired()
                owner_entries = sorted(
                    (item for item in self._entries.values() if item.owner_id == owner_id),
                    key=lambda item: item.last_accessed_at,
                )
                while len(owner_entries) >= _MAX_CACHE_ENTRIES_PER_OWNER:
                    expired = owner_entries.pop(0)
                    self._entries.pop(expired.preview_id, None)
                    shutil.rmtree(expired.directory, ignore_errors=True)
                if len(self._entries) >= _MAX_CACHE_ENTRIES:
                    expired = min(self._entries.values(), key=lambda item: item.last_accessed_at)
                    self._entries.pop(expired.preview_id, None)
                    shutil.rmtree(expired.directory, ignore_errors=True)
                self._entries[preview_id] = entry
            return entry
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise

    def get(self, preview_id: str, owner_id: str, file_id: str) -> AstronomyPreviewEntry:
        with self._lock:
            self._purge_expired()
            entry = self._entries.get(preview_id)
            if entry is None or entry.owner_id != owner_id or entry.file_id != file_id:
                raise AstronomyPreviewError("Interactive preview has expired or is not accessible")
            entry.last_accessed_at = time.monotonic()
            return entry

    def delete(self, preview_id: str, owner_id: str, file_id: str) -> None:
        with self._lock:
            entry = self._entries.get(preview_id)
            if entry is None or entry.owner_id != owner_id or entry.file_id != file_id:
                return
            self._entries.pop(preview_id, None)
        shutil.rmtree(entry.directory, ignore_errors=True)


astronomy_preview_cache = AstronomyPreviewCache()


def _normalized_suffix(name: str) -> str:
    return Path(name).suffix.casefold()


def _json_number(value: Any) -> float | int | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _fits_hdus(path: Path) -> list[dict[str, Any]]:
    from astropy.io import fits
    from astropy.wcs import WCS

    items: list[dict[str, Any]] = []
    with fits.open(path, memmap=True, lazy_load_hdus=True, ignore_missing_simple=True) as hdul:
        for index, hdu in enumerate(hdul):
            shape = list(getattr(hdu, "shape", None) or (np.shape(hdu.data) if getattr(hdu, "data", None) is not None else []))
            kind = "empty"
            if getattr(hdu, "is_image", False) and shape:
                kind = "spectrum" if len(shape) == 1 else "image"
            elif hasattr(hdu, "columns"):
                kind = "table"
            columns = None
            row_count = None
            table_preview = None
            spectrum_preview = None
            if kind == "table":
                columns = [
                    {"name": str(column.name), "format": str(column.format), "unit": str(column.unit) if column.unit else None}
                    for column in hdu.columns
                ]
                row_count = len(hdu.data) if hdu.data is not None else 0
                table_preview = []
                for row in (hdu.data[:50] if hdu.data is not None else []):
                    item: dict[str, Any] = {}
                    for column in hdu.columns.names[:50]:
                        value = row[column]
                        if isinstance(value, bytes):
                            rendered: Any = value.decode("utf-8", errors="replace")
                        elif np.ndim(value):
                            rendered = np.asarray(value).tolist()
                        elif isinstance(value, np.generic):
                            rendered = value.item()
                        else:
                            rendered = value
                        if isinstance(rendered, float):
                            rendered = _json_number(rendered)
                        elif isinstance(rendered, list):
                            rendered = [_json_number(part) if isinstance(part, (int, float)) else str(part) for part in rendered[:100]]
                        item[str(column)] = rendered if isinstance(rendered, (str, int, float, bool, type(None), list)) else str(rendered)
                    table_preview.append(item)
            if kind == "spectrum" and hdu.data is not None:
                values = np.asarray(hdu.data, dtype=float).reshape(-1)
                stride = max(1, int(math.ceil(values.size / 2000)))
                spectrum_preview = [
                    {"index": int(index), "value": _json_number(values[index])}
                    for index in range(0, values.size, stride)
                ]
            wcs_summary: dict[str, Any] | None = None
            if kind == "image" and len(shape) >= 2:
                try:
                    wcs = WCS(hdu.header)
                    wcs_summary = {
                        "celestial": bool(wcs.has_celestial),
                        "axis_types": list(wcs.world_axis_physical_types or []),
                        "units": [str(item) for item in (wcs.world_axis_units or [])],
                    }
                except Exception:
                    wcs_summary = {"celestial": False, "axis_types": [], "units": []}
            items.append({
                "index": index,
                "name": str(hdu.name or f"HDU {index}"),
                "type": hdu.__class__.__name__,
                "kind": kind,
                "shape": shape,
                "dtype": str(getattr(hdu, "data", np.asarray([])).dtype) if shape else None,
                "wcs": wcs_summary,
                "columns": columns,
                "row_count": row_count,
                "table_preview": table_preview,
                "spectrum_preview": spectrum_preview,
            })
    return items


def _tiff_pages(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import tifffile

    pages: list[dict[str, Any]] = []
    with tifffile.TiffFile(path) as tif:
        for index, page in enumerate(tif.pages):
            shape = list(page.shape)
            samples = int(shape[-1]) if len(shape) >= 3 and shape[-1] in {2, 3, 4} else 1
            pages.append({
                "index": index,
                "name": f"Page {index + 1}",
                "kind": "image",
                "shape": shape,
                "dtype": str(page.dtype),
                "samples": samples,
            })
        geospatial: dict[str, Any] = {"is_geotiff": bool(getattr(tif, "geotiff_metadata", None))}
        metadata = getattr(tif, "geotiff_metadata", None)
        if isinstance(metadata, dict):
            geospatial["metadata"] = {
                str(key): value
                for key, value in metadata.items()
                if isinstance(value, (str, int, float, bool, type(None)))
            }
    try:
        import rasterio

        with rasterio.open(path) as source:
            bounds_wgs84 = None
            if source.crs:
                try:
                    from rasterio.warp import transform_bounds
                    bounds_wgs84 = list(transform_bounds(source.crs, "EPSG:4326", *source.bounds, densify_pts=21))
                except Exception:
                    bounds_wgs84 = None
            geospatial.update({
                "is_geotiff": source.crs is not None,
                "crs": str(source.crs) if source.crs else None,
                "bounds": list(source.bounds),
                "bounds_wgs84": bounds_wgs84,
                "resolution": list(source.res),
                "bands": source.count,
                "nodata": _json_number(source.nodata),
            })
    except Exception:
        pass
    return pages, geospatial


def inspect_preview(entry: AstronomyPreviewEntry) -> dict[str, Any]:
    if entry.format == "fits":
        hdus = _fits_hdus(entry.path)
        selected = next((item["index"] for item in hdus if item["kind"] == "image" and len(item["shape"]) >= 2), None)
        return {
            "source_name": entry.source_name,
            "preview_id": entry.preview_id,
            "format": "FITS",
            "datasets": hdus,
            "selected_dataset": selected,
            "geospatial": None,
        }
    pages, geospatial = _tiff_pages(entry.path)
    return {
        "source_name": entry.source_name,
        "preview_id": entry.preview_id,
        "format": "GeoTIFF" if geospatial.get("is_geotiff") else "TIFF",
        "datasets": pages,
        "selected_dataset": 0 if pages else None,
        "geospatial": geospatial,
    }


def _fits_plane(path: Path, dataset_index: int, slice_indices: list[int]) -> tuple[np.ndarray, Any]:
    from astropy.io import fits

    with fits.open(path, memmap=True, lazy_load_hdus=True, ignore_missing_simple=True) as hdul:
        if dataset_index < 0 or dataset_index >= len(hdul):
            raise AstronomyPreviewError("Selected FITS HDU does not exist")
        hdu = hdul[dataset_index]
        if hdu.data is None:
            raise AstronomyPreviewError("Selected FITS HDU has no image data")
        data = hdu.data
        if data.ndim < 2:
            raise AstronomyPreviewError("Selected FITS HDU is not a two-dimensional image or cube")
        leading_shape = data.shape[:-2]
        normalized = []
        for axis, size in enumerate(leading_shape):
            value = slice_indices[axis] if axis < len(slice_indices) else 0
            if value < 0 or value >= size:
                raise AstronomyPreviewError(f"Slice index {value} is outside axis {axis} bounds")
            normalized.append(value)
        plane = np.asarray(data[tuple(normalized)] if normalized else data, dtype=np.float64)
        return plane, hdu.header.copy()


def _tiff_plane(path: Path, dataset_index: int, band: int) -> tuple[np.ndarray, None]:
    import tifffile

    try:
        import rasterio
        with rasterio.open(path) as source:
            if source.crs is not None:
                if band == 0:
                    if source.count < 3:
                        raise AstronomyPreviewError("RGB composite requires at least three GeoTIFF bands")
                    data = source.read([1, 2, 3], masked=True).astype(np.float64).filled(np.nan)
                    return np.moveaxis(data, 0, -1), None
                if band < 1 or band > source.count:
                    raise AstronomyPreviewError("Selected GeoTIFF band does not exist")
                return source.read(band, masked=True).astype(np.float64).filled(np.nan), None
    except AstronomyPreviewError:
        raise
    except Exception:
        pass

    with tifffile.TiffFile(path) as tif:
        if dataset_index < 0 or dataset_index >= len(tif.pages):
            raise AstronomyPreviewError("Selected TIFF page does not exist")
        data = np.asarray(tif.pages[dataset_index].asarray())
    if data.ndim == 2:
        return data.astype(np.float64), None
    if data.ndim == 3:
        if data.shape[-1] <= 16:
            if band == 0 and data.shape[-1] in {3, 4}:
                return np.asarray(data[..., :3], dtype=np.float64), None
            if band < 1 or band > data.shape[-1]:
                raise AstronomyPreviewError("Selected TIFF channel does not exist")
            return np.asarray(data[..., band - 1], dtype=np.float64), None
        if data.shape[0] <= 16:
            if band == 0 and data.shape[0] in {3, 4}:
                return np.moveaxis(np.asarray(data[:3], dtype=np.float64), 0, -1), None
            if band < 1 or band > data.shape[0]:
                raise AstronomyPreviewError("Selected TIFF channel does not exist")
            return np.asarray(data[band - 1], dtype=np.float64), None
    raise AstronomyPreviewError("Selected TIFF page cannot be represented as a two-dimensional plane")


def read_plane(
    entry: AstronomyPreviewEntry,
    dataset_index: int,
    slice_indices: list[int] | None = None,
    band: int = 1,
) -> tuple[np.ndarray, Any]:
    if entry.format == "fits":
        return _fits_plane(entry.path, dataset_index, slice_indices or [])
    return _tiff_plane(entry.path, dataset_index, band)


def _limits(values: np.ndarray, mode: str, low: float | None, high: float | None) -> tuple[float, float]:
    finite = values[np.isfinite(values)]
    if not finite.size:
        raise AstronomyPreviewError("Selected image plane contains no finite values")
    if low is not None and high is not None and high > low:
        return float(low), float(high)
    sample = finite
    if sample.size > 2_000_000:
        sample = sample[:: max(1, sample.size // 2_000_000)]
    if mode == "zscale":
        try:
            from astropy.visualization import ZScaleInterval

            lo, hi = ZScaleInterval().get_limits(sample)
            if math.isfinite(lo) and math.isfinite(hi) and hi > lo:
                return float(lo), float(hi)
        except Exception:
            pass
    lo, hi = np.nanpercentile(sample, [1, 99])
    if not hi > lo:
        lo, hi = float(np.nanmin(sample)), float(np.nanmax(sample))
    if not hi > lo:
        hi = lo + 1.0
    return float(lo), float(hi)


def _colourize(normalized: np.ndarray, colour_map: str) -> np.ndarray:
    value = np.clip(normalized, 0, 1)
    if colour_map == "gray":
        rgb = np.stack([value, value, value], axis=-1)
    elif colour_map == "heat":
        rgb = np.stack([np.clip(value * 3, 0, 1), np.clip(value * 3 - 1, 0, 1), np.clip(value * 3 - 2, 0, 1)], axis=-1)
    elif colour_map == "cool":
        rgb = np.stack([value, 1 - value, np.ones_like(value)], axis=-1)
    else:  # compact viridis approximation
        rgb = np.stack([
            np.clip(0.28 + 0.72 * value - 0.45 * value * value, 0, 1),
            np.clip(0.05 + 1.25 * value - 0.35 * value * value, 0, 1),
            np.clip(0.35 + 0.9 * (1 - np.abs(value - 0.45)), 0, 1),
        ], axis=-1)
    return np.asarray(np.rint(rgb * 255), dtype=np.uint8)


def render_plane(
    entry: AstronomyPreviewEntry,
    *,
    dataset_index: int,
    slice_indices: list[int] | None,
    band: int,
    stretch: str,
    interval: str,
    low: float | None,
    high: float | None,
    colour_map: str,
    invert: bool,
) -> dict[str, Any]:
    plane, header = read_plane(entry, dataset_index, slice_indices, band)
    lo, hi = _limits(plane, interval, low, high)
    if plane.ndim == 3:
        channels = []
        for index in range(3):
            channel = plane[..., index]
            channel_lo, channel_hi = _limits(channel, interval, low, high)
            channels.append(np.clip((channel - channel_lo) / (channel_hi - channel_lo), 0, 1))
        normalized = np.stack(channels, axis=-1)
    else:
        normalized = np.clip((plane - lo) / (hi - lo), 0, 1)
    if stretch == "log":
        normalized = np.log1p(1000 * normalized) / math.log1p(1000)
    elif stretch == "sqrt":
        normalized = np.sqrt(normalized)
    elif stretch == "asinh":
        normalized = np.arcsinh(10 * normalized) / np.arcsinh(10)
    if invert:
        normalized = 1 - normalized
    normalized[~np.isfinite(plane)] = 0
    rgb = np.asarray(np.rint(normalized * 255), dtype=np.uint8) if plane.ndim == 3 else _colourize(normalized, colour_map)
    image = Image.fromarray(rgb, mode="RGB")
    if max(image.size) > _MAX_RENDER_SIDE:
        image.thumbnail((_MAX_RENDER_SIDE, _MAX_RENDER_SIDE), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)

    finite = plane[np.isfinite(plane)]
    sample = finite[:: max(1, finite.size // 500_000)] if finite.size else finite
    counts, edges = np.histogram(sample, bins=96, range=(lo, hi)) if sample.size else (np.zeros(96), np.linspace(lo, hi, 97))
    wcs = _wcs_summary(header, plane.shape) if header is not None else None
    return {
        "image_base64": base64.b64encode(buffer.getvalue()).decode("ascii"),
        "source_width": int(plane.shape[1]),
        "source_height": int(plane.shape[0]),
        "render_width": image.width,
        "render_height": image.height,
        "display_min": lo,
        "display_max": hi,
        "statistics": {
            "valid_count": int(finite.size),
            "missing_count": int(plane.size - finite.size),
            "minimum": _json_number(np.min(finite)) if finite.size else None,
            "maximum": _json_number(np.max(finite)) if finite.size else None,
            "mean": _json_number(np.mean(finite)) if finite.size else None,
            "median": _json_number(np.median(finite)) if finite.size else None,
            "std": _json_number(np.std(finite)) if finite.size else None,
        },
        "histogram": {"counts": counts.astype(int).tolist(), "edges": edges.astype(float).tolist()},
        "wcs": wcs,
    }


def _wcs_summary(header: Any, shape: tuple[int, int]) -> dict[str, Any]:
    from astropy.wcs import WCS

    try:
        wcs = WCS(header).celestial
        if not wcs.has_celestial:
            return {"celestial": False}
        height, width = shape
        corners = np.asarray([[0, 0], [width - 1, 0], [width - 1, height - 1], [0, height - 1]], dtype=float)
        world = wcs.all_pix2world(corners, 0)
        return {
            "celestial": True,
            "axis_types": list(wcs.world_axis_physical_types or []),
            "units": [str(item) for item in (wcs.world_axis_units or [])],
            "corners": world.astype(float).tolist(),
        }
    except Exception:
        return {"celestial": False}


def inspect_pixel(
    entry: AstronomyPreviewEntry,
    *,
    dataset_index: int,
    slice_indices: list[int] | None,
    band: int,
    x: int,
    y: int,
) -> dict[str, Any]:
    plane, header = read_plane(entry, dataset_index, slice_indices, band)
    if x < 0 or y < 0 or y >= plane.shape[0] or x >= plane.shape[1]:
        raise AstronomyPreviewError("Pixel is outside the selected image plane")
    raw_value = plane[y, x]
    pixel_value = [_json_number(item) for item in np.asarray(raw_value).tolist()] if np.ndim(raw_value) else _json_number(raw_value)
    result: dict[str, Any] = {"x": x, "y": y, "value": pixel_value}
    if header is not None:
        try:
            from astropy.wcs import WCS

            wcs = WCS(header).celestial
            if wcs.has_celestial:
                world = wcs.all_pix2world([[x, y]], 0)[0]
                result.update({"ra_deg": float(world[0]), "dec_deg": float(world[1])})
        except Exception:
            pass
    return result


def inspect_region(
    entry: AstronomyPreviewEntry,
    *,
    dataset_index: int,
    slice_indices: list[int] | None,
    band: int,
    x0: int,
    y0: int,
    x1: int,
    y1: int,
) -> dict[str, Any]:
    plane, _ = read_plane(entry, dataset_index, slice_indices, band)
    left, right = sorted((max(0, x0), min(plane.shape[1], x1)))
    top, bottom = sorted((max(0, y0), min(plane.shape[0], y1)))
    if right <= left or bottom <= top:
        raise AstronomyPreviewError("Selected region is empty")
    values = plane[top:bottom, left:right]
    finite = values[np.isfinite(values)]
    return {
        "bounds": [left, top, right, bottom],
        "pixel_count": int(values.size),
        "valid_count": int(finite.size),
        "minimum": _json_number(np.min(finite)) if finite.size else None,
        "maximum": _json_number(np.max(finite)) if finite.size else None,
        "mean": _json_number(np.mean(finite)) if finite.size else None,
        "median": _json_number(np.median(finite)) if finite.size else None,
        "std": _json_number(np.std(finite)) if finite.size else None,
        "sum": _json_number(np.sum(finite)) if finite.size else None,
    }


def detect_sources(
    entry: AstronomyPreviewEntry,
    *,
    dataset_index: int,
    slice_indices: list[int] | None,
    band: int,
    threshold_sigma: float,
) -> dict[str, Any]:
    from scipy.ndimage import gaussian_filter, maximum_filter

    plane, header = read_plane(entry, dataset_index, slice_indices, band)
    if plane.ndim == 3:
        plane = np.nanmean(plane, axis=-1)
    finite = plane[np.isfinite(plane)]
    if not finite.size:
        raise AstronomyPreviewError("Selected image plane contains no finite pixels")
    background = float(np.nanmedian(finite))
    noise = float(1.4826 * np.nanmedian(np.abs(finite - background)))
    if not noise > 0:
        noise = float(np.nanstd(finite))
    if not noise > 0:
        return {"background": background, "noise": noise, "source_count": 0, "sources": []}
    smoothed = gaussian_filter(np.nan_to_num(plane, nan=background), sigma=1.0)
    threshold = background + max(1.0, threshold_sigma) * noise
    peaks = (smoothed == maximum_filter(smoothed, size=5)) & (smoothed > threshold)
    ys, xs = np.where(peaks)
    order = np.argsort(smoothed[ys, xs])[::-1][:2000]
    sources = [
        {"id": index + 1, "x": int(xs[item]), "y": int(ys[item]), "peak": float(plane[ys[item], xs[item]]), "snr": float((plane[ys[item], xs[item]] - background) / noise)}
        for index, item in enumerate(order)
    ]
    if header is not None and sources:
        try:
            from astropy.wcs import WCS

            wcs = WCS(header).celestial
            if wcs.has_celestial:
                world = wcs.all_pix2world([[item["x"], item["y"]] for item in sources], 0)
                for source, coordinate in zip(sources, world):
                    source.update({"ra_deg": float(coordinate[0]), "dec_deg": float(coordinate[1])})
        except Exception:
            pass
    return {"background": background, "noise": noise, "threshold": threshold, "source_count": len(sources), "truncated": int(peaks.sum()) > len(sources), "sources": sources}
