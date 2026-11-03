"""depth=7 vs 默认 8：完整链路下游影响（M3 → M4 → X3）。

判据不是"岛数好看"，而是：岛数 / 覆盖率 / 可见性 / X3 异常 一起变好，
且点云贴合度没有实质退化。
"""
import sys
sys.path.insert(0, r"E:\h3d_lablab\src")
import json
import numpy as np
import open3d as o3d

from h3d.core import PipelineConfig
from h3d.runner import _stage_mesh, _stage_texture, _stage_anomaly_qc
from h3d.runner.task import Task
from h3d.geo_align import _robust_diag

ROOT = "D:/h3d_relic_test"
DEPTH = int(sys.argv[1]) if len(sys.argv) > 1 else 7


def main():
    cfg = PipelineConfig()
    cfg.mesh.poisson_depth = DEPTH
    cfg.texture.paint_mode = "photo"
    cfg.photo_texture.texture_size = 2048
    cfg.runner.manifest = f"{ROOT}/90_meta/_depth{DEPTH}.json"
    cfg.runner.x3_enabled = True

    t = Task(item_id="relic_001",
             raw_paths=[f"{ROOT}/00_raw/relic_001/test111.ply"], root=ROOT)
    t.finish_stage("M1", [f"{ROOT}/10_clean/relic_001/relic_001_s01.ply"], {})

    m3 = _stage_mesh(t, cfg)
    print(f"\nM3 depth={DEPTH}: {m3.status.value} "
          f"面数={m3.metrics.get('n_triangles')} 顶点={m3.metrics.get('n_vertices')}")
    t.finish_stage("M3", m3.outputs, m3.metrics)

    m4 = _stage_texture(t, cfg)
    t.finish_stage("M4", m4.outputs, m4.metrics)
    m = m4.metrics
    al = m.get("icp_align") or {}
    print(f"M4: 择优={al.get('align_chosen')} 候选={al.get('align_candidates')}")
    for k in ("coverage_ratio", "island_fill_ratio", "normal_visible_fraction",
              "in_frame_fraction", "multiview_color_std"):
        print(f"    {k:24s} {m.get(k)}")

    x3 = _stage_anomaly_qc(t, cfg)
    print(f"X3: {x3.status.value} 异常={x3.metrics.get('anomaly_ratio')} "
          f"处数={x3.metrics.get('anomaly_regions')} "
          f"岛数={x3.metrics.get('island_count')} "
          f"sigma={x3.metrics.get('anomaly_sigma_median')}")


if __name__ == "__main__":
    main()
