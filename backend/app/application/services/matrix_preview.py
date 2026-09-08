"""Bounded numeric array previews. No pickle loading or source path disclosure."""
from contextlib import contextmanager
import math
import zipfile

import numpy as np
from scipy import io, sparse

from app.application.services.astronomy_preview import AstronomyPreviewCache, AstronomyPreviewError

LIMIT = 128 * 1024 * 1024
matrix_preview_cache = AstronomyPreviewCache({'.npy', '.npz', '.mtx', '.mat'}, max_bytes=LIMIT)


class MatrixPreviewError(ValueError):
    """Public validation messages; library exceptions must not expose host paths."""


def _check(shape, dtype):
    if np.dtype(dtype).kind not in 'biufc':
        raise MatrixPreviewError('仅支持数值或布尔数组，不支持对象、字符串和结构体')
    if len(shape) > 16 or any(int(n) <= 0 for n in shape):
        raise MatrixPreviewError('数组为空或维度超过 16')


def _header(stream):
    version = np.lib.format.read_magic(stream)
    if version == (1, 0):
        shape, _, dtype = np.lib.format.read_array_header_1_0(stream)
    elif version == (2, 0):
        shape, _, dtype = np.lib.format.read_array_header_2_0(stream)
    else:
        raise MatrixPreviewError('不支持此 NPY 头版本')
    _check(shape, dtype)
    return shape, dtype


def describe(entry):
    path = entry.path
    result = []

    def add(name, shape, dtype, is_sparse=False):
        _check(shape, dtype)
        result.append(dict(name=name, shape=list(shape), dtype=str(dtype), sparse=is_sparse))

    if entry.format == 'npy':
        with path.open('rb') as stream:
            shape, dtype = _header(stream)
        add('array', shape, dtype)
    elif entry.format == 'npz':
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > 512 or sum(i.file_size for i in members) > LIMIT:
                raise MatrixPreviewError('NPZ 解压后超过 128 MiB 或变量数量超过 512')
            # scipy sparse NPZ is an encoded matrix, not a collection of user arrays.
            if {'format.npy', 'shape.npy', 'data.npy'} <= set(archive.namelist()):
                matrix = sparse.load_npz(path)
                add('matrix', matrix.shape, matrix.dtype, True)
            else:
                for item in members:
                    if item.filename.endswith('.npy'):
                        with archive.open(item) as stream:
                            try:
                                shape, dtype = _header(stream)
                            except ValueError:
                                continue
                        add(item.filename[:-4], shape, dtype)
    elif entry.format == 'mtx':
        rows, cols, entries, storage, field, _ = io.mminfo(path)
        if entries > 2_000_000 or (storage == 'array' and rows * cols > LIMIT // 16):
            raise MatrixPreviewError('Matrix Market 预览最多支持 200 万个存储值')
        add('matrix', (rows, cols), 'complex128' if field == 'complex' else 'float64', storage == 'coordinate')
    elif entry.format == 'mat':
        import h5py
        if h5py.is_hdf5(path):
            with h5py.File(path, 'r') as store:
                # Root numeric MATLAB variables only; never follow external links.
                for name in list(store.keys())[:512]:
                    if name.startswith('#') or not isinstance(store.get(name, getlink=True), h5py.HardLink):
                        continue
                    value = store[name]
                    if isinstance(value, h5py.Dataset) and not value.is_virtual and not value.external and value.dtype.kind in 'biufc':
                        add(name, value.shape[::-1], value.dtype)
        else:
            for name, shape, kind in io.whosmat(path):
                if kind in {'double', 'single', 'logical', 'sparse', 'int8', 'uint8', 'int16', 'uint16', 'int32', 'uint32', 'int64', 'uint64'}:
                    add(name, shape, 'float64' if kind in {'double', 'single', 'logical', 'sparse'} else kind, kind == 'sparse')
    if not result:
        raise MatrixPreviewError('文件中没有可预览的数值数组；MAT 结构体、单元数组和引用对象暂不支持')
    return {'preview_id': entry.preview_id, 'source_name': entry.source_name, 'arrays': result}


@contextmanager
def _array(entry, info):
    name = info['name']
    path = entry.path
    if entry.format == 'npy':
        value = np.load(path, mmap_mode='r', allow_pickle=False)
        try:
            yield value, False
        finally:
            value._mmap.close()
    elif entry.format == 'npz':
        if info['sparse']:
            yield sparse.load_npz(path).tocoo(), False
        else:
            if math.prod(info['shape']) * np.dtype(info['dtype']).itemsize > LIMIT:
                raise MatrixPreviewError('变量解压后超过预览内存限制')
            with np.load(path, allow_pickle=False) as store:
                yield store[name], False
    elif entry.format == 'mtx':
        value = io.mmread(path)
        yield value.tocoo() if sparse.issparse(value) else value, False
    else:
        import h5py
        if h5py.is_hdf5(path):
            with h5py.File(path, 'r') as store:
                yield store[name], True
        else:
            if math.prod(info['shape']) * 16 > LIMIT:
                raise MatrixPreviewError('该 MAT 变量过大，请先使用分析工具提取较小区域')
            value = io.loadmat(path, variable_names=[name])[name]
            yield value.tocoo() if sparse.issparse(value) else value, False


def render(entry, variable, axes, indices, component):
    arrays = describe(entry)['arrays']
    info = next((item for item in arrays if item['name'] == variable), None)
    if info is None:
        raise MatrixPreviewError('变量不存在')
    shape = info['shape']
    ndim = len(shape)
    if ndim >= 2 and (len(axes) != 2 or len(set(axes)) != 2 or any(a < 0 or a >= ndim for a in axes)):
        raise MatrixPreviewError('请选择两个不同的有效显示轴')
    if len(indices) != ndim or any(i < 0 or i >= n for i, n in zip(indices, shape)):
        raise MatrixPreviewError('切片索引超出范围')
    selected = axes if ndim >= 2 else list(range(ndim))
    slices = [slice(0, n, max(1, math.ceil(n / 256))) if a in selected else indices[a] for a, n in enumerate(shape)]
    with _array(entry, info) as (array, reversed_axes):
        if sparse.issparse(array):
            row_step, col_step = slices[0].step, slices[1].step
            mask = (array.row % row_step == 0) & (array.col % col_step == 0)
            plane = np.zeros((math.ceil(shape[0] / row_step), math.ceil(shape[1] / col_step)), dtype=array.dtype)
            np.add.at(plane, (array.row[mask] // row_step, array.col[mask] // col_step), array.data[mask])
        else:
            plane = np.asarray(array[tuple(slices[::-1] if reversed_axes else slices)])
            if reversed_axes:
                plane = plane.transpose()
        if ndim >= 2 and axes[0] > axes[1]:
            plane = plane.T
        plane = {'magnitude': np.abs, 'real': np.real, 'imaginary': np.imag, 'phase': np.angle}[component](plane)
        # real/imag return views: detach before closing a memory-mapped source.
        plane = np.array(plane, dtype=float, copy=True)
    plane = plane.reshape(1, -1) if plane.ndim < 2 else plane
    finite = plane[np.isfinite(plane)]
    values = [[float(v) if np.isfinite(v) else None for v in row] for row in plane]
    rows = list(range(0, shape[axes[0]], slices[axes[0]].step)) if ndim >= 2 else [0]
    cols_axis = axes[1] if ndim >= 2 else 0
    cols = list(range(0, shape[cols_axis], slices[cols_axis].step)) if ndim else [0]
    return {'values': values, 'rows': rows, 'columns': cols, 'shape': shape,
            'sampled': any(isinstance(s, slice) and s.step > 1 for s in slices),
            'minimum': float(finite.min()) if finite.size else None,
            'maximum': float(finite.max()) if finite.size else None,
            'mean': float(finite.mean()) if finite.size else None,
            'non_finite': int(plane.size - finite.size)}
