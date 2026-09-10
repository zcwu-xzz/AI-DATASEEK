"""Advanced deterministic mathematical operators used by the mathematics plugin."""
from __future__ import annotations

import ast
import json
import math
from pathlib import Path

import numpy as np
from scipy import integrate, interpolate, linalg, optimize, sparse
from scipy.sparse import csgraph, linalg as spla


TOOL_NAMES = {
    'math_sparse_convert', 'math_sparse_clean', 'math_sparse_submatrix', 'math_sparse_arithmetic',
    'math_sparse_direct_solve', 'math_sparse_iterative_solve', 'math_sparse_least_squares',
    'math_sparse_eigen', 'math_sparse_svd', 'math_sparse_reorder',
    'math_matrix_properties', 'math_matrix_subspaces', 'math_matrix_pseudoinverse',
    'math_matrix_equilibrate', 'math_matrix_low_rank', 'math_matrix_block', 'math_matrix_compare',
    'math_linear_system_sensitivity', 'math_fit_robust_least_squares', 'math_regularized_inverse',
    'math_nonlinear_curve_fit', 'math_polynomial_fit', 'math_scalar_root', 'math_nonlinear_system',
    'math_constrained_optimize', 'math_linear_program', 'math_interpolate', 'math_differentiate',
    'math_integrate', 'math_ode_ivp', 'math_ode_bvp', 'math_tensor_mode_product',
    'math_tensor_multilinear_rank', 'math_tensor_tucker', 'math_tensor_cp', 'math_tensor_compare',
    'math_array_missing_handle', 'math_array_outlier_detect', 'math_result_validate', 'math_report_generate',
}

MAX_DENSE = 2_000_000


def load_mat73(path, variable=None):
    import h5py
    found = {}
    with h5py.File(path, 'r') as store:
        for name in list(store.keys())[:512]:
            link = store.get(name, getlink=True)
            value = store[name] if isinstance(link, h5py.HardLink) else None
            if isinstance(value, h5py.Dataset) and not value.is_virtual and not value.external and value.dtype.kind in 'biufc' and value.size:
                found[name] = np.asarray(value[()]).transpose()
    from operations import _select
    return _select(found, variable, 'MATLAB 7.3')


def _ops():
    import operations
    return operations


def _load(args, prefix=''):
    return _ops().load_array(args[prefix + 'input_path'], args.get(prefix + 'variable'))[0]


def _dense(value, purpose='运算'):
    return _ops().dense(value, purpose, MAX_DENSE)


def _matrix(value, dense=True):
    if sparse.issparse(value):
        if len(value.shape) != 2: raise ValueError('需要二维矩阵')
        return _dense(value) if dense else value
    value = _dense(value) if dense else np.asarray(value)
    if value.ndim != 2: raise ValueError(f'需要二维矩阵，当前形状为 {list(value.shape)}')
    return value


def _vector(value):
    value = _dense(value)
    if value.ndim == 2 and 1 in value.shape: value = value.reshape(-1)
    if value.ndim != 1: raise ValueError('需要一维向量')
    return value


def _finite(value):
    if not np.all(np.isfinite(value)): raise ValueError('输入包含 NaN 或无穷值')


def _save(args, value, name='result', extras=None):
    return _ops().save_array(args['output_path'], value, name, extras)


def _residual(reference, approximation):
    return _ops().residual(np.asarray(reference), np.asarray(approximation))


def _relative(norm, reference):
    denominator = float(np.linalg.norm(reference))
    return float(norm / denominator) if denominator else float(norm)


def _as_sparse(value, fmt='csr'):
    result = value if sparse.issparse(value) else sparse.coo_matrix(_matrix(value))
    return result.asformat(fmt)


def _safe(expr):
    allowed_names = {'x', 'sin', 'cos', 'tan', 'exp', 'log', 'sqrt', 'abs', 'pi'}
    allowed_nodes = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Call, ast.Name, ast.Load, ast.Subscript,
                     ast.Constant, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.USub, ast.UAdd)
    tree = ast.parse(expr, mode='eval')
    for node in ast.walk(tree):
        if not isinstance(node, allowed_nodes): raise ValueError('表达式包含不允许的语法')
        if isinstance(node, ast.Name) and node.id not in allowed_names: raise ValueError(f'不允许的名称: {node.id}')
        if isinstance(node, ast.Call) and (not isinstance(node.func, ast.Name) or node.func.id not in allowed_names - {'x', 'pi'}):
            raise ValueError('表达式函数不在允许列表')
    code = compile(tree, '<math-expression>', 'eval')
    namespace = {'sin': np.sin, 'cos': np.cos, 'tan': np.tan, 'exp': np.exp,
                 'log': np.log, 'sqrt': np.sqrt, 'abs': np.abs, 'pi': np.pi}
    return lambda x: eval(code, {'__builtins__': {}}, {**namespace, 'x': x})


def _history_callback(history, matrix, rhs):
    def callback(value):
        residual = value if np.isscalar(value) else rhs - matrix @ value
        history.append(float(abs(residual) if np.isscalar(residual) else np.linalg.norm(residual)))
    return callback


def _sparse_tools(op, args):
    a = _as_sparse(_load(args), args.get('format', 'csr'))
    if op == 'math_sparse_convert':
        return {'shape': list(a.shape), 'format': a.format, 'nnz': int(a.nnz), **_save(args, a, 'matrix')}
    if op == 'math_sparse_clean':
        before = int(a.nnz); a = a.tocsr(copy=True); a.sum_duplicates(); a.eliminate_zeros(); a.sort_indices()
        return {'shape': list(a.shape), 'nnz_before': before, 'nnz_after': int(a.nnz), 'removed': before-int(a.nnz), **_save(args, a, 'matrix')}
    if op == 'math_sparse_submatrix':
        rows = args.get('rows'); cols = args.get('columns')
        rs = np.asarray(rows, dtype=int) if rows is not None else np.arange(*args.get('row_range', [0, a.shape[0]]))
        cs = np.asarray(cols, dtype=int) if cols is not None else np.arange(*args.get('column_range', [0, a.shape[1]]))
        if rs.size == 0 or cs.size == 0 or np.any(rs < 0) or np.any(rs >= a.shape[0]) or np.any(cs < 0) or np.any(cs >= a.shape[1]): raise ValueError('子矩阵索引无效')
        out = a.tocsr()[rs][:, cs]
        return {'shape': list(out.shape), 'nnz': int(out.nnz), **_save(args, out, 'matrix')}
    if op == 'math_sparse_arithmetic':
        b = _as_sparse(_load(args, 'other_'))
        action = args.get('operation', 'add')
        if action == 'matmul':
            if a.shape[1] != b.shape[0]: raise ValueError('矩阵乘法内维不一致')
            out = a @ b
        else:
            if a.shape != b.shape: raise ValueError('逐元素运算要求形状一致')
            out = a + b if action == 'add' else a - b if action == 'subtract' else a.multiply(b)
        return {'operation': action, 'shape': list(out.shape), 'nnz': int(out.nnz), **_save(args, out, 'matrix')}
    rhs = _vector(_load(args, 'rhs_')) if 'rhs_input_path' in args else None
    if op in {'math_sparse_direct_solve', 'math_sparse_iterative_solve', 'math_sparse_least_squares'}:
        if rhs is None or rhs.shape[0] != a.shape[0]: raise ValueError('右端向量长度不匹配')
        _finite(a.data); _finite(rhs)
    if op == 'math_sparse_direct_solve':
        if a.shape[0] != a.shape[1]: raise ValueError('直接求解要求方阵')
        x = spla.spsolve(a.tocsc(), rhs)
        if not np.all(np.isfinite(x)): raise ValueError('矩阵奇异或求解失败')
        r = rhs-a@x
        return {'converged': True, 'residual_norm': float(np.linalg.norm(r)), 'relative_residual': _relative(np.linalg.norm(r), rhs), **_save(args, x, 'solution')}
    if op == 'math_sparse_iterative_solve':
        method = args.get('method', 'cg'); history=[]; tolerance=float(args.get('tolerance', 1e-8)); maxiter=int(args.get('max_iterations', 1000))
        methods={'cg':spla.cg,'bicgstab':spla.bicgstab,'gmres':spla.gmres,'minres':spla.minres}
        if method not in methods: raise ValueError('迭代方法无效')
        if method in {'cg','minres'} and a.shape[0] != a.shape[1]: raise ValueError('所选方法要求方阵')
        kwargs={'rtol':tolerance,'atol':0,'maxiter':maxiter,'callback':_history_callback(history,a,rhs)}
        if tolerance <= 0 or maxiter < 1: raise ValueError('容差和迭代次数必须为正')
        if method == 'minres': kwargs.pop('atol')
        if method == 'gmres': kwargs['callback_type']='pr_norm'
        x, info = methods[method](a, rhs, **kwargs)
        r=rhs-a@x
        return {'method':method,'converged':info==0,'status':int(info),'iterations':len(history),'residual_history':history[-200:],
                'residual_norm':float(np.linalg.norm(r)),'relative_residual':_relative(np.linalg.norm(r),rhs),**_save(args,x,'solution',{'residual_history':np.asarray(history)})}
    if op == 'math_sparse_least_squares':
        method=args.get('method','lsmr'); solver=spla.lsmr if method=='lsmr' else spla.lsqr
        result=solver(a,rhs,atol=float(args.get('tolerance',1e-8)),btol=float(args.get('tolerance',1e-8)),iter_lim=int(args.get('max_iterations',1000))) if method=='lsqr' else solver(a,rhs,atol=float(args.get('tolerance',1e-8)),btol=float(args.get('tolerance',1e-8)),maxiter=int(args.get('max_iterations',1000)))
        x=result[0]; r=rhs-a@x
        return {'method':method,'status':int(result[1]),'converged':int(result[1]) in {0,1,2},'iterations':int(result[2]),
                'residual_norm':float(np.linalg.norm(r)),'relative_residual':_relative(np.linalg.norm(r),rhs),**_save(args,x,'solution')}
    if op in {'math_sparse_eigen','math_sparse_svd'}:
        k=int(args.get('components', 6)); smallest=args.get('which','largest')=='smallest'
        limit=min(a.shape) - 1
        if not 1 <= k <= limit: raise ValueError(f'分量数应在 1 到 {limit} 之间')
        if op == 'math_sparse_eigen':
            if a.shape[0] != a.shape[1]: raise ValueError('特征值计算要求方阵')
            symmetric=(a-a.getH()).nnz==0
            values,vectors=(spla.eigsh(a,k=k,which='SM' if smallest else 'LM') if symmetric else spla.eigs(a,k=k,which='SM' if smallest else 'LM'))
            residuals=[_relative(np.linalg.norm(a@vectors[:,i]-values[i]*vectors[:,i]),a@vectors[:,i]) for i in range(k)]
            return {'eigenvalues':values,'symmetric':symmetric,'maximum_relative_residual':max(residuals),**(_save(args,vectors,'eigenvectors',{'eigenvalues':values}) if args.get('output_path') else {})}
        u,s,vh=spla.svds(a,k=k,which='SM' if smallest else 'LM'); order=np.argsort(s)[::-1]; s=s[order]; u=u[:,order]; vh=vh[order]
        norm=float(spla.norm(a)); captured=float(np.sum(s*s)); error=float(np.sqrt(max(norm*norm-captured,0))/max(norm,np.finfo(float).eps)) if not smallest else None
        return {'singular_values':s,'relative_residual':error,**(_save(args,u,'U',{'S':s,'Vh':vh}) if args.get('output_path') else {})}
    if op == 'math_sparse_reorder':
        if a.shape[0] != a.shape[1]: raise ValueError('带宽重排序要求方阵')
        graph=(abs(a)+abs(a).T).tocsr(); permutation=csgraph.reverse_cuthill_mckee(graph,symmetric_mode=True)
        out=a.tocsr()[permutation][:,permutation]
        def bandwidth(m):
            row,col=m.nonzero(); return int(np.max(np.abs(row-col),initial=0))
        return {'bandwidth_before':bandwidth(a),'bandwidth_after':bandwidth(out),'permutation':permutation,**_save(args,out,'matrix',{'permutation':permutation})}
    raise ValueError('未知稀疏矩阵工具')


def _matrix_tools(op, args):
    a=_matrix(_load(args)); _finite(a)
    if op == 'math_matrix_properties':
        tol=float(args.get('tolerance',1e-10)); square=a.shape[0]==a.shape[1]
        symmetric=bool(square and np.allclose(a,a.T.conj(),atol=tol,rtol=tol))
        eigmin=None; positive=None
        if symmetric and a.size<=MAX_DENSE:
            eigmin=float(np.min(np.linalg.eigvalsh(a)).real); positive=eigmin>tol
        diagonal=np.abs(np.diag(a)) if square else np.array([])
        dominance=bool(square and np.all(diagonal+tol>=np.sum(np.abs(a),axis=1)-diagonal))
        orthogonal=bool(square and np.allclose(a.T.conj()@a,np.eye(a.shape[0]),atol=tol,rtol=tol))
        return {'shape':list(a.shape),'tolerance':tol,'symmetric_or_hermitian':symmetric,'positive_definite':positive,
                'minimum_eigenvalue':eigmin,'diagonally_dominant':dominance,'orthogonal_or_unitary':orthogonal}
    if op == 'math_matrix_subspaces':
        if max(a.shape)**2 > MAX_DENSE: raise ValueError('完整子空间基超过输出元素上限，请先提取较小矩阵')
        u,s,vh=np.linalg.svd(a,full_matrices=True); tol=float(args.get('tolerance') or max(a.shape)*np.finfo(s.dtype).eps*s[0]); rank=int(np.sum(s>tol))
        column=u[:,:rank]; row=vh[:rank].T.conj(); null=vh[rank:].T.conj(); left=u[:,rank:]
        return {'rank':rank,'nullity':null.shape[1],'left_nullity':left.shape[1],'tolerance':tol,
                **_save(args,column,'column_space',{'row_space':row,'null_space':null,'left_null_space':left,'singular_values':s})}
    if op == 'math_matrix_pseudoinverse':
        rcond=float(args.get('rcond',1e-15)); out=np.linalg.pinv(a,rcond=rcond)
        return {'rcond':rcond,'rank':int(np.linalg.matrix_rank(a,tol=rcond*np.linalg.norm(a,2))),
                'penrose_residual_1':_relative(np.linalg.norm(a@out@a-a),a),'penrose_residual_2':_relative(np.linalg.norm(out@a@out-out),out),**_save(args,out,'pseudoinverse')}
    if op == 'math_matrix_equilibrate':
        row=np.max(np.abs(a),axis=1); row=np.where(row>0,1/row,1); first=row[:,None]*a
        col=np.max(np.abs(first),axis=0); col=np.where(col>0,1/col,1); out=first*col
        before=float(np.linalg.cond(a)); after=float(np.linalg.cond(out))
        return {'condition_before':before,'condition_after':after,'row_scale':row,'column_scale':col,**_save(args,out,'balanced',{'row_scale':row,'column_scale':col})}
    if op == 'math_matrix_low_rank':
        u,s,vh=np.linalg.svd(a,full_matrices=False); energy=float(args.get('energy',0.99)); rank=args.get('rank')
        if rank is None:
            rank=int(np.searchsorted(np.cumsum(s*s)/max(np.sum(s*s),np.finfo(float).eps),energy)+1)
        rank=int(rank)
        if not 1<=rank<=len(s): raise ValueError('目标秩无效')
        out=(u[:,:rank]*s[:rank])@vh[:rank]
        return {'rank':rank,'explained_energy':float(np.sum(s[:rank]**2)/max(np.sum(s*s),np.finfo(float).eps)),
                **_residual(a,out),**_save(args,out,'approximation',{'singular_values':s[:rank]})}
    if op == 'math_matrix_block':
        action=args.get('operation','extract')
        if action=='extract':
            rr=args.get('row_range',[0,a.shape[0]]); cc=args.get('column_range',[0,a.shape[1]]); out=a[slice(*rr),slice(*cc)]
        else:
            b=_matrix(_load(args,'other_')); _finite(b)
            if action=='horizontal': out=np.hstack([a,b])
            elif action=='vertical': out=np.vstack([a,b])
            elif action=='block_diagonal': out=linalg.block_diag(a,b)
            else: raise ValueError('分块操作无效')
        return {'operation':action,'shape':list(out.shape),**_save(args,out,'matrix')}
    if op == 'math_linear_system_sensitivity':
        rhs=_vector(_load(args,'other_')); _finite(rhs)
        if a.shape[0]!=rhs.size: raise ValueError('右端项长度不匹配')
        seed=int(args.get('random_seed',0)); scale=float(args.get('perturbation',1e-6)); trials=int(args.get('trials',20))
        if not 1<=trials<=1000 or scale<=0: raise ValueError('扰动参数无效')
        baseline=np.linalg.lstsq(a,rhs,rcond=None)[0]; rng=np.random.default_rng(seed); changes=[]
        for _ in range(trials):
            da=rng.normal(size=a.shape); da*=scale*np.linalg.norm(a)/max(np.linalg.norm(da),1e-30)
            db=rng.normal(size=rhs.shape); db*=scale*np.linalg.norm(rhs)/max(np.linalg.norm(db),1e-30)
            changed=np.linalg.lstsq(a+da,rhs+db,rcond=None)[0]
            changes.append(_relative(np.linalg.norm(changed-baseline),baseline))
        return {'random_seed':seed,'trials':trials,'relative_perturbation':scale,'condition_number':float(np.linalg.cond(a)),
                'solution_relative_change_mean':float(np.mean(changes)),'solution_relative_change_maximum':float(np.max(changes)),
                **(_save(args,baseline,'baseline_solution') if args.get('output_path') else {})}
    b=_matrix(_load(args,'other_')); _finite(b)
    if op == 'math_matrix_compare':
        if a.shape!=b.shape: return {'same_shape':False,'shape_a':list(a.shape),'shape_b':list(b.shape)}
        difference=a-b; atol=float(args.get('absolute_tolerance',1e-8)); rtol=float(args.get('relative_tolerance',1e-5)); mask=~np.isclose(a,b,atol=atol,rtol=rtol)
        flat=np.argwhere(mask); sample=[{'index':v.tolist(),'a':a[tuple(v)],'b':b[tuple(v)],'difference':difference[tuple(v)]} for v in flat[:100]]
        return {'same_shape':True,'equal_within_tolerance':not np.any(mask),'different_count':int(np.count_nonzero(mask)),
                'maximum_absolute_error':float(np.max(np.abs(difference),initial=0)),'relative_frobenius_error':_relative(np.linalg.norm(difference),a),
                'nonzero_pattern_difference':int(np.count_nonzero((a!=0)!=(b!=0))),'difference_sample':sample}
    raise ValueError('未知矩阵诊断工具')


def _fit_tools(op,args):
    if op in {'math_fit_robust_least_squares','math_regularized_inverse'}:
        a=_matrix(_load(args)); y=_vector(_load(args,'rhs_')); _finite(a); _finite(y)
        if a.shape[0]!=y.size: raise ValueError('设计矩阵与观测向量不匹配')
    if op=='math_fit_robust_least_squares':
        weights=np.ones(y.size)
        if args.get('weights_input_path'): weights=_vector(_load({'input_path':args['weights_input_path'],'variable':args.get('weights_variable')}))
        if weights.shape!=y.shape or np.any(weights<=0): raise ValueError('权重必须为正且与观测等长')
        loss=args.get('loss','soft_l1'); result=optimize.least_squares(lambda p:np.sqrt(weights)*(a@p-y),np.zeros(a.shape[1]),loss=loss,max_nfev=int(args.get('max_iterations',1000)))
        residual=y-a@result.x
        return {'converged':bool(result.success),'status':int(result.status),'message':result.message,'iterations':int(result.nfev),
                'cost':float(result.cost),'residual_norm':float(np.linalg.norm(residual)),**_save(args,result.x,'parameters')}
    if op=='math_regularized_inverse':
        strength=float(args.get('regularization',1e-3)); regularizer=np.eye(a.shape[1])
        if args.get('regularizer_input_path'): regularizer=_matrix(_load({'input_path':args['regularizer_input_path'],'variable':args.get('regularizer_variable')}))
        lhs=a.T.conj()@a+strength*(regularizer.T.conj()@regularizer); solution=np.linalg.solve(lhs,a.T.conj()@y); residual=y-a@solution
        return {'regularization':strength,'residual_norm':float(np.linalg.norm(residual)),'regularization_norm':float(np.linalg.norm(regularizer@solution)),
                'normal_equation_residual':float(np.linalg.norm(lhs@solution-a.T.conj()@y)),**_save(args,solution,'solution')}
    x=_vector(_load(args)); y=_vector(_load(args,'other_')); _finite(x); _finite(y)
    if x.shape!=y.shape: raise ValueError('坐标与观测长度不一致')
    if op=='math_polynomial_fit':
        degree=int(args.get('degree',1)); weights=None
        if not 0<=degree<min(x.size,100): raise ValueError('多项式次数无效')
        coefficients=np.polynomial.polynomial.polyfit(x,y,degree,w=weights); fitted=np.polynomial.polynomial.polyval(x,coefficients); residual=y-fitted
        return {'degree':degree,'coefficients_ascending':coefficients,'r_squared':float(1-np.sum(residual**2)/max(np.sum((y-y.mean())**2),np.finfo(float).eps)),
                'residual_norm':float(np.linalg.norm(residual)),**_save(args,fitted,'fitted',{'coefficients':coefficients})}
    if op=='math_nonlinear_curve_fit':
        model=args.get('model','gaussian')
        funcs={'gaussian':lambda x,a,c,w,b:a*np.exp(-.5*((x-c)/w)**2)+b,'exponential':lambda x,a,k,b:a*np.exp(k*x)+b,
               'lorentzian':lambda x,a,c,w,b:a/(1+((x-c)/w)**2)+b,'logistic':lambda x,L,k,c,b:L/(1+np.exp(-k*(x-c)))+b}
        if model not in funcs: raise ValueError('拟合模型无效')
        defaults = [float(np.ptp(y)),float(x[np.argmax(y)]),max(float(np.std(x)),1e-6),float(np.min(y))]
        if model == 'exponential': defaults = [float(np.ptp(y)), 1., float(np.min(y))]
        if model == 'logistic': defaults = [float(np.ptp(y)), 1., float(np.median(x)), float(np.min(y))]
        initial=np.asarray(args.get('initial_parameters',defaults),float)
        if initial.size != len(defaults): raise ValueError('初始参数数量与拟合模型不匹配')
        lower=np.asarray(args.get('lower_bounds',[-np.inf]*len(initial))); upper=np.asarray(args.get('upper_bounds',[np.inf]*len(initial)))
        params,cov=optimize.curve_fit(funcs[model],x,y,p0=initial,bounds=(lower,upper),maxfev=int(args.get('max_iterations',10000)))
        fitted=funcs[model](x,*params); residual=y-fitted
        return {'model':model,'parameters':params,'parameter_standard_errors':np.sqrt(np.maximum(np.diag(cov),0)),
                'residual_norm':float(np.linalg.norm(residual)),'r_squared':float(1-np.sum(residual**2)/max(np.sum((y-y.mean())**2),np.finfo(float).eps)),**_save(args,fitted,'fitted',{'parameters':params,'covariance':cov})}
    raise ValueError('未知拟合工具')


def _optimization_tools(op,args):
    if op=='math_scalar_root':
        f=_safe(args['expression']); method=args.get('method','brentq')
        if method=='brentq':
            bracket=args.get('bracket');
            if not isinstance(bracket,list) or len(bracket)!=2: raise ValueError('区间法需要两个端点')
            result=optimize.root_scalar(f,bracket=bracket,method='brentq',xtol=float(args.get('tolerance',1e-10)),maxiter=int(args.get('max_iterations',100)))
        else:
            result=optimize.root_scalar(f,x0=float(args.get('initial',0)),x1=float(args.get('second_initial',1)),method='secant',xtol=float(args.get('tolerance',1e-10)),maxiter=int(args.get('max_iterations',100)))
        return {'root':float(result.root),'converged':bool(result.converged),'iterations':int(result.iterations),'function_value':float(f(result.root)),'method':method}
    if op=='math_nonlinear_system':
        expressions=args['expressions']; initial=np.asarray(args['initial'],float)
        if not expressions or len(expressions)!=initial.size or initial.size>100: raise ValueError('方程数量与初值维度不一致或超过上限')
        funcs=[_safe(e) for e in expressions]
        def system(x): return np.asarray([f(x) for f in funcs],float)
        result=optimize.root(system,initial,method=args.get('method','hybr'),options={'maxfev':int(args.get('max_iterations',1000))})
        residual=system(result.x)
        return {'solution':result.x,'converged':bool(result.success),'status':int(result.status),'message':str(result.message),'evaluations':int(result.nfev),
                'residual_norm':float(np.linalg.norm(residual)),**(_save(args,result.x,'solution') if args.get('output_path') else {})}
    if op=='math_constrained_optimize':
        # Deterministic quadratic objective 0.5*x'Q*x+c'x with linear constraints.
        q=_matrix(_load(args)); c=_vector(_load(args,'rhs_')); _finite(q); _finite(c)
        if q.shape[0]!=q.shape[1] or q.shape[0]!=c.size: raise ValueError('二次目标矩阵与线性项维度不匹配')
        initial=np.asarray(args.get('initial',[0.0]*c.size),float)
        bounds=args.get('bounds'); scipy_bounds=None
        if bounds is not None:
            if len(bounds) != c.size or any(len(pair) != 2 for pair in bounds): raise ValueError('变量边界维度不匹配')
            lower_bounds=np.asarray([-np.inf if pair[0] is None else pair[0] for pair in bounds],float)
            upper_bounds=np.asarray([np.inf if pair[1] is None else pair[1] for pair in bounds],float)
            if np.any(np.isnan(lower_bounds)) or np.any(np.isnan(upper_bounds)) or np.any(lower_bounds>upper_bounds): raise ValueError('变量边界无效')
            scipy_bounds=optimize.Bounds(lower_bounds,upper_bounds)
        q=(q+q.T.conj())/2
        constraints=[]
        if args.get('constraint_input_path'):
            matrix=_matrix(_load({'input_path':args['constraint_input_path'],'variable':args.get('constraint_variable')})); lower=np.asarray(args.get('constraint_lower',[-np.inf]*matrix.shape[0])); upper=np.asarray(args.get('constraint_upper',[np.inf]*matrix.shape[0])); constraints=[optimize.LinearConstraint(matrix,lower,upper)]
        objective=lambda x:.5*x@q@x+c@x; jac=lambda x:q@x+c
        result=optimize.minimize(objective,initial,jac=jac,bounds=scipy_bounds,constraints=constraints,method='SLSQP',options={'maxiter':int(args.get('max_iterations',1000)),'ftol':float(args.get('tolerance',1e-9))})
        violation=0.0
        if constraints:
            values=matrix@result.x; violation=float(max(np.max(lower-values,initial=0),np.max(values-upper,initial=0)))
        return {'solution':result.x,'objective':float(result.fun),'converged':bool(result.success),'status':int(result.status),'iterations':int(result.nit),'constraint_violation':violation,'message':str(result.message),**_save(args,result.x,'solution')}
    if op=='math_linear_program':
        c=_vector(_load(args)); _finite(c); aub=bub=aeq=beq=None
        if args.get('inequality_input_path'):
            aub=_matrix(_load({'input_path':args['inequality_input_path'],'variable':args.get('inequality_variable')})); bub=np.asarray(args['inequality_upper'],float)
        if args.get('equality_input_path'):
            aeq=_matrix(_load({'input_path':args['equality_input_path'],'variable':args.get('equality_variable')})); beq=np.asarray(args['equality_value'],float)
        bounds=args.get('bounds',[[0,None]]*c.size)
        result=optimize.linprog(c,A_ub=aub,b_ub=bub,A_eq=aeq,b_eq=beq,bounds=bounds,method='highs')
        payload={'converged':bool(result.success),'status':int(result.status),'message':str(result.message),'iterations':int(result.nit),'objective':float(result.fun) if result.success else None}
        if result.x is not None: payload.update({'solution':result.x,**_save(args,result.x,'solution')})
        return payload
    raise ValueError('未知优化工具')


def _calculus_tools(op,args):
    x=_vector(_load(args)); _finite(x)
    if op in {'math_interpolate','math_differentiate','math_integrate'}:
        y=_dense(_load(args,'other_')); _finite(y)
        if y.shape[0]!=x.size: raise ValueError('坐标轴长度必须与数据第一维一致')
        order=np.argsort(x); x=x[order]; y=y[order]
        if np.any(np.diff(x)<=0): raise ValueError('坐标必须唯一且严格递增')
    if op=='math_interpolate':
        target=_vector(_load(args,'target_')); method=args.get('method','linear'); extrapolate=bool(args.get('extrapolate',False))
        if not extrapolate and (target.min()<x.min() or target.max()>x.max()): raise ValueError('目标坐标超出范围；如需外推请显式启用')
        fn=interpolate.interp1d(
            x,
            y,
            axis=0,
            kind=method,
            bounds_error=not extrapolate,
            fill_value='extrapolate' if extrapolate else np.nan,
        )
        out=fn(target); return {'method':method,'point_count':target.size,'extrapolated':bool(np.any((target<x.min())|(target>x.max()))),**_save(args,out,'interpolated',{'coordinates':target})}
    if op=='math_differentiate':
        edge=int(args.get('edge_order',2)); derivative=np.gradient(y,x,axis=0,edge_order=edge)
        return {'edge_order':edge,'shape':list(derivative.shape),**_save(args,derivative,'derivative',{'coordinates':x})}
    if op=='math_integrate':
        method=args.get('method','trapezoid')
        value=integrate.simpson(y,x=x,axis=0) if method=='simpson' else integrate.trapezoid(y,x=x,axis=0)
        cumulative=integrate.cumulative_trapezoid(y,x=x,axis=0,initial=0)
        return {'method':method,'integral':value,**_save(args,cumulative,'cumulative_integral',{'coordinates':x})}
    # ODE input is a parameter matrix A for y'=Ay+forcing, a common reproducible scientific form.
    matrix=_matrix(_load(args,'other_')); initial=np.asarray(args['initial'],float)
    if matrix.shape[0]!=matrix.shape[1] or initial.size!=matrix.shape[0]: raise ValueError('系统矩阵和初始/边界维度不匹配')
    span=args.get('interval',[float(x.min()),float(x.max())])
    if op=='math_ode_ivp':
        forcing=np.asarray(args.get('forcing',[0.0]*initial.size),float)
        result=integrate.solve_ivp(lambda t,y:matrix@y+forcing,span,initial,t_eval=x,method=args.get('method','RK45'),rtol=float(args.get('relative_tolerance',1e-6)),atol=float(args.get('absolute_tolerance',1e-9)))
        residual=np.gradient(result.y,result.t,axis=1)-(matrix@result.y+forcing[:,None])
        return {'converged':bool(result.success),'message':result.message,'evaluations':int(result.nfev),'residual_rms':float(np.sqrt(np.mean(residual**2))),**_save(args,result.y.T,'solution',{'coordinates':result.t})}
    # Second-order linear BVP: y''=A y, with y(left)=initial, y(right)=final.
    final=np.asarray(args['final'],float)
    if final.shape!=initial.shape: raise ValueError('两端边界值维度不一致')
    position=np.linspace(initial,final,x.size).T; velocity=np.gradient(position,x,axis=1)
    guess=np.vstack([position,velocity]); n=initial.size
    result=integrate.solve_bvp(lambda t,z:np.vstack([z[n:],matrix@z[:n]]),lambda ya,yb:np.concatenate([ya[:n]-initial,yb[:n]-final]),x,guess,tol=float(args.get('tolerance',1e-5)),max_nodes=int(args.get('max_nodes',10000)))
    values=result.sol(x)[:n].T
    return {'converged':bool(result.success),'status':int(result.status),'message':result.message,'iterations':int(result.niter),'nodes':int(result.x.size),'residual_rms':float(np.sqrt(np.mean(result.rms_residuals**2))),**_save(args,values,'solution',{'coordinates':x})}


def _tensor_tools(op,args):
    a=_dense(_load(args)); _finite(a)
    if a.ndim<2: raise ValueError('需要至少二维张量')
    if op=='math_tensor_mode_product':
        matrix=_matrix(_load(args,'other_')); axis=int(args.get('mode',0))%a.ndim
        if matrix.shape[1]!=a.shape[axis]: raise ValueError('矩阵列数与目标模尺寸不一致')
        out=np.moveaxis(np.tensordot(matrix,a,axes=(1,axis)),0,axis)
        return {'mode':axis,'shape':list(out.shape),**_save(args,out,'tensor')}
    if op=='math_tensor_multilinear_rank':
        spectra=[]; ranks=[]; energy=float(args.get('energy',0.99))
        for mode in range(a.ndim):
            unfolded=np.moveaxis(a,mode,0).reshape(a.shape[mode],-1); s=np.linalg.svd(unfolded,compute_uv=False)
            rank=int(np.searchsorted(np.cumsum(s*s)/max(np.sum(s*s),np.finfo(float).eps),energy)+1)
            ranks.append(rank); spectra.append({'mode':mode,'rank':rank,'singular_values':s[:min(100,len(s))]})
        return {'shape':list(a.shape),'energy_threshold':energy,'multilinear_rank':ranks,'mode_spectra':spectra}
    if op in {'math_tensor_tucker','math_tensor_cp'}:
        import tensorly as tl
        from tensorly.decomposition import parafac, tucker
        tl.set_backend('numpy'); seed=int(args.get('random_seed',0)); iterations=int(args.get('max_iterations',200)); tolerance=float(args.get('tolerance',1e-6))
        if op=='math_tensor_tucker':
            ranks=args.get('ranks')
            if ranks is None or len(ranks)!=a.ndim or any(not 1<=int(r)<=n for r,n in zip(ranks,a.shape)): raise ValueError('Tucker 秩必须为每个模指定有效维数')
            core,factors=tucker(a,rank=[int(v) for v in ranks],init='svd',tol=tolerance,n_iter_max=iterations,random_state=seed)
            reconstructed=tl.tucker_to_tensor((core,factors)); extras={'core':core,**{f'factor_{i}':v for i,v in enumerate(factors)}}
            return {'ranks':[int(v) for v in ranks],'random_seed':seed,'initialization':'svd',**_residual(a,reconstructed),**_save(args,core,'core',extras)}
        rank=int(args.get('rank',1));
        if not 1<=rank<=min(256,a.size): raise ValueError('CP 秩无效')
        cp=parafac(a,rank=rank,init='svd',tol=tolerance,n_iter_max=iterations,random_state=seed,normalize_factors=True)
        reconstructed=tl.cp_to_tensor(cp); extras={'weights':cp.weights,**{f'factor_{i}':v for i,v in enumerate(cp.factors)}}
        return {'rank':rank,'random_seed':seed,'initialization':'svd',**_residual(a,reconstructed),**_save(args,cp.weights,'weights',extras)}
    if op=='math_tensor_compare':
        b=_dense(_load(args,'other_')); _finite(b)
        if a.shape!=b.shape: return {'same_shape':False,'shape_a':list(a.shape),'shape_b':list(b.shape)}
        d=np.abs(a-b); flat=int(np.argmax(d)); index=list(np.unravel_index(flat,d.shape)); axes=[]
        for axis in range(a.ndim):
            reduced=np.max(d,axis=tuple(i for i in range(a.ndim) if i!=axis))
            axes.append({'axis':axis,'maximum_error':float(np.max(reduced)),'index_of_maximum':int(np.argmax(reduced))})
        return {'same_shape':True,'maximum_absolute_error':float(d.flat[flat]),'maximum_error_index':index,
                'mean_absolute_error':float(np.mean(d)),'root_mean_square_error':float(np.sqrt(np.mean(d*d))),
                'relative_error':_relative(np.linalg.norm(d),a),'axis_error_profiles':axes}
    raise ValueError('未知张量工具')


def _quality_tools(op,args):
    a=_dense(_load(args));
    if op=='math_array_missing_handle':
        if a.dtype.kind not in 'fc': raise ValueError('缺失值处理需要浮点或复数数组')
        mask=~np.isfinite(a); method=args.get('method','constant'); out=a.copy()
        if method=='drop':
            if a.ndim>2: raise ValueError('剔除缺失值仅支持向量或按行处理的矩阵')
            out=a[~np.any(mask,axis=tuple(range(1,a.ndim))) if a.ndim>1 else ~mask]
        elif method=='constant': out[mask]=args.get('value',0)
        elif method in {'mean','median'}:
            if np.iscomplexobj(a): raise ValueError('复数数组不支持均值或中位数填补')
            statistic=np.nanmean(np.where(np.isfinite(a),a,np.nan),axis=0) if method=='mean' else np.nanmedian(np.where(np.isfinite(a),a,np.nan),axis=0)
            out=np.where(mask,statistic,out)
            if not np.all(np.isfinite(out)): raise ValueError('存在整列缺失，无法使用所选统计量填补')
        else: raise ValueError('缺失值处理方法无效')
        return {'method':method,'missing_count':int(mask.sum()),'output_shape':list(out.shape),**_save(args,out,'cleaned',{'missing_mask':mask})}
    if op=='math_array_outlier_detect':
        if np.iscomplexobj(a): raise ValueError('异常值检测不支持复数数组')
        _finite(a); method=args.get('method','mad'); threshold=float(args.get('threshold',3.5)); flat=a.reshape(-1)
        if method=='mad':
            center=np.median(flat); scale=1.4826*np.median(np.abs(flat-center)); score=np.abs(flat-center)/scale if scale else np.zeros_like(flat)
        elif method=='iqr':
            q1,q3=np.quantile(flat,[.25,.75]); center=(q1+q3)/2; scale=q3-q1; score=np.maximum(q1-flat,flat-q3)/scale if scale else np.zeros_like(flat); threshold=float(args.get('threshold',1.5))
        else: raise ValueError('异常检测方法无效')
        mask=(score>threshold).reshape(a.shape); locations=np.argwhere(mask)
        return {'method':method,'threshold':threshold,'outlier_count':int(mask.sum()),'fraction':float(mask.mean()),
                'location_sample':[v.tolist() for v in locations[:200]],**(_save(args,mask.astype(np.uint8),'outlier_mask') if args.get('output_path') else {})}
    if op=='math_result_validate':
        b=_dense(_load(args,'other_')); kind=args.get('validation','reconstruction'); tolerance=float(args.get('tolerance',1e-8))
        if kind=='reconstruction':
            if a.shape!=b.shape: raise ValueError('重构验证要求形状一致')
            absolute=float(np.linalg.norm(a-b)); relative=_relative(absolute,a); metrics={'absolute_residual':absolute,'relative_residual':relative}; passed=relative<=tolerance
        elif kind=='orthogonality':
            matrix=_matrix(a); ident=np.eye(matrix.shape[1]); relative=float(np.linalg.norm(matrix.T.conj()@matrix-ident)); metrics={'orthogonality_error':relative}; passed=relative<=tolerance
        elif kind=='linear_system':
            matrix=_matrix(a); solution=np.asarray(b); rhs=_dense(_load(args,'rhs_')); error=np.linalg.norm(matrix@solution-rhs); relative=_relative(error,rhs); metrics={'absolute_residual':float(error),'relative_residual':relative}; passed=relative<=tolerance
        else: raise ValueError('验证类型无效')
        return {'validation':kind,'tolerance':tolerance,'passed':bool(passed),**metrics}
    if op=='math_report_generate':
        summary=_ops().numeric_summary(a); report={'source':Path(args['input_path']).name,'variable':args.get('variable'),'shape':list(a.shape),'dtype':str(a.dtype),'statistics':summary,'notes':args.get('notes','')}
        path=Path(args['output_path']); path.parent.mkdir(parents=True,exist_ok=True)
        if path.suffix.lower()=='.json': path.write_text(json.dumps(_ops().serializable(report),ensure_ascii=False,indent=2),encoding='utf-8')
        elif path.suffix.lower() in {'.md','.markdown'}:
            path.write_text(f"# 数学分析报告\n\n- 输入：{report['source']}\n- 形状：{report['shape']}\n- 类型：{report['dtype']}\n\n## 数值摘要\n\n```json\n{json.dumps(_ops().serializable(summary),ensure_ascii=False,indent=2)}\n```\n\n## 说明\n\n{report['notes'] or '无'}\n",encoding='utf-8')
        else: raise ValueError('报告仅支持 JSON 或 Markdown')
        return {'report':report,'output_path':path.name,'output_size_bytes':path.stat().st_size}
    raise ValueError('未知质量工具')


def execute(op,args):
    if op.startswith('math_sparse_'): return _sparse_tools(op,args)
    if op in {'math_matrix_properties','math_matrix_subspaces','math_matrix_pseudoinverse','math_matrix_equilibrate','math_matrix_low_rank','math_matrix_block','math_matrix_compare','math_linear_system_sensitivity'}: return _matrix_tools(op,args)
    if op in {'math_fit_robust_least_squares','math_regularized_inverse','math_nonlinear_curve_fit','math_polynomial_fit'}: return _fit_tools(op,args)
    if op in {'math_scalar_root','math_nonlinear_system','math_constrained_optimize','math_linear_program'}: return _optimization_tools(op,args)
    if op in {'math_interpolate','math_differentiate','math_integrate','math_ode_ivp','math_ode_bvp'}: return _calculus_tools(op,args)
    if op in {'math_tensor_mode_product','math_tensor_multilinear_rank','math_tensor_tucker','math_tensor_cp','math_tensor_compare'}: return _tensor_tools(op,args)
    return _quality_tools(op,args)
