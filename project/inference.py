"""零中心配对循环区块检验；四个相位必须共同抽样。"""
from __future__ import annotations

import numpy as np
import pandas as pd


def block_indices(n: int, draws: int, block: int, seed: int) -> np.ndarray:
    if n < 2 or draws < 1 or not 1 <= block <= n:
        raise ValueError('区块抽样参数无效')
    rng = np.random.default_rng(seed)
    starts = rng.integers(0, n, size=(draws, int(np.ceil(n / block))))
    return ((starts[..., None] + np.arange(block)) % n).reshape(draws, -1)[:, :n]


def aligned_returns(a: pd.DataFrame, b: pd.DataFrame):
    keys = ['cycle', 'phase'] if 'phase' in a and 'phase' in b else ['cycle']
    if a.duplicated(keys).any() or b.duplicated(keys).any():
        raise ValueError('重复的配对收益键')
    aa, bb = a.set_index(keys).sort_index(), b.set_index(keys).sort_index()
    if not aa.index.equals(bb.index):
        raise ValueError('配对收益日期/相位不一致')
    if keys == ['cycle', 'phase']:
        x, y = aa.net.unstack('phase').to_numpy(), bb.net.unstack('phase').to_numpy()
    else:
        x, y = aa.net.to_numpy()[:, None], bb.net.to_numpy()[:, None]
    if not np.isfinite(x).all() or not np.isfinite(y).all():
        raise ValueError('收益中存在缺失值')
    return x, y


def paired_inference(a, b, draws=4000, block=4, seed=20261111):
    x, y = aligned_returns(a, b)
    delta = (x - y).mean(axis=1)
    indices = block_indices(len(delta), draws, block, seed)
    observed = float(delta.mean())
    boot = delta[indices].mean(axis=1)
    null = (delta - observed)[indices].mean(axis=1)
    terminal = (np.prod(1 + x[indices], axis=1) - np.prod(1 + y[indices], axis=1)).mean(axis=1)
    return {
        'method': 'null_centered_paired_circular_block_bootstrap',
        'observed_mean_cycle_delta': observed,
        'bootstrap_p_two_sided': float((1 + np.sum(np.abs(null) >= abs(observed) - 1e-15)) / (draws + 1)),
        'p_positive': float((1 + np.sum(null >= observed - 1e-15)) / (draws + 1)),
        'mean_cycle_delta_ci95': np.quantile(boot, [.025, .975]).tolist(),
        'terminal_delta': float((np.prod(1 + x, axis=0) - np.prod(1 + y, axis=0)).mean()),
        'terminal_delta_ci95': np.quantile(terminal, [.025, .975]).tolist(),
        'draws': draws, 'block_cycles': block, 'cycles': len(delta), 'phases': x.shape[1],
        'cycle_deltas': delta.tolist(),
        'limitations': '小样本、相位重叠；依赖弱平稳假设；终值区间为重采样描述区间，不是预测区间。',
    }


def bh_adjust(pvalues):
    p = np.asarray(pvalues, dtype=float)
    order = np.argsort(p)
    ranked = np.minimum.accumulate((p[order] * len(p) / np.arange(1, len(p) + 1))[::-1])[::-1]
    out = np.empty_like(p)
    out[order] = np.minimum(1, ranked)
    return out


def family_test(delta_matrix, draws=4000, block=4, seed=20261111):
    d = np.asarray(delta_matrix, dtype=float)
    indices = block_indices(len(d), draws, block, seed)
    observed = d.mean(axis=0)
    null = (d - observed)[indices].mean(axis=1)
    raw = (1 + (np.abs(null) >= np.abs(observed)[None, :] - 1e-15).sum(axis=0)) / (draws + 1)
    max_null = np.maximum(0, null.max(axis=1))
    adjusted = (1 + (max_null[:, None] >= observed[None, :] - 1e-15).sum(axis=0)) / (draws + 1)
    global_p = (1 + (max_null >= max(0, float(observed.max())) - 1e-15).sum()) / (draws + 1)
    return {'p_two_sided': raw.tolist(), 'q_bh': bh_adjust(raw).tolist(),
            'p_max_positive': adjusted.tolist(), 'global_p_positive': float(global_p)}
