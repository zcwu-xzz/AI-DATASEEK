from __future__ import annotations
import base64, json, subprocess, sys
from pathlib import Path
import h5py
import numpy as np
import pytest
from scipy import io, sparse

ROOT=Path(__file__).resolve().parents[2]
OPS=ROOT/'tools/mathematics/operations.py'
PYTHON=ROOT/'sandbox/.venv/bin/python'

def run(name,args):
    encoded=base64.urlsafe_b64encode(json.dumps(args).encode()).decode()
    result=subprocess.run([str(PYTHON if PYTHON.exists() else sys.executable),str(OPS),name,encoded],capture_output=True,text=True,check=True)
    return json.loads(result.stdout.strip().splitlines()[-1])

@pytest.fixture
def data(tmp_path):
    values={
        'matrix':np.array([[4.,1.],[1.,3.]]),'matrix2':np.array([[3.,1.],[1.,2.]]),'rhs':np.array([1.,2.]),
        'x':np.linspace(0,1,9),'target':np.linspace(.1,.9,5),'curve':np.linspace(0,1,9)**2,
        'tensor':np.arange(24.,dtype=float).reshape(2,3,4),'tensor2':np.arange(24.,dtype=float).reshape(2,3,4)+.1,
        'missing':np.array([[1.,np.nan],[3.,100.]]),'orthogonal':np.eye(2),
        'design':np.column_stack([np.ones(9),np.linspace(0,1,9)]),'observed':2+3*np.linspace(0,1,9),
        'constraint':np.array([[1.,1.]]),'objective':np.array([1.,2.]),'ode':np.array([[-1.]])
    }
    paths={}
    for key,value in values.items(): paths[key]=tmp_path/f'{key}.npy'; np.save(paths[key],value)
    paths['sparse']=tmp_path/'sparse.npz'; sparse.save_npz(paths['sparse'],sparse.csr_matrix(values['matrix']))
    paths['sparse2']=tmp_path/'sparse2.mtx'; io.mmwrite(paths['sparse2'],sparse.csr_matrix(values['matrix2']))
    paths['mat73']=tmp_path/'v73.mat'
    with h5py.File(paths['mat73'],'w') as f: f['tensor']=values['tensor'].T
    paths['tmp']=tmp_path
    return paths

def test_foundation_fixes(data):
    assert run('math_array_inspect',{'input_path':str(data['sparse'])})['sparse'] is True
    assert run('math_array_inspect',{'input_path':str(data['mat73']),'variable':'tensor'})['shape']==[2,3,4]
    four=data['tmp']/'four.npy'; np.save(four,np.ones((2,3,4,5)))
    result=run('math_tensor_statistics',{'input_path':str(four)})
    assert result['success'] and len(result['axis_summaries'])==4

def test_all_sparse_tools(data):
    t=data['tmp']; base={'input_path':str(data['sparse'])}; rhs=str(data['rhs']); other=str(data['sparse2'])
    calls={
      'math_sparse_convert':{**base,'format':'csc','output_path':str(t/'convert.npz')},
      'math_sparse_clean':{**base,'output_path':str(t/'clean.mtx')},
      'math_sparse_submatrix':{**base,'row_range':[0,2],'column_range':[0,1],'output_path':str(t/'sub.mtx')},
      'math_sparse_arithmetic':{**base,'other_input_path':other,'operation':'add','output_path':str(t/'sum.mtx')},
      'math_sparse_direct_solve':{**base,'rhs_input_path':rhs,'output_path':str(t/'direct.npy')},
      'math_sparse_iterative_solve':{**base,'rhs_input_path':rhs,'method':'cg','output_path':str(t/'iter.npy')},
      'math_sparse_least_squares':{**base,'rhs_input_path':rhs,'method':'lsmr','output_path':str(t/'ls.npy')},
      'math_sparse_eigen':{**base,'components':1,'output_path':str(t/'eig.npz')},
      'math_sparse_svd':{**base,'components':1,'output_path':str(t/'svd.npz')},
      'math_sparse_reorder':{**base,'output_path':str(t/'reorder.npz')},
    }
    results={name:run(name,args) for name,args in calls.items()}
    assert all(v['success'] for v in results.values()),results
    assert results['math_sparse_iterative_solve']['converged']
    assert results['math_sparse_direct_solve']['relative_residual']<1e-12

def test_all_matrix_diagnostic_tools(data):
    t=data['tmp']; base={'input_path':str(data['matrix'])}; other=str(data['matrix2'])
    calls={
      'math_matrix_properties':base,
      'math_matrix_subspaces':{**base,'output_path':str(t/'spaces.npz')},
      'math_matrix_pseudoinverse':{**base,'output_path':str(t/'pinv.npy')},
      'math_matrix_equilibrate':{**base,'output_path':str(t/'balanced.npz')},
      'math_matrix_low_rank':{**base,'rank':1,'output_path':str(t/'low.npy')},
      'math_matrix_block':{**base,'operation':'block_diagonal','other_input_path':other,'output_path':str(t/'block.npy')},
      'math_matrix_compare':{**base,'other_input_path':other},
      'math_linear_system_sensitivity':{**base,'other_input_path':str(data['rhs']),'trials':5,'random_seed':7},
    }
    results={name:run(name,args) for name,args in calls.items()}
    assert all(v['success'] for v in results.values()),results
    assert results['math_matrix_properties']['positive_definite'] is True
    assert results['math_matrix_pseudoinverse']['penrose_residual_1']<1e-12

def test_all_fitting_and_optimization_tools(data):
    t=data['tmp']; design=str(data['design']); observed=str(data['observed'])
    calls={
      'math_fit_robust_least_squares':{'input_path':design,'rhs_input_path':observed,'output_path':str(t/'robust.npy')},
      'math_regularized_inverse':{'input_path':design,'rhs_input_path':observed,'regularization':1e-6,'output_path':str(t/'ridge.npy')},
      'math_nonlinear_curve_fit':{'input_path':str(data['x']),'other_input_path':str(data['curve']),'model':'gaussian','initial_parameters':[1,.5,.5,0],'output_path':str(t/'curve.npz')},
      'math_polynomial_fit':{'input_path':str(data['x']),'other_input_path':str(data['curve']),'degree':2,'output_path':str(t/'poly.npz')},
      'math_scalar_root':{'expression':'cos(x)-x','bracket':[0,1]},
      'math_nonlinear_system':{'expressions':['x[0]**2+x[1]-2','x[0]+x[1]**2-2'],'initial':[1,1]},
      'math_constrained_optimize':{'input_path':str(data['matrix']),'rhs_input_path':str(data['objective']),'constraint_input_path':str(data['constraint']),'constraint_lower':[1],'constraint_upper':[1],'initial':[.5,.5],'output_path':str(t/'opt.npy')},
      'math_linear_program':{'input_path':str(data['objective']),'inequality_input_path':str(data['constraint']),'inequality_upper':[2],'bounds':[[0,None],[0,None]],'output_path':str(t/'lp.npy')},
    }
    results={name:run(name,args) for name,args in calls.items()}
    assert all(v['success'] for v in results.values()),results
    assert results['math_scalar_root']['converged']
    assert results['math_polynomial_fit']['r_squared']>0.999999

def test_all_calculus_tools(data):
    t=data['tmp']; x=str(data['x']); y=str(data['curve']); ode=str(data['ode'])
    calls={
      'math_interpolate':{'input_path':x,'other_input_path':y,'target_input_path':str(data['target']),'method':'cubic','output_path':str(t/'interp.npz')},
      'math_differentiate':{'input_path':x,'other_input_path':y,'output_path':str(t/'derivative.npz')},
      'math_integrate':{'input_path':x,'other_input_path':y,'method':'simpson','output_path':str(t/'integral.npz')},
      'math_ode_ivp':{'input_path':x,'other_input_path':ode,'initial':[1],'output_path':str(t/'ivp.npz')},
      'math_ode_bvp':{'input_path':x,'other_input_path':ode,'initial':[0],'final':[1],'output_path':str(t/'bvp.npz')},
    }
    results={name:run(name,args) for name,args in calls.items()}
    assert all(v['success'] for v in results.values()),results
    assert results['math_ode_ivp']['converged'] and results['math_ode_bvp']['converged']

def test_all_tensor_and_quality_tools(data):
    t=data['tmp']; tensor=str(data['tensor']); other=str(data['tensor2'])
    factor=t/'factor.npy'; np.save(factor,np.eye(3)[:2])
    clean=t/'clean-input.npy'; np.save(clean,np.array([1.,2.,100.,3.]))
    calls={
      'math_tensor_mode_product':{'input_path':tensor,'other_input_path':str(factor),'mode':1,'output_path':str(t/'mode.npy')},
      'math_tensor_multilinear_rank':{'input_path':tensor,'energy':.99},
      'math_tensor_tucker':{'input_path':tensor,'ranks':[2,2,2],'random_seed':2,'output_path':str(t/'tucker.npz')},
      'math_tensor_cp':{'input_path':tensor,'rank':2,'random_seed':2,'output_path':str(t/'cp.npz')},
      'math_tensor_compare':{'input_path':tensor,'other_input_path':other},
      'math_array_missing_handle':{'input_path':str(data['missing']),'method':'median','output_path':str(t/'filled.npz')},
      'math_array_outlier_detect':{'input_path':str(clean),'method':'mad','output_path':str(t/'outlier.npy')},
      'math_result_validate':{'input_path':tensor,'other_input_path':tensor,'validation':'reconstruction'},
      'math_report_generate':{'input_path':tensor,'notes':'测试报告','output_path':str(t/'report.md')},
    }
    results={name:run(name,args) for name,args in calls.items()}
    assert all(v['success'] for v in results.values()),results
    assert results['math_result_validate']['passed']
    assert (t/'report.md').read_text().startswith('# 数学分析报告')


@pytest.mark.parametrize('expression', ["__import__('os').getcwd()", 'x.__class__', '[x for x in x]'])
def test_expression_rejects_python(expression):
    assert not run('math_scalar_root', {'expression': expression, 'bracket': [0, 1]})['success']


def test_solver_edge_cases(data):
    base = {'input_path': str(data['sparse']), 'rhs_input_path': str(data['rhs']),
            'output_path': str(data['tmp'] / 'solution.npy')}
    assert run('math_sparse_iterative_solve', {**base, 'method': 'minres'})['converged']
    assert not run('math_sparse_iterative_solve', {**base, 'max_iterations': 1, 'tolerance': 1e-15})['converged']
    assert not run('math_sparse_iterative_solve', {**base, 'max_iterations': 0})['success']
    singular = data['tmp'] / 'singular.npz'
    sparse.save_npz(singular, sparse.csr_matrix((2, 2)))
    assert not run('math_sparse_direct_solve', {**base, 'input_path': str(singular)})['success']
    result = run('math_constrained_optimize', {
        'input_path': str(data['matrix']), 'rhs_input_path': str(data['objective']),
        'bounds': [[0, None], [0, None]], 'output_path': str(data['tmp'] / 'bounded.npy')})
    assert result['converged']
    np.testing.assert_allclose(np.load(data['tmp'] / 'bounded.npy'), [0, 0], atol=1e-7)


def test_infeasible_and_unbounded_linear_program(data):
    args = {'input_path': str(data['objective']), 'output_path': str(data['tmp'] / 'lp.npy')}
    assert run('math_linear_program', {**args, 'bounds': [[None, None], [None, None]]})['status'] == 3
    assert run('math_linear_program', {**args, 'inequality_input_path': str(data['constraint']),
                                     'inequality_upper': [-1]})['status'] == 2
