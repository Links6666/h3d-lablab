"""tests.test_novel_calib : novel 阈值在线标定（P3）单元测试。"""
from __future__ import annotations

import numpy as np

from h3d.novel_calib import online_novel_threshold


def _mk_clouds(overlap=0.5, n_base=2000, n_dense=1000):
    rng = np.random.default_rng(0)
    base = rng.normal(size=(n_base, 3))
    n_old = int(n_dense * overlap)
    n_new = n_dense - n_old
    dense = np.vstack([rng.normal(size=(n_old, 3)),
                       rng.normal(size=(n_new, 3)) + 5.0])  # 新区域远离
    return base, dense


class TestOnlineNovelThreshold:
    def test_high_overlap_low_novel(self):
        """重叠 90% → novel 低（应 < 0.3）。"""
        base, dense = _mk_clouds(overlap=0.9)
        thr, novel = online_novel_threshold(base, dense)
        assert 0.0 <= novel < 0.35
        assert thr > 0

    def test_low_overlap_high_novel(self):
        """重叠 10% → novel 高（clamp 至上限 0.40 仍显著高于低重叠场景）。"""
        base, dense = _mk_clouds(overlap=0.1)
        thr, novel = online_novel_threshold(base, dense)
        assert novel >= 0.35  # clamp 上限 0.40 附近（显著高）
        base2, dense2 = _mk_clouds(overlap=0.9)
        _t2, novel_low = online_novel_threshold(base2, dense2)
        assert novel > novel_low + 0.1  # 低重叠显著高于高重叠

    def test_empty_inputs(self):
        thr, novel = online_novel_threshold(np.zeros((0, 3)),
                                            np.zeros((0, 3)))
        assert 0.0 <= novel <= 0.40


class TestNovelWriteBack:
    def test_update_smoothing(self):
        from h3d.novel_calib import update_novel_threshold
        assert update_novel_threshold(None, 0.4) == 0.4  # 首次直接采用
        v = update_novel_threshold(0.4, 0.2, lr=0.3)
        assert 0.3 < v < 0.36  # 0.4*0.7+0.2*0.3=0.34

    def test_save_and_load(self, tmp_path):
        from h3d.novel_calib import save_novel_threshold
        p1 = save_novel_threshold(str(tmp_path), "r1", 0.35, 0.08)
        import os, json
        assert os.path.exists(p1)
        d = json.load(open(p1, encoding="utf-8"))
        assert d["novel_ratio"] == 0.35
        # 第二次回写 → 平滑
        save_novel_threshold(str(tmp_path), "r1", 0.15, 0.05)
        d2 = json.load(open(p1, encoding="utf-8"))
        assert 0.25 < d2["novel_ratio"] < 0.35  # 0.35*0.7+0.15*0.3=0.29
