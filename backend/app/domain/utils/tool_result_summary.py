"""Bounded presentation of verified metrics when answer synthesis is unavailable."""
import math
from pathlib import PurePosixPath

LABELS = {
    'rcond': '奇异值截断阈值', 'rank': '矩阵秩',
    'penrose_residual_1': '伪逆条件一相对残差', 'penrose_residual_2': '伪逆条件二相对残差',
    'condition_before': '均衡前条件数', 'condition_after': '均衡后条件数',
    'row_scale': '行缩放系数', 'column_scale': '列缩放系数',
    'output_path': '结果文件', 'output_size_bytes': '文件大小（字节）',
    'shape': '数组形状', 'dtype': '数据类型', 'iterations': '迭代次数',
    'converged': '是否收敛', 'residual_norm': '残差范数',
    'relative_residual': '相对残差', 'absolute_residual': '绝对残差',
    'minimum': '最小值', 'maximum': '最大值', 'mean': '均值',
    'standard_deviation': '标准差', 'count': '数量', 'nnz': '非零元素数',
    'r_squared': '决定系数', 'parameters': '拟合参数', 'ranks': '各模秩',
    'random_seed': '随机种子', 'singular_values': '奇异值',
    'eigenvalues': '特征值', 'constraint_violation': '约束违反量',
    'objective': '目标函数值', 'passed': '验证是否通过', 'method': '方法',
}


def _value(value, depth=0):
    if isinstance(value, bool):
        return '是' if value else '否'
    if isinstance(value, float):
        return format(value, '.6g') if math.isfinite(value) else '非有限值'
    if isinstance(value, (list, tuple)):
        if depth > 1:
            return '…'
        items = [_value(v, depth + 1) for v in value[:8]]
        if len(value) > 8:
            items.append(f'…共 {len(value)} 项')
        return '[' + ', '.join(items) + ']'
    if isinstance(value, dict):
        return '；'.join(f'{k}: {_value(v, depth + 1)}' for k, v in list(value.items())[:6])[:400]
    return str(value)[:400]


def summarize_tool_metrics(payload: dict, chinese=True) -> str:
    metrics = payload.get('summary') if isinstance(payload.get('summary'), dict) else payload
    intro = ('计算已完成，结果文件已保留。自动解读暂未完成，以下为工具直接返回的结果：'
             if chinese else 'The operation completed. Automatic interpretation is unavailable; verified tool results follow:')
    lines = [intro, '', '| 指标 | 结果 |' if chinese else '| Metric | Result |', '| --- | --- |']
    for key, value in list(metrics.items())[:30]:
        if key in {'success', 'artifacts', 'attachments'} or value is None or value == '':
            continue
        if key.endswith('_path') and isinstance(value, str):
            value = PurePosixPath(value).name
        label = LABELS.get(key, f'指标 `{key}`') if chinese else key.replace('_', ' ')
        text = _value(value).replace('|', '\\|').replace('\n', ' ').replace('\r', ' ')
        lines.append(f'| {label} | {text} |')
    return '\n'.join(lines)
