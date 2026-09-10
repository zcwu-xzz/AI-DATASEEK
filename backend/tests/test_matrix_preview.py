import io

import h5py
import numpy as np
import pytest
from scipy import io as sio, sparse

from app.application.services.matrix_preview import describe, render, matrix_preview_cache, result_curves
from app.application.services.astronomy_preview import AstronomyPreviewError


@pytest.fixture
def prepare(tmp_path):
    entries = []
    def load(name, writer):
        path = tmp_path / name
        writer(path)
        with path.open('rb') as stream:
            entry = matrix_preview_cache.create('owner', name, name, stream)
        entries.append(entry)
        return entry
    yield load
    for entry in entries:
        matrix_preview_cache.delete(entry.preview_id, 'owner', entry.file_id)


@pytest.mark.parametrize('extension', ['NPY', 'npz', 'mat'])
def test_tensor_slice_and_reversed_axes(prepare, extension):
    value = np.arange(120).reshape(2, 3, 4, 5)
    def write(path):
        with path.open('wb') as stream:
            if extension == 'NPY': np.save(stream, value)
            elif extension == 'npz': np.savez(stream, tensor=value, other=np.ones((2, 2)))
            else: sio.savemat(stream, {'tensor': value})
    entry = prepare('tensor.' + extension, write)
    key = 'array' if extension == 'NPY' else 'tensor'
    result = render(entry, key, [3, 1], [1, 0, 2, 0], 'real')
    np.testing.assert_array_equal(result['values'], value[1, :, 2, :].T)
    assert 'path' not in describe(entry)


def test_mat73_axis_order(prepare):
    value = np.arange(60).reshape(3, 4, 5)
    def write(path):
        with h5py.File(path, 'w') as store:
            store['tensor'] = value.T
            store['external'] = h5py.ExternalLink('/etc/passwd', 'private')
    entry = prepare('tensor.mat', write)
    assert describe(entry)['arrays'][0]['shape'] == [3, 4, 5]
    assert len(describe(entry)['arrays']) == 1
    np.testing.assert_array_equal(render(entry, 'tensor', [0, 2], [0, 2, 0], 'real')['values'], value[:, 2, :])


@pytest.mark.parametrize('extension', ['mtx', 'npz'])
def test_sparse_bounded_sampling(prepare, extension):
    matrix = sparse.eye(10_000, format='csr')
    entry = prepare('sparse.' + extension, lambda path: sio.mmwrite(str(path), matrix) if extension == 'mtx' else sparse.save_npz(path, matrix))
    assert describe(entry)['arrays'][0]['sparse']
    result = render(entry, 'matrix', [0, 1], [0, 0], 'real')
    assert len(result['values']) <= 256
    assert result['sampled']
    assert result['values'][0][0] == 1


def test_nonfinite_complex_and_invalid_axes(prepare):
    value = np.array([[1 + 2j, np.nan], [3j, np.inf]])
    entry = prepare('complex.npy', lambda path: np.save(path, value))
    result = render(entry, 'array', [0, 1], [0, 0], 'imaginary')
    assert result['values'][0][0] == 2
    assert render(entry, 'array', [0, 1], [0, 0], 'real')['non_finite'] == 2
    with pytest.raises(ValueError): render(entry, 'array', [0, 0], [0, 0], 'real')
    with pytest.raises(ValueError): render(entry, 'array', [0, 1], [9, 0], 'real')
    with pytest.raises(AstronomyPreviewError): matrix_preview_cache.get(entry.preview_id, 'other', entry.file_id)
    with pytest.raises(AstronomyPreviewError): matrix_preview_cache.get(entry.preview_id, 'owner', 'other-file')


def test_object_array_rejected(prepare):
    entry = prepare('object.npy', lambda path: np.save(path, np.array([{'a': 1}], dtype=object)))
    with pytest.raises(ValueError, match='对象'): describe(entry)


def test_original_resolution_region_profiles(prepare):
    value = np.arange(600 * 700).reshape(600, 700)
    entry = prepare('region.npy', lambda path: np.save(path, value))
    result = render(entry, 'array', [0, 1], [0, 0], 'real', [301, 310], [451, 460])
    expected = value[301:310, 451:460]
    np.testing.assert_array_equal(result['values'], expected)
    np.testing.assert_allclose(result['row_profile'], expected.mean(axis=1))
    np.testing.assert_allclose(result['column_profile'], expected.mean(axis=0))
    assert result['rows'] == list(range(301, 310))
    assert not result['sampled']
    assert result['standard_deviation'] == expected.std()
    with pytest.raises(ValueError):
        render(entry, 'array', [0, 1], [0, 0], 'real', [0, 601])


def test_sparse_region_structure(prepare):
    matrix = sparse.coo_matrix(([7., -4.], ([301, 309], [451, 459])), shape=(10000, 10000))
    entry = prepare('structure.npz', lambda path: sparse.save_npz(path, matrix))
    result = render(entry, 'matrix', [0, 1], [0, 0], 'real', [301, 310], [451, 460], structure=True)
    assert result['values'][0][0] == 1
    assert result['values'][8][8] == 1
    assert result['nonzero'] == 2
    overview = render(entry, 'matrix', [0, 1], [0, 0], 'real', structure=True)
    assert sum(sum(row) for row in overview['values']) > 0


def test_mathematics_result_curves(prepare):
    entry = prepare('result.npz', lambda path: np.savez(path, S=np.array([4., 3.]),
                    eigenvalues=np.array([1+2j, 1-2j]), residual_history=np.array([1., .1, .001])))
    curves = result_curves(entry)
    assert len(curves) == 4
    energy = next(c for c in curves if '累计能量' in c['title'])
    np.testing.assert_allclose(energy['y'], [.64, 1.])
    eigen = next(c for c in curves if c['kind'] == 'scatter')
    assert eigen['y'] == [2., -2.]


@pytest.mark.parametrize('value', [np.array(42), np.arange(6), np.empty((0, 3))])
def test_scalar_vector_empty(prepare, value):
    entry = prepare('small.npy', lambda path: np.save(path, value))
    if value.size == 0:
        with pytest.raises(ValueError): describe(entry)
    else:
        result = render(entry, 'array', [0, 1], [0] * value.ndim, 'real')
        assert result['values'] == value.reshape(1, -1).tolist()
