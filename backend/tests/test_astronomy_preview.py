from __future__ import annotations

import numpy as np
from astropy.io import fits
from PIL import Image
import rasterio
from rasterio.transform import from_origin

from app.application.services.astronomy_preview import (
    astronomy_preview_cache,
    detect_sources,
    inspect_pixel,
    inspect_preview,
    inspect_region,
    render_plane,
)


def test_fits_cube_preview_supports_hdu_slice_wcs_pixel_and_region(tmp_path):
    path = tmp_path / "cube.FITS"
    header = fits.Header()
    header.update({
        "CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "CRPIX1": 3.0, "CRPIX2": 2.0,
        "CRVAL1": 120.0, "CRVAL2": 22.0, "CDELT1": -0.01, "CDELT2": 0.01,
    })
    data = np.arange(2 * 4 * 6, dtype=np.float32).reshape(2, 4, 6)
    fits.PrimaryHDU(data=data, header=header).writeto(path)
    with path.open("rb") as stream:
        entry = astronomy_preview_cache.create("owner", "file", path.name, stream, declared_size=path.stat().st_size)
    try:
        metadata = inspect_preview(entry)
        assert metadata["format"] == "FITS"
        assert metadata["datasets"][0]["shape"] == [2, 4, 6]
        rendered = render_plane(entry, dataset_index=0, slice_indices=[1], band=1, stretch="asinh", interval="zscale", low=None, high=None, colour_map="heat", invert=False)
        assert rendered["source_width"] == 6
        assert rendered["source_height"] == 4
        assert rendered["wcs"]["celestial"] is True
        assert rendered["image_base64"]
        pixel = inspect_pixel(entry, dataset_index=0, slice_indices=[1], band=1, x=2, y=1)
        assert pixel["value"] == float(data[1, 1, 2])
        assert "ra_deg" in pixel
        region = inspect_region(entry, dataset_index=0, slice_indices=[1], band=1, x0=1, y0=1, x1=4, y1=3)
        assert region["pixel_count"] == 6
        sources = detect_sources(entry, dataset_index=0, slice_indices=[1], band=1, threshold_sigma=1)
        assert "sources" in sources
    finally:
        astronomy_preview_cache.delete(entry.preview_id, "owner", "file")


def test_multipage_tiff_preview_selects_page_and_channel(tmp_path):
    path = tmp_path / "science.TIFF"
    first = np.arange(20, dtype=np.uint8).reshape(4, 5)
    second = np.dstack([first, first + 10, first + 20]).astype(np.uint8)
    Image.fromarray(first).save(path, save_all=True, append_images=[Image.fromarray(second)])
    with path.open("rb") as stream:
        entry = astronomy_preview_cache.create("owner", "tiff", path.name, stream)
    try:
        metadata = inspect_preview(entry)
        assert metadata["format"] == "TIFF"
        assert len(metadata["datasets"]) == 2
        rendered = render_plane(entry, dataset_index=1, slice_indices=[], band=2, stretch="linear", interval="percentile", low=None, high=None, colour_map="gray", invert=True)
        assert rendered["source_width"] == 5
        assert rendered["statistics"]["minimum"] == 10
    finally:
        astronomy_preview_cache.delete(entry.preview_id, "owner", "tiff")


def test_fits_binary_table_is_bounded_and_previewable(tmp_path):
    path = tmp_path / "catalog.fits"
    table = fits.BinTableHDU.from_columns([
        fits.Column(name="RA", format="D", unit="deg", array=[10.0, np.nan]),
        fits.Column(name="NAME", format="8A", array=["source-a", "source-b"]),
    ], name="CATALOG")
    fits.HDUList([fits.PrimaryHDU(), table]).writeto(path)
    with path.open("rb") as stream:
        entry = astronomy_preview_cache.create("owner", "catalog", path.name, stream)
    try:
        metadata = inspect_preview(entry)
        catalog = metadata["datasets"][1]
        assert catalog["kind"] == "table"
        assert catalog["row_count"] == 2
        assert catalog["columns"][0]["name"] == "RA"
        assert catalog["table_preview"][1]["RA"] is None
    finally:
        astronomy_preview_cache.delete(entry.preview_id, "owner", "catalog")


def test_geotiff_preview_reports_public_map_extent_and_rgb(tmp_path):
    path = tmp_path / "sky-map.tif"
    values = np.stack([np.full((4, 5), value, dtype=np.uint16) for value in (10, 20, 30)])
    with rasterio.open(path, "w", driver="GTiff", width=5, height=4, count=3, dtype="uint16", crs="EPSG:4326", transform=from_origin(100, 30, 0.1, 0.1)) as target:
        target.write(values)
    with path.open("rb") as stream:
        entry = astronomy_preview_cache.create("owner", "geo", path.name, stream)
    try:
        metadata = inspect_preview(entry)
        assert metadata["format"] == "GeoTIFF"
        assert metadata["geospatial"]["bounds_wgs84"] == [100.0, 29.6, 100.5, 30.0]
        rendered = render_plane(entry, dataset_index=0, slice_indices=[], band=0, stretch="linear", interval="percentile", low=None, high=None, colour_map="gray", invert=False)
        assert rendered["source_width"] == 5
        assert rendered["source_height"] == 4
    finally:
        astronomy_preview_cache.delete(entry.preview_id, "owner", "geo")
