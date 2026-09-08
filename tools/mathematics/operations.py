from __future__ import annotations

import base64
import json
import math
import sys
from pathlib import Path

import numpy as np
from scipy import io as scipy_io
from scipy import linalg, sparse


MAX_DENSE_ELEMENTS = 2_000_000
MAX_DECOMPOSITION_ELEMENTS = 1_000_000
MAX_REPORT_VALUES = 256


def fail(message: str) -> None:
    print(json.dumps({"success": False, "error": message}, ensure_ascii=False))
    raise SystemExit(0)


def scalar(value):
    value = value.item() if isinstance(value, np.generic) else value
    if isinstance(value, complex):
        return {"real": float(value.real), "imag": float(value.imag)}
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def serializable(value):
    if isinstance(value, dict):
        return {str(k): serializable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serializable(v) for v in value]
    if isinstance(value, np.ndarray):
        if value.size > MAX_REPORT_VALUES:
            return {"shape": list(value.shape), "sample": [scalar(v) for v in value.reshape(-1)[:MAX_REPORT_VALUES]]}
        return serializable(value.tolist())
    return scalar(value)


def emit(payload: dict) -> None:
    print(json.dumps({"success": True, **serializable(payload)}, ensure_ascii=False))


def finite_values(array: np.ndarray) -> np.ndarray:
    flat = np.asarray(array).reshape(-1)
    if np.issubdtype(flat.dtype, np.number):
        return flat[np.isfinite(flat)]
    fail("数组必须是数值类型")


def numeric_summary(array) -> dict:
    if sparse.issparse(array):
        values = array.data
        size = int(np.prod(array.shape))
        implicit_zeros = size - int(array.nnz)
    else:
        values = np.asarray(array).reshape(-1)
        size = values.size
        implicit_zeros = 0
    sampled = values.size > MAX_DENSE_ELEMENTS
    evaluated = values[np.linspace(0, values.size - 1, MAX_DENSE_ELEMENTS, dtype=np.int64)] if sampled else values
    finite = finite_values(evaluated)
    result = {
        "element_count": int(size),
        "stored_value_count": int(values.size),
        "evaluated_stored_count": int(evaluated.size),
        "sampled": sampled,
        "finite_evaluated_count": int(finite.size),
        "non_finite_evaluated_count": int(evaluated.size - finite.size),
        "zero_count_evaluated": int(np.count_nonzero(finite == 0) + (implicit_zeros if not sampled else 0)),
    }
    if finite.size:
        magnitudes = np.abs(finite)
        result.update({
            "minimum_magnitude": float(magnitudes.min()),
            "maximum_magnitude": float(magnitudes.max()),
            "mean_magnitude": float(magnitudes.mean()),
            "l2_norm": float(np.linalg.norm(finite)),
        })
        if not np.iscomplexobj(finite):
            result.update({
                "minimum": float(finite.min()), "maximum": float(finite.max()),
                "mean": float(finite.mean()), "standard_deviation": float(finite.std()),
                "quantiles": {str(q): float(np.quantile(finite, q)) for q in (0.25, 0.5, 0.75, 0.95)},
            })
    return result


def _select(mapping: dict, variable: str | None, source: str):
    candidates = {str(k): v for k, v in mapping.items() if not str(k).startswith("__") and (sparse.issparse(v) or isinstance(v, np.ndarray)) and np.asarray(v).size}
    if variable:
        if variable not in candidates:
            fail(f"变量不存在: {variable}; 可选变量: {', '.join(candidates) or '无'}")
        return candidates[variable], variable, sorted(candidates)
    if len(candidates) != 1:
        fail(f"{source} 包含 {len(candidates)} 个数组，请通过 variable 明确选择: {', '.join(sorted(candidates)) or '无'}")
    name = next(iter(candidates))
    return candidates[name], name, [name]


def load_array(path_value: str, variable: str | None = None):
    path = Path(path_value)
    if not path.is_file():
        fail("输入文件不存在")
    suffix = path.suffix.lower()
    try:
        if suffix == ".npy":
            value = np.load(path, allow_pickle=False, mmap_mode="r")
            return value, variable or "array", [variable or "array"]
        if suffix == ".npz":
            archive = np.load(path, allow_pickle=False)
            return _select({k: archive[k] for k in archive.files}, variable, "NPZ")
        if suffix == ".mtx":
            value = scipy_io.mmread(path)
            return value, variable or "matrix", [variable or "matrix"]
        if suffix == ".mat":
            return _select(scipy_io.loadmat(path), variable, "MAT")
        if suffix in {".h5", ".hdf5", ".hdf"}:
            import h5py
            found = {}
            with h5py.File(path, "r") as handle:
                def visit(name, obj):
                    if isinstance(obj, h5py.Dataset) and obj.dtype.kind in "biufc" and obj.size:
                        found[name] = obj[()]
                handle.visititems(visit)
            return _select(found, variable, "HDF5")
        if suffix in {".csv", ".tsv", ".txt"}:
            delimiter = "," if suffix == ".csv" else "\t" if suffix == ".tsv" else None
            value = np.genfromtxt(path, delimiter=delimiter, comments="#", dtype=complex if _looks_complex(path) else float)
            if np.asarray(value).size == 0:
                fail("文本数组为空")
            return value, variable or "array", [variable or "array"]
    except SystemExit:
        raise
    except Exception as exc:
        fail(f"数组解析失败: {type(exc).__name__}: {exc}")
    fail("不支持的数组格式；支持 NPY、NPZ、CSV、TSV、TXT、MTX、MAT、H5/HDF5")


def _looks_complex(path: Path) -> bool:
    try:
        return "j" in path.read_text(encoding="utf-8", errors="ignore")[:8192].lower()
    except OSError:
        return False


def dense(value, purpose: str, limit=MAX_DENSE_ELEMENTS) -> np.ndarray:
    count = int(np.prod(value.shape))
    if count > limit:
        fail(f"{purpose} 需要稠密数组，但元素数 {count} 超过安全上限 {limit}")
    result = value.toarray() if sparse.issparse(value) else np.asarray(value)
    if result.dtype.kind not in "biufc":
        fail("数组必须是数值类型")
    return result


def require_ndim(value, ndim: int, kind: str) -> np.ndarray:
    array = dense(value, kind)
    if kind == "向量" and array.ndim == 2 and 1 in array.shape:
        array = array.reshape(-1)
    if array.ndim != ndim:
        fail(f"需要{kind}（{ndim} 维），当前形状为 {list(array.shape)}")
    return array


def require_finite(array: np.ndarray, purpose: str) -> None:
    if not np.all(np.isfinite(array)):
        fail(f"{purpose}要求所有输入值均为有限数")


def save_array(path_value: str, value, variable="array", extras: dict | None = None) -> dict:
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    extras = extras or {}
    variable = "".join(c if c.isalnum() or c == "_" else "_" for c in str(variable)).strip("_") or "array"
    try:
        if suffix == ".npy":
            np.save(path, value.toarray() if sparse.issparse(value) else value, allow_pickle=False)
        elif suffix == ".npz":
            if sparse.issparse(value) and not extras:
                sparse.save_npz(path, value)
            else:
                payload = {variable: value.toarray() if sparse.issparse(value) else value, **extras}
                np.savez_compressed(path, **payload)
        elif suffix == ".mtx":
            scipy_io.mmwrite(path, value)
        elif suffix == ".mat":
            scipy_io.savemat(path, {variable: value.toarray() if sparse.issparse(value) else value, **extras})
        elif suffix in {".h5", ".hdf5", ".hdf"}:
            import h5py
            with h5py.File(path, "w") as handle:
                handle.create_dataset(variable, data=value.toarray() if sparse.issparse(value) else value)
                for name, item in extras.items():
                    handle.create_dataset(name, data=item)
        elif suffix in {".csv", ".tsv", ".txt"}:
            array = dense(value, "文本导出")
            if array.ndim > 2:
                fail("CSV/TSV/TXT 只能保存向量或矩阵")
            np.savetxt(path, array, delimiter="," if suffix == ".csv" else "\t" if suffix == ".tsv" else " ")
        else:
            fail("输出格式不支持；请使用 NPY、NPZ、CSV、TSV、TXT、MTX、MAT、H5/HDF5")
    except SystemExit:
        raise
    except Exception as exc:
        fail(f"结果保存失败: {type(exc).__name__}: {exc}")
    if not path.is_file() or path.stat().st_size == 0:
        fail("结果文件未生成")
    return {"output_path": path.name, "output_size_bytes": path.stat().st_size}


def load(args, prefix=""):
    return load_array(args[prefix + "input_path"], args.get(prefix + "variable"))


def residual(reference, approximation) -> dict:
    numerator = float(np.linalg.norm(reference - approximation))
    denominator = float(np.linalg.norm(reference))
    return {"absolute_residual": numerator, "relative_residual": numerator / denominator if denominator else numerator}


def inspect_tool(args):
    value, variable, variables = load(args)
    shape = list(value.shape)
    size = int(np.prod(shape))
    return {"file": Path(args["input_path"]).name, "variable": variable, "variables": variables,
            "shape": shape, "ndim": len(shape), "dtype": str(value.dtype), "sparse": sparse.issparse(value),
            "storage_format": value.getformat() if sparse.issparse(value) else "dense",
            "density": float(value.nnz / size) if sparse.issparse(value) and size else (float(np.count_nonzero(value) / size) if size else 0),
            "statistics": numeric_summary(value)}


def validate_tool(args):
    value, variable, variables = load(args)
    shape = list(value.shape)
    issues = []
    if int(np.prod(shape)) == 0: issues.append("数组为空")
    values = value.data if sparse.issparse(value) else np.asarray(value)
    if values.dtype.kind not in "biufc": issues.append("不是数值数组")
    elif not np.all(np.isfinite(values)): issues.append("包含 NaN 或无穷值")
    expected = args.get("expected_ndim")
    if expected is not None and len(shape) != int(expected): issues.append(f"维数应为 {expected}，实际为 {len(shape)}")
    constant_axes = []
    if not sparse.issparse(value) and values.size <= MAX_DENSE_ELEMENTS and values.dtype.kind in "biufc":
        for axis in range(values.ndim):
            if values.shape[axis] > 1 and np.all(np.diff(values, axis=axis) == 0): constant_axes.append(axis)
    return {"valid": not issues, "issues": issues, "variable": variable, "variables": variables,
            "shape": shape, "dtype": str(values.dtype), "constant_axes": constant_axes}


def main() -> None:
    if len(sys.argv) != 3: fail("参数错误")
    op = sys.argv[1]
    try:
        encoded = sys.argv[2] + "=" * (-len(sys.argv[2]) % 4)
        args = json.loads(base64.urlsafe_b64decode(encoded).decode())
    except Exception: fail("参数编码无效")

    if op == "math_array_inspect": emit(inspect_tool(args)); return
    if op == "math_array_validate": emit(validate_tool(args)); return
    if op == "math_array_convert":
        value, name, _ = load(args); emit({"shape": list(value.shape), "dtype": str(value.dtype), **save_array(args["output_path"], value, name)}); return

    if op.startswith("math_vector_"):
        a, name, _ = load(args); a = require_ndim(a, 1, "向量")
        if op == "math_vector_statistics": emit({"variable": name, "shape": list(a.shape), "statistics": numeric_summary(a)}); return
        if op == "math_vector_norm":
            require_finite(a, "范数计算"); order = args.get("order", 2); order = np.inf if str(order).lower() in {"inf", "infinity"} else float(order)
            emit({"order": "inf" if order == np.inf else order, "norm": float(np.linalg.norm(a, ord=order))}); return
        if op == "math_vector_normalize":
            require_finite(a, "向量标准化"); method=args.get("method", "l2")
            if np.iscomplexobj(a) and method in {"minmax", "zscore"}: fail("复数向量不支持最小最大值或标准分数标准化")
            if method == "l2": center, scale = 0, np.linalg.norm(a)
            elif method == "maxabs": center, scale = 0, np.max(np.abs(a))
            elif method == "minmax": center, scale = np.min(a), np.max(a)-np.min(a)
            else: center, scale = np.mean(a), np.std(a)
            if scale == 0: fail("向量尺度为零，无法执行所选标准化")
            out=(a-center)/scale; emit({"method": method, "center": center, "scale": scale, **save_array(args["output_path"],out,name)}); return
        if op == "math_vector_visualize":
            require_finite(a, "向量绘图"); plot_vector(a,args["output_path"],args.get("style","line")); emit({"style":args.get("style","line"),"point_count":a.size,"output_path":Path(args["output_path"]).name}); return
        second_prefix = "basis_" if op == "math_vector_projection" else "other_"
        b, _, _ = load(args, second_prefix); b=require_ndim(b,1,"向量")
        if a.shape != b.shape: fail(f"向量形状不一致: {list(a.shape)} 与 {list(b.shape)}")
        require_finite(a,"向量运算"); require_finite(b,"向量运算")
        if op == "math_vector_distance":
            delta=np.abs(a-b); metric=args.get("metric","euclidean")
            value=np.linalg.norm(delta) if metric=="euclidean" else np.sum(delta) if metric=="manhattan" else np.max(delta) if metric=="chebyshev" else np.sum(delta**float(args.get("p",2)))**(1/float(args.get("p",2)))
            emit({"metric":metric,"distance":float(value)}); return
        if op == "math_vector_similarity":
            denom=np.linalg.norm(a)*np.linalg.norm(b)
            cosine=np.vdot(a,b)/denom if denom else None
            pearson=np.corrcoef(a,b)[0,1] if a.size>1 and not np.iscomplexobj(a) and not np.iscomplexobj(b) else None
            emit({"dot_product":np.vdot(a,b),"cosine_similarity":cosine,"pearson_correlation":pearson,"pearson_note":"复数向量不计算皮尔逊相关" if np.iscomplexobj(a) or np.iscomplexobj(b) else None}); return
        if op == "math_vector_arithmetic":
            result=elementwise(a,b,args["operation"]); emit({"operation":args["operation"],**save_array(args["output_path"],result,name)}); return
        if op == "math_vector_projection":
            denominator=np.vdot(b,b)
            if denominator == 0: fail("基向量为零向量")
            coefficient=np.vdot(b,a)/denominator; projected=coefficient*b
            payload={"coefficient":coefficient,"projection_norm":float(np.linalg.norm(projected))}
            if args.get("output_path"): payload.update(save_array(args["output_path"],projected,name))
            emit(payload); return

    if op.startswith("math_matrix_") or op in {"math_linear_system_solve","math_sparse_matrix_profile"}:
        value,name,_=load(args)
        if op == "math_sparse_matrix_profile":
            matrix=value if sparse.issparse(value) else sparse.csr_matrix(require_ndim(value,2,"矩阵"))
            rows=np.diff(matrix.tocsr().indptr); cols=np.diff(matrix.tocsc().indptr); size=int(np.prod(matrix.shape))
            emit({"shape":list(matrix.shape),"format":matrix.getformat(),"nnz":int(matrix.nnz),"density":float(matrix.nnz/size) if size else 0,
                  "row_nnz":{"min":int(rows.min(initial=0)),"max":int(rows.max(initial=0)),"mean":float(rows.mean()) if rows.size else 0},
                  "column_nnz":{"min":int(cols.min(initial=0)),"max":int(cols.max(initial=0)),"mean":float(cols.mean()) if cols.size else 0},"diagonal_nnz":int(np.count_nonzero(matrix.diagonal()))}); return
        if op == "math_matrix_visualize":
            if len(value.shape) != 2: fail(f"需要矩阵（2 维），当前形状为 {list(value.shape)}")
            plot_matrix(value,args["output_path"],args.get("style","heatmap")); emit({"shape":list(value.shape),"style":args.get("style","heatmap"),"output_path":Path(args["output_path"]).name}); return
        if op == "math_matrix_transpose" and sparse.issparse(value):
            result=value.transpose(); emit({"shape":list(result.shape),**save_array(args["output_path"],result,name)}); return
        if op == "math_matrix_statistics" and sparse.issparse(value):
            if len(value.shape) != 2: fail(f"需要矩阵（2 维），当前形状为 {list(value.shape)}")
            matrix=value.tocsr(); square=matrix.shape[0]==matrix.shape[1]
            row_norms=np.sqrt(np.asarray(matrix.multiply(matrix.conjugate()).sum(axis=1)).real.reshape(-1))
            col_norms=np.sqrt(np.asarray(matrix.multiply(matrix.conjugate()).sum(axis=0)).real.reshape(-1))
            symmetric=bool(square and (matrix-matrix.getH()).nnz==0)
            emit({"shape":list(matrix.shape),"rank":None,"rank_note":"稀疏矩阵未自动稠密化，未计算完整秩","trace":matrix.diagonal().sum() if square else None,
                  "frobenius_norm":float(np.linalg.norm(matrix.data)),"row_norms":row_norms,"column_norms":col_norms,"symmetric":symmetric,"statistics":numeric_summary(matrix)}); return
        a=require_ndim(value,2,"矩阵")
        if op == "math_matrix_statistics":
            require_finite(a,"矩阵统计"); square=a.shape[0]==a.shape[1]
            rank=int(np.linalg.matrix_rank(a)) if a.size<=MAX_DECOMPOSITION_ELEMENTS else None
            emit({"shape":list(a.shape),"rank":rank,"rank_note":"矩阵超过分解安全上限，未计算完整秩" if rank is None else None,"trace":np.trace(a) if square else None,"frobenius_norm":float(np.linalg.norm(a)),
                  "row_norms":np.linalg.norm(a,axis=1),"column_norms":np.linalg.norm(a,axis=0),"symmetric":bool(square and np.allclose(a,a.T.conj())),"statistics":numeric_summary(a)}); return
        require_finite(a,"矩阵运算")
        if op == "math_matrix_rank_condition":
            if a.size > MAX_DECOMPOSITION_ELEMENTS: fail(f"秩和条件数计算元素数超过安全上限 {MAX_DECOMPOSITION_ELEMENTS}")
            singular=np.linalg.svd(a,compute_uv=False); rank=int(np.linalg.matrix_rank(a,tol=args.get("tolerance")))
            emit({"shape":list(a.shape),"rank":rank,"singular_values":singular,"condition_number":float(np.linalg.cond(a)),"numerically_invertible":bool(a.shape[0]==a.shape[1] and rank==a.shape[0])}); return
        if op == "math_matrix_determinant_trace":
            if a.shape[0]!=a.shape[1]: fail("迹和行列式要求方阵")
            if a.size > MAX_DECOMPOSITION_ELEMENTS: fail(f"行列式计算元素数超过安全上限 {MAX_DECOMPOSITION_ELEMENTS}")
            sign,logabs=np.linalg.slogdet(a); emit({"trace":np.trace(a),"determinant_sign":sign,"log_absolute_determinant":float(logabs),"determinant":np.linalg.det(a) if abs(logabs)<700 else None}); return
        if op == "math_matrix_transpose": emit({"shape":list(a.T.shape),**save_array(args["output_path"],a.T,name)}); return
        if op in {"math_matrix_arithmetic","math_matrix_multiply"}:
            b,_,_=load(args,"other_"); b=require_ndim(b,2,"矩阵"); require_finite(b,"矩阵运算")
            if op == "math_matrix_multiply":
                if a.shape[1]!=b.shape[0]: fail("矩阵乘法内维不一致")
                if a.shape[0]*b.shape[1]>MAX_DENSE_ELEMENTS: fail("矩阵乘法结果超过安全上限")
                result=a@b; operation="matmul"
            else:
                if a.shape!=b.shape: fail("逐元素矩阵运算要求形状一致")
                operation=args["operation"]; result=elementwise(a,b,operation)
            emit({"operation":operation,"shape":list(result.shape),**save_array(args["output_path"],result,name)}); return
        if op == "math_linear_system_solve":
            b,_,_=load(args,"rhs_"); b=dense(b,"右端项"); require_finite(b,"方程求解")
            if b.ndim not in {1,2} or b.shape[0]!=a.shape[0]: fail("右端项维度与系数矩阵不匹配")
            if a.size > MAX_DECOMPOSITION_ELEMENTS: fail(f"方程求解元素数超过安全上限 {MAX_DECOMPOSITION_ELEMENTS}")
            solution,residuals,rank,singular=np.linalg.lstsq(a,b,rcond=None); approximation=a@solution
            emit({"method":"solve" if a.shape[0]==a.shape[1] and rank==a.shape[0] else "least_squares","rank":int(rank),"condition_number":float(np.linalg.cond(a)),
                  **residual(b,approximation),**save_array(args["output_path"],solution,"solution")}); return
        if op == "math_matrix_inverse":
            if a.shape[0]!=a.shape[1] or a.size>MAX_DECOMPOSITION_ELEMENTS: fail("逆矩阵仅支持安全上限内的方阵")
            inverse=np.linalg.inv(a); ident=np.eye(a.shape[0],dtype=a.dtype); emit({**residual(ident,a@inverse),**save_array(args["output_path"],inverse,"inverse")}); return
        if a.size>MAX_DECOMPOSITION_ELEMENTS: fail(f"矩阵分解元素数超过安全上限 {MAX_DECOMPOSITION_ELEMENTS}")
        if op == "math_matrix_eigen":
            if a.shape[0]!=a.shape[1]: fail("特征分解要求方阵")
            values,vectors=np.linalg.eig(a); error=residual(a@vectors,vectors*values)
            payload={"eigenvalues":values,**error}
            if args.get("vectors",True) and args.get("output_path"): payload.update(save_array(args["output_path"],vectors,"eigenvectors",{"eigenvalues":values}))
            emit(payload); return
        if op == "math_matrix_svd":
            u,s,vh=np.linalg.svd(a,full_matrices=False); k=args.get("components"); k=len(s) if k is None else int(k)
            if not 1<=k<=len(s): fail("components 超出有效范围")
            approximation=(u[:,:k]*s[:k])@vh[:k]; total=float(np.sum(s*s)); payload={"singular_values":s,"components":k,"explained_energy_ratio":float(np.sum(s[:k]**2)/total) if total else 1,**residual(a,approximation)}
            if args.get("output_path"): payload.update(save_array(args["output_path"],u[:,:k],"U",{"S":s[:k],"Vh":vh[:k]}))
            emit(payload); return
        if op == "math_matrix_qr":
            q,r=np.linalg.qr(a,mode="reduced"); payload={**residual(a,q@r),"orthogonality_error":float(np.linalg.norm(q.T.conj()@q-np.eye(q.shape[1])))}
            if args.get("output_path"): payload.update(save_array(args["output_path"],q,"Q",{"R":r}))
            emit(payload); return
        if op == "math_matrix_lu":
            p,l,u=linalg.lu(a); payload=residual(a,p@l@u)
            if args.get("output_path"): payload.update(save_array(args["output_path"],p,"P",{"L":l,"U":u}))
            emit(payload); return
        if op == "math_matrix_cholesky":
            if a.shape[0]!=a.shape[1] or not np.allclose(a,a.T.conj()): fail("Cholesky 分解要求 Hermitian/对称方阵")
            factor=np.linalg.cholesky(a); payload=residual(a,factor@factor.T.conj())
            if args.get("output_path"): payload.update(save_array(args["output_path"],factor,"L"))
            emit(payload); return

    if op.startswith("math_tensor_"):
        value,name,_=load(args); a=dense(value,"张量运算")
        if a.ndim < 1: fail("需要至少一维数组")
        if op == "math_tensor_statistics":
            axes=[]
            for axis in range(a.ndim):
                reduced=np.linalg.norm(a,axis=tuple(i for i in range(a.ndim) if i!=axis)) if a.ndim>1 else np.abs(a)
                axes.append({"axis":axis,"size":a.shape[axis],"norm_min":float(np.min(reduced)),"norm_max":float(np.max(reduced)),"norm_mean":float(np.mean(reduced))})
            emit({"shape":list(a.shape),"ndim":a.ndim,"statistics":numeric_summary(a),"axis_summaries":axes}); return
        require_finite(a,"张量运算")
        if op == "math_tensor_slice":
            axis=normalize_axis(args["axis"],a.ndim); index=int(args["index"])
            if not -a.shape[axis]<=index<a.shape[axis]: fail("切片索引越界")
            result=np.take(a,index,axis=axis); emit({"axis":axis,"index":index,"shape":list(result.shape),**save_array(args["output_path"],result,name)}); return
        if op == "math_tensor_transpose":
            axes=[normalize_axis(v,a.ndim) for v in args["axes"]]
            if sorted(axes)!=list(range(a.ndim)): fail("axes 必须是全部轴的一个排列")
            result=np.transpose(a,axes); emit({"axes":axes,"shape":list(result.shape),**save_array(args["output_path"],result,name)}); return
        if op == "math_tensor_reshape":
            shape=[int(v) for v in args["shape"]]
            if not shape or sum(v==-1 for v in shape)>1 or any(v==0 or v < -1 for v in shape): fail("目标形状无效")
            try: result=np.reshape(a,shape)
            except ValueError: fail("目标形状与元素总数不匹配")
            emit({"original_shape":list(a.shape),"shape":list(result.shape),**save_array(args["output_path"],result,name)}); return
        if op == "math_tensor_reduce":
            axes=tuple(sorted({normalize_axis(v,a.ndim) for v in args["axes"]})); operation=args["operation"]
            if not axes: fail("至少指定一个归约轴")
            if np.iscomplexobj(a) and operation in {"min", "max"}: fail("复数张量不支持最小值或最大值归约")
            result=np.sum(a,axis=axes) if operation=="sum" else np.mean(a,axis=axes) if operation=="mean" else np.min(a,axis=axes) if operation=="min" else np.max(a,axis=axes) if operation=="max" else np.sqrt(np.sum(np.abs(a)**2,axis=axes))
            emit({"operation":operation,"axes":list(axes),"shape":list(np.shape(result)),**save_array(args["output_path"],np.asarray(result),name)}); return
        if op == "math_tensor_contract":
            b,_,_=load(args,"other_"); b=dense(b,"张量缩并"); require_finite(b,"张量缩并")
            axes_a,axes_b=contraction_axes(args["axes"],a.ndim,b.ndim)
            if any(a.shape[left] != b.shape[right] for left,right in zip(axes_a,axes_b)): fail("张量缩并轴尺寸不匹配")
            result_shape=[a.shape[i] for i in range(a.ndim) if i not in axes_a]+[b.shape[i] for i in range(b.ndim) if i not in axes_b]
            if int(np.prod(result_shape,dtype=np.int64))>MAX_DENSE_ELEMENTS: fail("张量缩并结果超过安全上限")
            try: result=np.tensordot(a,b,axes=args["axes"])
            except (ValueError,IndexError,TypeError) as exc: fail(f"张量缩并轴不匹配: {exc}")
            if result.size>MAX_DENSE_ELEMENTS: fail("张量缩并结果超过安全上限")
            emit({"axes":args["axes"],"shape":list(result.shape),**save_array(args["output_path"],result,name)}); return
        if op == "math_tensor_unfold":
            mode=normalize_axis(args["mode"],a.ndim); result=np.moveaxis(a,mode,0).reshape(a.shape[mode],-1)
            emit({"mode":mode,"shape":list(result.shape),**save_array(args["output_path"],result,name)}); return
        if op == "math_tensor_visualize":
            view=tensor_view(a,args.get("slice_indices",[])); plot_matrix(view,args["output_path"],"heatmap"); emit({"source_shape":list(a.shape),"view_shape":list(view.shape),"output_path":Path(args["output_path"]).name}); return
    fail("未知数学工具")


def normalize_axis(axis, ndim):
    axis=int(axis)
    if axis<0: axis+=ndim
    if not 0<=axis<ndim: fail("轴索引越界")
    return axis


def contraction_axes(value, ndim_a, ndim_b):
    if isinstance(value, int):
        if value < 0 or value > min(ndim_a, ndim_b): fail("张量缩并轴数量无效")
        return list(range(ndim_a-value,ndim_a)),list(range(value))
    if not isinstance(value,list) or len(value)!=2 or not all(isinstance(item,list) for item in value): fail("axes 应为整数或两个轴列表")
    left=[normalize_axis(axis,ndim_a) for axis in value[0]]; right=[normalize_axis(axis,ndim_b) for axis in value[1]]
    if len(left)!=len(right) or len(set(left))!=len(left) or len(set(right))!=len(right): fail("张量缩并轴定义无效")
    return left,right


def elementwise(a,b,operation):
    if operation=="add": return a+b
    if operation=="subtract": return a-b
    if operation=="multiply": return a*b
    if np.any(b==0): fail("除数包含零")
    return a/b


def plot_vector(a,path_value,style):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    path=Path(path_value); path.parent.mkdir(parents=True,exist_ok=True)
    x=np.arange(a.size); y=np.abs(a) if np.iscomplexobj(a) else a
    fig,ax=plt.subplots(figsize=(9,4.5))
    if style=="scatter": ax.scatter(x,y,s=8)
    elif style=="histogram": ax.hist(y,bins=min(80,max(10,int(np.sqrt(a.size)))))
    else: ax.plot(x,y,linewidth=1)
    ax.set_title("Vector magnitude" if np.iscomplexobj(a) else "Vector"); ax.grid(alpha=.25); fig.tight_layout(); fig.savefig(path,dpi=150); plt.close(fig)
    if not path.is_file() or path.stat().st_size==0: fail("图像未生成")


def plot_matrix(value,path_value,style):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    path=Path(path_value); path.parent.mkdir(parents=True,exist_ok=True)
    fig,ax=plt.subplots(figsize=(7,6))
    if style=="sparsity":
        matrix=value if sparse.issparse(value) else sparse.csr_matrix(np.asarray(value))
        ax.spy(matrix,markersize=max(.1,min(3,500/max(matrix.shape))))
    else:
        matrix=dense(value,"矩阵热图")
        rows=np.linspace(0,matrix.shape[0]-1,min(matrix.shape[0],1000),dtype=int); cols=np.linspace(0,matrix.shape[1]-1,min(matrix.shape[1],1000),dtype=int)
        shown=np.abs(matrix[np.ix_(rows,cols)]) if np.iscomplexobj(matrix) else matrix[np.ix_(rows,cols)]
        image=ax.imshow(shown,aspect="auto",interpolation="nearest",cmap="viridis"); fig.colorbar(image,ax=ax,shrink=.8)
    ax.set_title("Matrix sparsity" if style=="sparsity" else "Matrix magnitude" if np.iscomplexobj(value) else "Matrix heatmap"); fig.tight_layout(); fig.savefig(path,dpi=150); plt.close(fig)
    if not path.is_file() or path.stat().st_size==0: fail("图像未生成")


def tensor_view(a,indices):
    if a.ndim==1: return a.reshape(1,-1)
    view=a
    for index in indices:
        if view.ndim<=2: fail("slice_indices 数量超过高维轴数量")
        idx=int(index)
        if not -view.shape[0]<=idx<view.shape[0]: fail("张量可视化切片索引越界")
        view=view[idx]
    while view.ndim>2: view=view[0]
    return view


if __name__ == "__main__":
    try:
        main()
    except (np.linalg.LinAlgError, linalg.LinAlgError, ValueError, FloatingPointError) as exc:
        fail(f"数学运算失败: {type(exc).__name__}: {exc}")
