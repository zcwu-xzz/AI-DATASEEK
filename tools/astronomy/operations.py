from __future__ import annotations

import base64
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np


class AstronomyError(RuntimeError):
    pass


def decode_args(value: str) -> dict[str, Any]:
    return json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("utf-8"))


def json_value(value: Any) -> Any:
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, np.ndarray):
        return [json_value(item) for item in value.tolist()]
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    return value


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps({"success": True, **json_value(payload)}, ensure_ascii=False, separators=(",", ":")))


def fail(message: str) -> None:
    print(json.dumps({"success": False, "error": message}, ensure_ascii=False))
    raise SystemExit(0)


def path_arg(args: dict[str, Any], key: str = "input_path", *, required: bool = True) -> Path | None:
    value = args.get(key)
    if value in (None, "") and key == "input_path":
        candidates = args.get("input_paths")
        if isinstance(candidates, list) and candidates:
            value = candidates[0]
    if value in (None, "") and not required:
        return None
    if not isinstance(value, str):
        raise AstronomyError(f"{key} is required")
    path = Path(value)
    if key != "output_path" and not path.is_file():
        raise AstronomyError(f"input file does not exist: {path.name}")
    return path


def output_arg(args: dict[str, Any], suffix: str) -> Path:
    value = args.get("output_path")
    if not isinstance(value, str) or not value:
        raise AstronomyError("output_path is required")
    path = Path(value)
    if path.suffix == "":
        path = path.with_suffix(suffix)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def hdu_key(args: dict[str, Any]) -> int | str:
    return args.get("hdu", 0)


def open_fits(path: Path, mode: str = "readonly"):
    from astropy.io import fits

    return fits.open(path, mode=mode, memmap=mode == "readonly", lazy_load_hdus=True, ignore_missing_simple=False)


def image_hdu(path: Path, args: dict[str, Any]):
    hdul = open_fits(path)
    key = hdu_key(args)
    try:
        hdu = hdul[key]
    except Exception:
        hdul.close()
        raise AstronomyError(f"FITS HDU {key!r} was not found")
    if hdu.data is None or np.ndim(hdu.data) < 2:
        hdul.close()
        raise AstronomyError("selected FITS HDU has no image or cube data")
    return hdul, hdu


def plane_from_hdu(hdu: Any, args: dict[str, Any]) -> tuple[np.ndarray, tuple[int, ...]]:
    data = hdu.data
    leading = data.shape[:-2]
    requested = list(args.get("slice_indices") or [])
    indices: list[int] = []
    for axis, size in enumerate(leading):
        index = int(requested[axis]) if axis < len(requested) else 0
        if not 0 <= index < size:
            raise AstronomyError(f"slice index {index} is outside axis {axis} of length {size}")
        indices.append(index)
    plane = np.asarray(data[tuple(indices)] if indices else data, dtype=float)
    return plane, tuple(indices)


def finite_stats(values: np.ndarray) -> dict[str, Any]:
    array = np.asarray(values, dtype=float)
    finite = array[np.isfinite(array)]
    result: dict[str, Any] = {
        "count": int(array.size), "valid_count": int(finite.size),
        "missing_count": int(array.size - finite.size),
    }
    if finite.size:
        result.update({
            "minimum": float(np.min(finite)), "maximum": float(np.max(finite)),
            "mean": float(np.mean(finite)), "median": float(np.median(finite)),
            "std": float(np.std(finite)),
            "quantiles": {str(q): float(np.percentile(finite, q)) for q in (1, 5, 25, 50, 75, 95, 99)},
        })
    return result


def write_fits(path: Path, data: np.ndarray, header: Any, *, history: str) -> None:
    from astropy.io import fits

    header = header.copy()
    header.add_history(history)
    fits.PrimaryHDU(data=np.asarray(data), header=header).writeto(path, overwrite=True, checksum=True)


def read_table(path: Path, hdu: int | str = 1):
    from astropy.table import Table

    if path.suffix.casefold() in {".fit", ".fits", ".fts", ".fz"}:
        return Table.read(path, hdu=hdu)
    try:
        return Table.read(path)
    except Exception:
        return Table.read(path, format="ascii.csv")


def write_table(table: Any, output: Path) -> None:
    suffix = output.suffix.casefold()
    if suffix in {".fit", ".fits", ".fts"}:
        table.write(output, format="fits", overwrite=True)
    elif suffix in {".ecsv"}:
        table.write(output, format="ascii.ecsv", overwrite=True)
    else:
        table.write(output, format="ascii.csv", overwrite=True)


def table_columns(table: Any, args: dict[str, Any], defaults: tuple[str, ...]) -> tuple[np.ndarray, ...]:
    names = {str(name).casefold(): str(name) for name in table.colnames}
    resolved = []
    for key, fallback in zip(("time_column", "value_column", "wavelength_column", "flux_column"), defaults):
        requested = str(args.get(key, fallback)).casefold()
        name = names.get(requested)
        if name is None:
            raise AstronomyError(f"column {requested!r} was not found")
        resolved.append(np.asarray(table[name], dtype=float))
    return tuple(resolved)


def simple_filter_mask(table: Any, expression: str) -> np.ndarray:
    match = re.fullmatch(r"\s*([A-Za-z_][\w.-]*)\s*(==|!=|<=|>=|<|>)\s*(.+?)\s*", expression)
    if not match or match.group(1) not in table.colnames:
        raise AstronomyError("filter_expression must be a simple 'column operator value' expression")
    column, operator, raw = match.groups()
    values = np.asarray(table[column])
    try:
        target: Any = float(raw)
        values = values.astype(float)
    except ValueError:
        target = raw.strip("\"'")
        values = values.astype(str)
    return {"==": np.equal, "!=": np.not_equal, "<": np.less, "<=": np.less_equal, ">": np.greater, ">=": np.greater_equal}[operator](values, target)


def fits_foundation(op: str, args: dict[str, Any]) -> bool:
    if not op.startswith("astronomy_fits_") and op not in {"astronomy_wcs_validate", "astronomy_wcs_cutout", "astronomy_region_statistics"}:
        return False
    source = path_arg(args)
    from astropy.io import fits

    if op == "astronomy_fits_validate":
        issues = []
        try:
            with open_fits(source) as hdul:
                hdus = []
                for index, hdu in enumerate(hdul):
                    try:
                        hdu.verify("exception")
                    except Exception as exc:
                        issues.append({"hdu": index, "message": str(exc)})
                    hdus.append({"index": index, "name": hdu.name, "type": type(hdu).__name__, "shape": list(hdu.shape or []), "cards": len(hdu.header)})
            emit({"file": source.name, "valid": not issues, "issues": issues, "hdus": hdus})
        except Exception as exc:
            emit({"file": source.name, "valid": False, "issues": [{"message": str(exc)}], "hdus": []})
        return True
    if op == "astronomy_fits_checksum":
        output = path_arg(args, "output_path", required=False)
        if output:
            output.parent.mkdir(parents=True, exist_ok=True)
            with open_fits(source) as hdul:
                for hdu in hdul:
                    hdu.add_datasum(); hdu.add_checksum()
                hdul.writeto(output, overwrite=True, checksum=True)
            emit({"file": source.name, "verified": True, "output_path": str(output), "attachments": [str(output)]})
        else:
            with open_fits(source) as hdul:
                checks = [{"hdu": i, "checksum": h.verify_checksum(), "datasum": h.verify_datasum()} for i, h in enumerate(hdul)]
            emit({"file": source.name, "verified": all(item["checksum"] == 1 and item["datasum"] == 1 for item in checks), "checks": checks})
        return True
    if op == "astronomy_fits_hdu_extract":
        output = output_arg(args, ".fits")
        with open_fits(source) as hdul:
            selected = hdul[hdu_key(args)].copy()
        fits.HDUList([fits.PrimaryHDU(), selected] if not isinstance(selected, fits.PrimaryHDU) else [selected]).writeto(output, overwrite=True, checksum=True)
        emit({"selected_hdu": str(hdu_key(args)), "output_path": str(output), "attachments": [str(output)]})
        return True
    if op == "astronomy_fits_image_statistics":
        hdul, hdu = image_hdu(source, args)
        try:
            plane, indices = plane_from_hdu(hdu, args)
            emit({"hdu": str(hdu_key(args)), "slice_indices": indices, "shape": list(plane.shape), "statistics": finite_stats(plane)})
        finally: hdul.close()
        return True
    if op in {"astronomy_fits_table_extract", "astronomy_fits_table_filter"}:
        table = read_table(source, hdu_key(args))
        columns = args.get("columns") or list(table.colnames)
        missing = [name for name in columns if name not in table.colnames]
        if missing: raise AstronomyError(f"table columns not found: {missing}")
        table = table[columns]
        if op.endswith("filter"):
            expression = args.get("filter_expression")
            if not expression: raise AstronomyError("filter_expression is required")
            table = table[simple_filter_mask(table, expression)]
        output = output_arg(args, ".fits" if op.endswith("filter") else ".csv")
        write_table(table, output)
        emit({"row_count": len(table), "columns": list(table.colnames), "output_path": str(output), "attachments": [str(output)]})
        return True
    if op == "astronomy_fits_compress":
        output = output_arg(args, ".fits")
        compress = args.get("operation", "compress") != "decompress"
        with open_fits(source) as hdul:
            result = []
            for index, hdu in enumerate(hdul):
                if compress and getattr(hdu, "is_image", False) and hdu.data is not None:
                    if index == 0:
                        result.append(fits.PrimaryHDU())
                    result.append(fits.CompImageHDU(data=hdu.data, header=hdu.header, name=hdu.name, compression_type="RICE_1"))
                else: result.append(hdu.copy())
        fits.HDUList(result).writeto(output, overwrite=True, checksum=True)
        emit({"operation": "compress" if compress else "decompress", "output_path": str(output), "attachments": [str(output)]})
        return True
    if op == "astronomy_fits_cube_slice":
        output = output_arg(args, ".fits"); hdul, hdu = image_hdu(source, args)
        try:
            plane, indices = plane_from_hdu(hdu, args)
            from astropy.wcs import WCS
            try: header = WCS(hdu.header).celestial.to_header(relax=True)
            except Exception: header = hdu.header
            write_fits(output, plane, header, history=f"DataSeek cube slice {indices}")
        finally: hdul.close()
        emit({"slice_indices": indices, "shape": list(plane.shape), "output_path": str(output), "attachments": [str(output)]})
        return True
    if op == "astronomy_fits_cube_moment":
        output = output_arg(args, ".fits"); hdul, hdu = image_hdu(source, args)
        try:
            data = np.asarray(hdu.data, dtype=float); axis = int(args.get("axis", 0)); order = int(args.get("index", 0))
            if not 0 <= axis < data.ndim: raise AstronomyError("axis is outside the cube dimensions")
            coordinates = np.arange(data.shape[axis], dtype=float)
            shape = [1] * data.ndim; shape[axis] = data.shape[axis]; coord = coordinates.reshape(shape)
            total = np.nansum(data, axis=axis)
            if order == 0: moment = total
            else:
                center = np.nansum(data * coord, axis=axis) / np.where(total == 0, np.nan, total)
                if order == 1: moment = center
                elif order == 2: moment = np.sqrt(np.nansum(data * (coord - np.expand_dims(center, axis)) ** 2, axis=axis) / np.where(total == 0, np.nan, total))
                else: raise AstronomyError("moment order must be 0, 1 or 2")
            write_fits(output, moment, hdu.header, history=f"DataSeek moment {order} along numpy axis {axis}")
        finally: hdul.close()
        emit({"axis": axis, "moment": order, "shape": list(moment.shape), "output_path": str(output), "attachments": [str(output)]})
        return True
    if op in {"astronomy_fits_cube_spectrum", "astronomy_fits_pv_diagram"}:
        hdul, hdu = image_hdu(source, args)
        try:
            data = np.asarray(hdu.data, dtype=float)
            if data.ndim < 3: raise AstronomyError("operation requires a FITS cube")
            if op.endswith("cube_spectrum"):
                x, y = int(args.get("x", 0)), int(args.get("y", 0)); radius = float(args.get("radius", 0))
                yy, xx = np.indices(data.shape[-2:]); mask = (xx - x) ** 2 + (yy - y) ** 2 <= radius ** 2 if radius > 0 else ((xx == x) & (yy == y))
                values = np.nanmean(data[..., mask], axis=-1).reshape(-1)
                table = __import__("astropy.table", fromlist=["Table"]).Table({"channel": np.arange(values.size), "value": values})
            else:
                x0, y0, x1, y1 = (int(args.get(k, 0)) for k in ("x0", "y0", "x1", "y1")); samples = max(abs(x1-x0), abs(y1-y0), 2)
                xs=np.linspace(x0,x1,samples).round().astype(int); ys=np.linspace(y0,y1,samples).round().astype(int)
                pv=data[..., np.clip(ys,0,data.shape[-2]-1),np.clip(xs,0,data.shape[-1]-1)].reshape((-1,samples))
                output=output_arg(args,".fits"); write_fits(output,pv,hdu.header,history="DataSeek position-velocity extraction")
                emit({"shape":list(pv.shape),"output_path":str(output),"attachments":[str(output)]}); return True
        finally: hdul.close()
        output=output_arg(args,".csv"); write_table(table,output); emit({"point_count":len(table),"output_path":str(output),"attachments":[str(output)]}); return True
    if op == "astronomy_wcs_validate":
        from astropy.wcs import WCS
        with open_fits(source) as hdul:
            hdu=hdul[hdu_key(args)]; issues=[]
            try:
                w=WCS(hdu.header); celestial=bool(w.has_celestial); footprint=w.calc_footprint(hdu.header) if celestial and np.ndim(hdu.data)>=2 else None
                if not celestial: issues.append("no celestial WCS axes")
                payload={"celestial":celestial,"pixel_n_dim":w.pixel_n_dim,"world_n_dim":w.world_n_dim,"axis_types":w.world_axis_physical_types,"units":w.world_axis_units,"footprint":footprint,"issues":issues}
            except Exception as exc: payload={"celestial":False,"issues":[str(exc)]}
        emit(payload); return True
    if op == "astronomy_wcs_cutout":
        from astropy.nddata import Cutout2D
        from astropy.wcs import WCS
        output=output_arg(args,".fits"); hdul,hdu=image_hdu(source,args)
        try:
            plane,_=plane_from_hdu(hdu,args); w=WCS(hdu.header).celestial
            if all(key in args for key in ("x0","y0","x1","y1")):
                width=int(args["x1"])-int(args["x0"]); height=int(args["y1"])-int(args["y0"]); position=(int(args["x0"])+width/2,int(args["y0"])+height/2)
            elif all(key in args for key in ("ra","dec","radius")):
                from astropy.coordinates import SkyCoord
                from astropy import units as u
                position=SkyCoord(float(args["ra"]),float(args["dec"]),unit="deg"); width=height=2*float(args["radius"])*u.arcsec
            else: raise AstronomyError("provide x0/y0/x1/y1 or ra/dec/radius")
            cut=Cutout2D(plane,position,(height,width),wcs=w,mode="trim"); write_fits(output,cut.data,cut.wcs.to_header(relax=True),history="DataSeek WCS-aware cutout")
        finally: hdul.close()
        emit({"shape":list(cut.data.shape),"output_path":str(output),"attachments":[str(output)]}); return True
    if op == "astronomy_region_statistics":
        hdul,hdu=image_hdu(source,args)
        try:
            plane,_=plane_from_hdu(hdu,args); region_value=args.get("region")
            if region_value:
                from regions import Regions
                text=Path(region_value).read_text(encoding="utf-8") if Path(str(region_value)).is_file() else str(region_value)
                parsed=Regions.parse(text,format="ds9"); combined=np.zeros(plane.shape,dtype=bool)
                from astropy.wcs import WCS
                wcs=WCS(hdu.header).celestial
                for region in parsed:
                    pixel_region=region.to_pixel(wcs) if hasattr(region,"to_pixel") else region
                    mask=pixel_region.to_mask(mode="center"); image=mask.to_image(plane.shape)
                    if image is not None:combined|=image.astype(bool)
                selected=plane[combined];bounds=None
            else:
                x0,y0,x1,y1=(int(args.get(k,v)) for k,v in (("x0",0),("y0",0),("x1",plane.shape[1]),("y1",plane.shape[0])))
                selected=plane[max(0,y0):min(plane.shape[0],y1),max(0,x0):min(plane.shape[1],x1)];bounds=[x0,y0,x1,y1]
        finally: hdul.close()
        emit({"bounds":bounds,"region":region_value,"statistics":finite_stats(selected)}); return True
    return False


def ccd_tools(op: str, args: dict[str, Any]) -> bool:
    if not op.startswith("astronomy_") or op not in {
        "astronomy_ccd_calibrate","astronomy_bad_pixel_mask","astronomy_cosmic_ray_remove","astronomy_background_model",
        "astronomy_image_register","astronomy_image_stack","astronomy_image_reproject","astronomy_image_mosaic","astronomy_difference_image",
        "astronomy_source_catalog","astronomy_aperture_photometry","astronomy_psf_profile","astronomy_image_quality","astronomy_photometric_calibrate",
    }: return False
    source=path_arg(args); output=path_arg(args,"output_path",required=False)
    hdul,hdu=image_hdu(source,args)
    try: data,_=plane_from_hdu(hdu,args); header=hdu.header.copy()
    finally: hdul.close()
    if op == "astronomy_ccd_calibrate":
        calibrated=data.astype(float)
        for key,sign in (("bias_path",-1),("dark_path",-float(args.get("exposure",1)))):
            p=path_arg(args,key,required=False)
            if p:
                x,y=image_hdu(p,{}); correction,_=plane_from_hdu(y,{}); x.close(); calibrated += sign*correction
        flat=path_arg(args,"flat_path",required=False)
        if flat:
            x,y=image_hdu(flat,{}); correction,_=plane_from_hdu(y,{}); x.close(); normalized=correction/np.nanmedian(correction); calibrated=np.divide(calibrated,normalized,out=np.full_like(calibrated,np.nan),where=normalized!=0)
        if not output: raise AstronomyError("output_path is required")
        output.parent.mkdir(parents=True,exist_ok=True); write_fits(output,calibrated,header,history="DataSeek bias/dark/flat calibration"); emit({"statistics":finite_stats(calibrated),"output_path":str(output),"attachments":[str(output)]}); return True
    if op == "astronomy_bad_pixel_mask":
        median=np.nanmedian(data); mad=np.nanmedian(np.abs(data-median)); mask=~np.isfinite(data)|(np.abs(data-median)>float(args.get("threshold_sigma",8))*1.4826*mad)
        corrected=data.copy(); corrected[mask]=median
        if output: write_fits(output,corrected,header,history="DataSeek bad-pixel interpolation")
        emit({"bad_pixel_count":int(mask.sum()),"bad_pixel_fraction":float(mask.mean()),**({"output_path":str(output),"attachments":[str(output)]} if output else {})}); return True
    if op == "astronomy_cosmic_ray_remove":
        import astroscrappy
        mask,clean=astroscrappy.detect_cosmics(data,gain=float(args.get("gain",1)),readnoise=float(args.get("read_noise",0)),sigclip=float(args.get("threshold_sigma",4.5)))
        if not output: raise AstronomyError("output_path is required")
        write_fits(output,clean,header,history="DataSeek L.A.Cosmic cleaning"); emit({"cosmic_pixel_count":int(mask.sum()),"output_path":str(output),"attachments":[str(output)]}); return True
    if op == "astronomy_background_model":
        from photutils.background import Background2D,MedianBackground
        box=max(8,int(args.get("window",64))); model=Background2D(data,(box,box),filter_size=(3,3),bkg_estimator=MedianBackground()); corrected=data-model.background
        if not output: raise AstronomyError("output_path is required")
        write_fits(output,corrected,header,history="DataSeek 2D background subtraction"); emit({"background_median":float(np.nanmedian(model.background)),"background_rms_median":float(np.nanmedian(model.background_rms)),"output_path":str(output),"attachments":[str(output)]}); return True
    if op in {"astronomy_image_stack","astronomy_image_register","astronomy_image_mosaic"}:
        paths=[Path(item) for item in (args.get("input_paths") or [str(source)])]
        arrays=[]; headers=[]
        for p in paths:
            x,y=image_hdu(p,args); arr,_=plane_from_hdu(y,args); arrays.append(arr); headers.append(y.header.copy()); x.close()
        reference=arrays[0]
        if op == "astronomy_image_register":
            from scipy.signal import fftconvolve
            aligned=[reference]; shifts=[[0,0]]
            for arr in arrays[1:]:
                corr=fftconvolve(np.nan_to_num(reference-reference.mean()),np.nan_to_num(arr-arr.mean())[::-1,::-1],mode="same"); peak=np.unravel_index(np.argmax(corr),corr.shape); shift=np.array(peak)-np.array(corr.shape)//2; aligned.append(np.roll(arr,shift,axis=(0,1))); shifts.append(shift.tolist())
            arrays=aligned
        if op == "astronomy_image_mosaic":
            from reproject.mosaicking import find_optimal_celestial_wcs,reproject_and_coadd
            wcs,shape=find_optimal_celestial_wcs([(arr,head) for arr,head in zip(arrays,headers)]); result,_=reproject_and_coadd([(arr,head) for arr,head in zip(arrays,headers)],wcs,shape_out=shape); out_header=wcs.to_header()
        else:
            result=np.nanmedian(np.stack(arrays),axis=0) if op=="astronomy_image_stack" else arrays[-1]; out_header=headers[0]
        if not output: raise AstronomyError("output_path is required")
        write_fits(output,result,out_header,history=f"DataSeek {op}"); emit({"input_count":len(arrays),**({"shifts":shifts} if op=="astronomy_image_register" else {}),"shape":list(result.shape),"output_path":str(output),"attachments":[str(output)]}); return True
    if op == "astronomy_image_reproject":
        reference=path_arg(args,"reference_path"); x,y=image_hdu(reference,{}); target_shape=y.data.shape[-2:]; target_header=y.header.copy(); x.close()
        from reproject import reproject_interp
        result,footprint=reproject_interp((data,header),target_header,shape_out=target_shape)
        if not output: raise AstronomyError("output_path is required")
        write_fits(output,result,target_header,history="DataSeek WCS reprojection"); emit({"coverage_fraction":float(np.mean(footprint>0)),"output_path":str(output),"attachments":[str(output)]}); return True
    if op == "astronomy_difference_image":
        other=path_arg(args,"other_path"); x,y=image_hdu(other,args); comparison,_=plane_from_hdu(y,args); x.close()
        if comparison.shape!=data.shape: raise AstronomyError("difference images must have the same shape; reproject first")
        finite=np.isfinite(data)&np.isfinite(comparison); scale=np.nanmedian(data[finite])/np.nanmedian(comparison[finite]); difference=data-comparison*scale
        if not output: raise AstronomyError("output_path is required")
        write_fits(output,difference,header,history="DataSeek scaled difference image"); emit({"scale":float(scale),"statistics":finite_stats(difference),"output_path":str(output),"attachments":[str(output)]}); return True
    if op in {"astronomy_source_catalog","astronomy_aperture_photometry","astronomy_psf_profile","astronomy_image_quality"}:
        from astropy.stats import sigma_clipped_stats
        mean,median,std=sigma_clipped_stats(data,sigma=3)
        from photutils.detection import DAOStarFinder
        finder=DAOStarFinder(fwhm=max(1.0,float(args.get("radius",3))),threshold=float(args.get("threshold_sigma",5))*std)
        sources=finder(data-median)
        if sources is None: sources=__import__("astropy.table",fromlist=["Table"]).Table()
        if op == "astronomy_source_catalog":
            if len(sources) and {"xcentroid","ycentroid"}.issubset(sources.colnames):
                try:
                    from astropy.wcs import WCS
                    wcs=WCS(header).celestial
                    if wcs.has_celestial:
                        world=wcs.all_pix2world(np.column_stack([sources["xcentroid"],sources["ycentroid"]]),0);sources["ra_deg"]=world[:,0];sources["dec_deg"]=world[:,1]
                except Exception:pass
            if output: write_table(sources,output)
            emit({"source_count":len(sources),"background":float(median),"noise":float(std),**({"output_path":str(output),"attachments":[str(output)]} if output else {}),"preview":json.loads(sources[:50].to_pandas().to_json(orient="records")) if len(sources) else []}); return True
        if op == "astronomy_aperture_photometry":
            from photutils.aperture import CircularAnnulus,CircularAperture,aperture_photometry
            positions=[(float(args["x"]),float(args["y"]))] if "x" in args else [(float(row["xcentroid"]),float(row["ycentroid"])) for row in sources[:1000]]
            radius=float(args.get("aperture_radius",4)); ann_in=float(args.get("annulus_inner",6)); ann_out=float(args.get("annulus_outer",9)); aper=CircularAperture(positions,r=radius); ann=CircularAnnulus(positions,r_in=ann_in,r_out=ann_out); phot=aperture_photometry(data,[aper,ann]); bg=np.asarray(phot["aperture_sum_1"])/ann.area; phot["net_flux"]=np.asarray(phot["aperture_sum_0"])-bg*aper.area
            if output: write_table(phot,output)
            emit({"source_count":len(phot),"preview":json.loads(phot[:50].to_pandas().to_json(orient="records")),**({"output_path":str(output),"attachments":[str(output)]} if output else {})}); return True
        fwhm=np.asarray(sources["sharpness"],float) if len(sources) and "sharpness" in sources.colnames else np.asarray([])
        payload={"background":float(median),"noise":float(std),"source_count":len(sources),"saturation_fraction":float(np.mean(data>=np.nanpercentile(data,99.99))),"shape":list(data.shape)}
        if fwhm.size: payload["sharpness_median"]=float(np.nanmedian(fwhm))
        emit(payload); return True
    if op == "astronomy_photometric_calibrate":
        table=read_table(source,hdu_key(args)); columns=args.get("columns") or []
        if len(columns)<2: raise AstronomyError("columns must contain instrumental and reference magnitude columns")
        inst=np.asarray(table[columns[0]],float); ref=np.asarray(table[columns[1]],float); delta=ref-inst; zero=float(np.nanmedian(delta)); scatter=float(1.4826*np.nanmedian(np.abs(delta-zero)))
        emit({"zero_point":zero,"robust_scatter":scatter,"star_count":int(np.isfinite(delta).sum())}); return True
    return False


def spectral_tools(op: str, args: dict[str, Any]) -> bool:
    names={"astronomy_spectrum_trace_extract","astronomy_spectrum_continuum","astronomy_spectrum_line_measure","astronomy_spectrum_resample","astronomy_spectrum_coadd","astronomy_spectrum_snr","astronomy_spectrum_redshift","astronomy_spectrum_barycentric"}
    if op not in names:return False
    source=path_arg(args); output=path_arg(args,"output_path",required=False)
    from astropy.table import Table
    if op == "astronomy_spectrum_trace_extract":
        hdul,hdu=image_hdu(source,args)
        try:
            data,_=plane_from_hdu(hdu,args); profile=np.nansum(data,axis=1); center=int(np.nanargmax(profile)); radius=max(1,int(args.get("radius",3))); flux=np.nansum(data[max(0,center-radius):center+radius+1],axis=0); table=Table({"pixel":np.arange(flux.size),"flux":flux})
        finally:hdul.close()
        if not output:raise AstronomyError("output_path is required")
        write_table(table,output);emit({"trace_center":center,"point_count":len(table),"output_path":str(output),"attachments":[str(output)]});return True
    table=read_table(source,hdu_key(args)); wave_name=str(args.get("wavelength_column","wavelength")); flux_name=str(args.get("flux_column","flux"))
    if wave_name not in table.colnames or flux_name not in table.colnames:raise AstronomyError("wavelength or flux column was not found")
    wave=np.asarray(table[wave_name],float); flux=np.asarray(table[flux_name],float); valid=np.isfinite(wave)&np.isfinite(flux); wave,flux=wave[valid],flux[valid]; order=np.argsort(wave);wave,flux=wave[order],flux[order]
    if op == "astronomy_spectrum_continuum":
        degree=max(1,min(9,int(args.get("degree",3))));coeff=np.polyfit(wave,flux,degree);continuum=np.polyval(coeff,wave);mode=args.get("operation","normalize");result=flux/continuum if mode=="normalize" else flux-continuum;out=Table({wave_name:wave,flux_name:flux,"continuum":continuum,"result":result})
        if not output:raise AstronomyError("output_path is required")
        write_table(out,output);emit({"degree":degree,"operation":mode,"output_path":str(output),"attachments":[str(output)]});return True
    if op == "astronomy_spectrum_line_measure":
        from scipy.signal import find_peaks,peak_widths
        baseline=np.nanmedian(flux);noise=1.4826*np.nanmedian(np.abs(flux-baseline));peaks,_=find_peaks(np.abs(flux-baseline),height=float(args.get("threshold_sigma",5))*noise);widths=peak_widths(np.abs(flux-baseline),peaks,rel_height=.5)[0] if peaks.size else []
        lines=[{"index":int(i),"wavelength":float(wave[i]),"peak":float(flux[i]),"fwhm":float(widths[j]*np.nanmedian(np.diff(wave))),"integrated_flux":float(np.trapz(flux[max(0,i-3):i+4]-baseline,wave[max(0,i-3):i+4]))} for j,i in enumerate(peaks[:1000])]
        if output:write_table(Table(rows=lines),output)
        emit({"line_count":len(lines),"noise":float(noise),"lines":lines[:100],**({"output_path":str(output),"attachments":[str(output)]} if output else {})});return True
    if op == "astronomy_spectrum_resample":
        reference=path_arg(args,"reference_path",required=False)
        if reference:
            ref=read_table(reference);grid=np.asarray(ref[wave_name],float)
        else:grid=np.linspace(wave.min(),wave.max(),int(args.get("bins",len(wave))))
        result=np.interp(grid,wave,flux,left=np.nan,right=np.nan);out=Table({wave_name:grid,flux_name:result})
        if not output:raise AstronomyError("output_path is required")
        write_table(out,output);emit({"point_count":len(out),"output_path":str(output),"attachments":[str(output)]});return True
    if op == "astronomy_spectrum_coadd":
        paths=[Path(item) for item in (args.get("input_paths") or [str(source)])];series=[]
        for p in paths:
            t=read_table(p);series.append((np.asarray(t[wave_name],float),np.asarray(t[flux_name],float)))
        lo=max(x.min() for x,_ in series);hi=min(x.max() for x,_ in series);size=int(args.get("bins",max(len(x) for x,_ in series)));grid=np.linspace(lo,hi,size);stack=np.stack([np.interp(grid,x,y) for x,y in series]);combined=np.nanmedian(stack,axis=0);out=Table({wave_name:grid,flux_name:combined,"scatter":np.nanstd(stack,axis=0)})
        if not output:raise AstronomyError("output_path is required")
        write_table(out,output);emit({"input_count":len(paths),"point_count":size,"output_path":str(output),"attachments":[str(output)]});return True
    if op == "astronomy_spectrum_snr":
        window=max(5,int(args.get("window",21)));from scipy.ndimage import median_filter;smooth=median_filter(flux,size=window,mode="nearest");residual=flux-smooth;noise=1.4826*np.nanmedian(np.abs(residual-np.nanmedian(residual)));emit({"point_count":len(flux),"noise":float(noise),"median_signal":float(np.nanmedian(flux)),"snr":float(np.nanmedian(np.abs(smooth))/noise) if noise else None});return True
    if op == "astronomy_spectrum_redshift":
        rest=np.asarray(args.get("rest_wavelengths") or [],float);observed=np.asarray(args.get("observed_wavelengths") or [],float)
        if not rest.size or rest.size!=observed.size:raise AstronomyError("equal-length rest_wavelengths and observed_wavelengths are required")
        z=observed/rest-1;emit({"redshift":float(np.median(z)),"scatter":float(np.std(z)),"line_redshifts":z});return True
    if op == "astronomy_spectrum_barycentric":
        from astropy.coordinates import EarthLocation,SkyCoord
        from astropy.time import Time
        from astropy import units as u
        if not args.get("times") or args.get("ra") is None or args.get("dec") is None:raise AstronomyError("times, ra and dec are required")
        location=EarthLocation.of_site(args.get("location") or "greenwich");target=SkyCoord(float(args["ra"])*u.deg,float(args["dec"])*u.deg);values=[]
        for value in args["times"]:values.append(float(target.radial_velocity_correction(obstime=Time(value),location=location).to_value(u.km/u.s)))
        emit({"correction_km_s":values,"location":str(args.get("location") or "greenwich")});return True
    return False


def lightcurve_tools(op: str,args:dict[str,Any])->bool:
    names={"astronomy_lightcurve_extract","astronomy_lightcurve_clean_bin","astronomy_lightcurve_variability","astronomy_lightcurve_periodogram","astronomy_lightcurve_phase_fold","astronomy_lightcurve_transient"}
    if op not in names:return False
    source=path_arg(args);table=read_table(source,hdu_key(args));tn=str(args.get("time_column","time"));vn=str(args.get("value_column","flux"))
    if tn not in table.colnames or vn not in table.colnames:raise AstronomyError("time or value column was not found")
    t=np.asarray(table[tn],float);y=np.asarray(table[vn],float);valid=np.isfinite(t)&np.isfinite(y);t,y=t[valid],y[valid];order=np.argsort(t);t,y=t[order],y[order];output=path_arg(args,"output_path",required=False)
    from astropy.table import Table
    if op=="astronomy_lightcurve_extract":
        out=Table({tn:t,vn:y});
        if output:write_table(out,output)
        emit({"point_count":len(out),"time_range":[float(t.min()),float(t.max())] if t.size else None,**({"output_path":str(output),"attachments":[str(output)]} if output else {})});return True
    median=np.median(y);mad=np.median(np.abs(y-median));sigma=1.4826*mad or np.std(y);keep=np.abs(y-median)<=float(args.get("threshold_sigma",5))*sigma
    if op=="astronomy_lightcurve_clean_bin":
        t,y=t[keep],y[keep];bins=max(1,int(args.get("bins",100)));edges=np.linspace(t.min(),t.max(),bins+1);which=np.digitize(t,edges)-1;bt=np.array([np.mean(t[which==i]) for i in range(bins) if np.any(which==i)]);by=np.array([np.mean(y[which==i]) for i in range(bins) if np.any(which==i)]);out=Table({tn:bt,vn:by})
        if not output:raise AstronomyError("output_path is required")
        write_table(out,output);emit({"removed_outliers":int((~keep).sum()),"bin_count":len(out),"output_path":str(output),"attachments":[str(output)]});return True
    if op=="astronomy_lightcurve_variability":
        emit({"point_count":len(y),"mean":float(np.mean(y)),"median":float(median),"std":float(np.std(y)),"mad":float(mad),"amplitude":float(np.max(y)-np.min(y)),"reduced_chi_square_constant":float(np.sum(((y-np.mean(y))/(sigma or 1))**2)/max(1,len(y)-1))});return True
    if op=="astronomy_lightcurve_periodogram":
        from astropy.timeseries import LombScargle
        frequency,power=LombScargle(t,y).autopower(minimum_frequency=1/float(args.get("max_period",1000)),maximum_frequency=1/float(args.get("min_period",.01)));top=np.argsort(power)[-10:][::-1];candidates=[{"period":float(1/frequency[i]),"power":float(power[i])} for i in top]
        if output:write_table(Table({"frequency":frequency,"power":power}),output)
        emit({"best_period":candidates[0]["period"] if candidates else None,"candidates":candidates,**({"output_path":str(output),"attachments":[str(output)]} if output else {})});return True
    if op=="astronomy_lightcurve_phase_fold":
        period=args.get("period");
        if period is None:raise AstronomyError("period is required")
        phase=((t-t.min())/float(period))%1;idx=np.argsort(phase);out=Table({"phase":phase[idx],vn:y[idx],tn:t[idx]})
        if not output:raise AstronomyError("output_path is required")
        write_table(out,output);emit({"period":period,"point_count":len(out),"output_path":str(output),"attachments":[str(output)]});return True
    candidates=np.where(np.abs(y-median)>float(args.get("threshold_sigma",5))*sigma)[0];events=[{"index":int(i),"time":float(t[i]),"value":float(y[i]),"sigma":float((y[i]-median)/(sigma or 1))} for i in candidates]
    if output:write_table(Table(rows=events),output)
    emit({"event_count":len(events),"events":events[:1000],**({"output_path":str(output),"attachments":[str(output)]} if output else {})});return True


def catalog_tools(op:str,args:dict[str,Any])->bool:
    names={"astronomy_catalog_cone_filter","astronomy_catalog_crossmatch","astronomy_coordinate_convert","astronomy_proper_motion_propagate","astronomy_angular_separation","astronomy_healpix_bin","astronomy_sky_coverage","astronomy_catalog_merge","astronomy_photometry_convert","astronomy_sed_build"}
    if op not in names:return False
    from astropy.coordinates import SkyCoord
    from astropy import units as u
    from astropy.table import Table,hstack,vstack
    source=path_arg(args);table=read_table(source,hdu_key(args));rn=str(args.get("ra_column","ra"));dn=str(args.get("dec_column","dec"));output=path_arg(args,"output_path",required=False)
    if op in {"astronomy_photometry_convert","astronomy_sed_build"}:
        column=str(args.get("column") or args.get("value_column","flux"));values=np.asarray(table[column],float);operation=args.get("operation","jy_to_abmag")
        if operation=="jy_to_abmag":converted=-2.5*np.log10(values/3631);unit="AB mag"
        elif operation=="abmag_to_jy":converted=3631*10**(-.4*values);unit="Jy"
        elif operation=="jy_to_mjy":converted=values*1000;unit="mJy"
        elif operation=="mjy_to_jy":converted=values/1000;unit="Jy"
        else:converted=values;unit=str(args.get("format") or "input")
        if op=="astronomy_sed_build":
            wave=np.asarray(table[str(args.get("wavelength_column","wavelength"))],float);converted=values;result=Table({"wavelength":wave,"frequency_hz":299792458/(wave*1e-10),"flux":converted});unit="input"
        else:result=Table({column:values,"converted":converted})
        if output:write_table(result,output)
        emit({"row_count":len(result),"output_unit":unit,**({"output_path":str(output),"attachments":[str(output)]} if output else {})});return True
    if rn not in table.colnames or dn not in table.colnames:raise AstronomyError("RA or Dec column was not found")
    coords=SkyCoord(np.asarray(table[rn],float)*u.deg,np.asarray(table[dn],float)*u.deg,frame="icrs")
    if op=="astronomy_catalog_cone_filter":
        center=SkyCoord(float(args["ra"])*u.deg,float(args["dec"])*u.deg);mask=coords.separation(center)<=float(args.get("radius_deg",1))*u.deg;result=table[mask]
    elif op=="astronomy_catalog_crossmatch":
        other=read_table(path_arg(args,"other_path"));othercoords=SkyCoord(np.asarray(other[rn],float)*u.deg,np.asarray(other[dn],float)*u.deg);idx,sep,_=coords.match_to_catalog_sky(othercoords);mask=sep<=float(args.get("join_radius_arcsec",1))*u.arcsec;result=hstack([table[mask],other[idx[mask]]],table_names=["left","right"],uniq_col_name="{col_name}_{table_name}");result["match_distance_arcsec"]=sep[mask].arcsec
    elif op=="astronomy_coordinate_convert":
        target=coords.transform_to(str(args.get("target_frame","galactic")));result=table.copy();result["target_lon_deg"]=target.spherical.lon.deg;result["target_lat_deg"]=target.spherical.lat.deg
    elif op=="astronomy_proper_motion_propagate":
        from astropy.time import Time
        pmra=np.asarray(table[str(args.get("pmra_column","pmra"))],float)*u.mas/u.yr;pmdec=np.asarray(table[str(args.get("pmdec_column","pmdec"))],float)*u.mas/u.yr;c=SkyCoord(ra=coords.ra,dec=coords.dec,pm_ra_cosdec=pmra,pm_dec=pmdec,obstime=Time(float(args.get("epoch",2000)),format="jyear"));moved=c.apply_space_motion(Time(float(args.get("target_epoch",2000)),format="jyear"));result=table.copy();result["target_ra_deg"]=moved.ra.deg;result["target_dec_deg"]=moved.dec.deg
    elif op=="astronomy_angular_separation":
        other=read_table(path_arg(args,"other_path"));othercoords=SkyCoord(np.asarray(other[rn],float)*u.deg,np.asarray(other[dn],float)*u.deg);count=min(len(coords),len(othercoords));result=Table({"separation_arcsec":coords[:count].separation(othercoords[:count]).arcsec,"position_angle_deg":coords[:count].position_angle(othercoords[:count]).deg})
    elif op=="astronomy_healpix_bin":
        try:
            from astropy_healpix import HEALPix
            nside=max(1,int(args.get("bins",64)));hp=HEALPix(nside=nside,order="nested",frame="icrs");pixels=hp.skycoord_to_healpix(coords);unique,counts=np.unique(pixels,return_counts=True);result=Table({"healpix":unique,"count":counts})
        except ImportError:raise AstronomyError("astropy-healpix is required for HEALPix binning")
    elif op=="astronomy_sky_coverage":
        xyz=np.mean(coords.cartesian.xyz,axis=1);center=SkyCoord(x=xyz[0],y=xyz[1],z=xyz[2],representation_type="cartesian",frame="icrs");radius=np.max(center.separation(coords));wrapped=coords.ra.wrap_at(180*u.deg).deg;emit({"source_count":len(coords),"center_ra_deg":float(center.spherical.lon.deg),"center_dec_deg":float(center.spherical.lat.deg),"radius_deg":float(radius.deg),"ra_wrapped_range_deg":[float(np.min(wrapped)),float(np.max(wrapped))],"dec_range_deg":[float(np.min(coords.dec.deg)),float(np.max(coords.dec.deg))]});return True
    else:
        paths=[Path(item) for item in (args.get("input_paths") or [str(source)])];result=vstack([read_table(p) for p in paths],metadata_conflicts="silent")
    if output:write_table(result,output)
    emit({"row_count":len(result),"columns":list(result.colnames),**({"output_path":str(output),"attachments":[str(output)]} if output else {})});return True


def main() -> None:
    try:
        if len(sys.argv) != 3: raise AstronomyError("tool name and encoded arguments are required")
        op=sys.argv[1];args=decode_args(sys.argv[2])
        if fits_foundation(op,args) or ccd_tools(op,args) or spectral_tools(op,args) or lightcurve_tools(op,args) or catalog_tools(op,args):return
        raise AstronomyError(f"unknown astronomy tool: {op}")
    except AstronomyError as exc: fail(str(exc))
    except Exception as exc: fail(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__": main()
