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


def result_curves(entry):
    """Read recognized numerical result arrays, never execute result contents."""
    curves = []
    labels = {'S': '奇异值谱', 'singular_values': '奇异值谱',
              'residual_history': '迭代残差', 'eigenvalues': '特征值分布'}
    for info in describe(entry)['arrays']:
        if info['name'] not in labels or math.prod(info['shape']) > 4096:
            continue
        with _array(entry, info) as (array, reversed_axes):
            values = np.array(array, copy=True).reshape(-1)
        if not np.all(np.isfinite(values)):
            continue
        if info['name'] == 'eigenvalues':
            curves.append({'title': labels[info['name']], 'kind': 'scatter',
                           'x': values.real.tolist(), 'y': values.imag.tolist()})
        else:
            values = values.real.astype(float)
            curves.append({'title': labels[info['name']], 'kind': 'line',
                           'x': list(range(len(values))), 'y': values.tolist()})
            if info['name'] in {'S', 'singular_values'}:
                energy = np.cumsum(values * values)
                if energy.size and energy[-1] > 0:
                    curves.append({'title': '已保存奇异值的累计能量比例', 'kind': 'line',
                                   'x': list(range(len(values))), 'y': (energy/energy[-1]).tolist()})
    return curves


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


def _bounded_range(start, end, size, label):
    start = 0 if start is None else int(start)
    end = size if end is None else int(end)
    if start < 0 or end <= start or end > size:
        raise MatrixPreviewError(f'{label}范围超出数组边界')
    return start, end


def render(entry, variable, axes, indices, component, row_range=None, column_range=None,
           max_points=256, structure=False):
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
    if not 16 <= int(max_points) <= 512:
        raise MatrixPreviewError('每轴显示点数应在 16 到 512 之间')
    selected = axes if ndim >= 2 else list(range(ndim))
    display_ranges = {}
    if ndim >= 2:
        display_ranges[axes[0]] = _bounded_range(*(row_range or [None, None]), shape[axes[0]], '行')
        display_ranges[axes[1]] = _bounded_range(*(column_range or [None, None]), shape[axes[1]], '列')
    elif ndim == 1:
        display_ranges[0] = _bounded_range(*(column_range or [None, None]), shape[0], '列')
    slices = []
    for axis, size in enumerate(shape):
        if axis in selected:
            start, end = display_ranges[axis]
            slices.append(slice(start, end, max(1, math.ceil((end - start) / int(max_points)))))
        else:
            slices.append(indices[axis])
    with _array(entry, info) as (array, reversed_axes):
        if sparse.issparse(array):
            if ndim != 2 or axes != [0, 1]:
                raise MatrixPreviewError('稀疏矩阵仅支持以轴 0、1 显示')
            row_start, row_end = display_ranges[0]
            col_start, col_end = display_ranges[1]
            row_step, col_step = slices[0].step, slices[1].step
            matrix = array.tocsr()[row_start:row_end, col_start:col_end].tocoo()
            mask = (matrix.row % row_step == 0) & (matrix.col % col_step == 0)
            if structure:
                # Bin all nonzero entries so overview never loses off-grid structure.
                matrix.sum_duplicates()
                mask = matrix.data != 0
            plane = np.zeros((math.ceil((row_end-row_start) / row_step), math.ceil((col_end-col_start) / col_step)), dtype=array.dtype)
            data = np.ones(np.count_nonzero(mask), dtype=float) if structure else matrix.data[mask]
            np.add.at(plane, (matrix.row[mask] // row_step, matrix.col[mask] // col_step), data)
            region_nnz = int(matrix.nnz)
        else:
            plane = np.asarray(array[tuple(slices[::-1] if reversed_axes else slices)])
            if reversed_axes:
                plane = plane.transpose()
            region_nnz = int(np.count_nonzero(plane))
        if ndim >= 2 and axes[0] > axes[1]:
            plane = plane.T
        if structure:
            plane = np.asarray(plane != 0, dtype=float)
        else:
            plane = {'magnitude': np.abs, 'real': np.real, 'imaginary': np.imag, 'phase': np.angle}[component](plane)
        # real/imag return views: detach before closing a memory-mapped source.
        plane = np.array(plane, dtype=float, copy=True)
    plane = plane.reshape(1, -1) if plane.ndim < 2 else plane
    finite = plane[np.isfinite(plane)]
    values = [[float(v) if np.isfinite(v) else None for v in row] for row in plane]
    rows = list(range(slices[axes[0]].start, slices[axes[0]].stop, slices[axes[0]].step)) if ndim >= 2 else [0]
    cols_axis = axes[1] if ndim >= 2 else 0
    cols = list(range(slices[cols_axis].start, slices[cols_axis].stop, slices[cols_axis].step)) if ndim else [0]
    row_profile = np.nanmean(np.where(np.isfinite(plane), plane, np.nan), axis=1)
    column_profile = np.nanmean(np.where(np.isfinite(plane), plane, np.nan), axis=0)
    return {'values': values, 'rows': rows, 'columns': cols, 'shape': shape,
            'sampled': any(isinstance(s, slice) and s.step > 1 for s in slices),
            'row_step': slices[axes[0]].step if ndim >= 2 else 1,
            'column_step': slices[cols_axis].step if ndim else 1,
            'row_range': list(display_ranges.get(axes[0], (0, 1))) if ndim >= 2 else [0, 1],
            'column_range': list(display_ranges.get(cols_axis, (0, 1))) if ndim else [0, 1],
            'structure': bool(structure), 'nonzero': region_nnz,
            'finite_count': int(finite.size), 'count': int(plane.size),
            'minimum': float(finite.min()) if finite.size else None,
            'maximum': float(finite.max()) if finite.size else None,
            'mean': float(finite.mean()) if finite.size else None,
            'standard_deviation': float(finite.std()) if finite.size else None,
            'row_profile': [float(v) if np.isfinite(v) else None for v in row_profile],
            'column_profile': [float(v) if np.isfinite(v) else None for v in column_profile],
            'non_finite': int(plane.size - finite.size)}
