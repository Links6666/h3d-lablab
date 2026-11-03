"""跑一次真正的整批（走 runner 全链路，含 M6 交付与报表），用于交付物核验。

不是逐阶段手动调——那会绕过编排、manifest、报表这些"只在整批里才跑"的部分。
"""
import sys
sys.path.insert(0, r"E:\h3d_lablab\src")
import os

from h3d.core import PipelineConfig
from h3d.runner import run_batch

ROOT = r"D:/h3d_relic_test"


def main():
    cfg = PipelineConfig()
    cfg.project.name = "relic_delivery"
    cfg.project.raw_dir = os.path.join(ROOT, "00_raw")
    cfg.project.formats = ["ply", "obj", "glb"]
    # 单件、无 S1（用既有 capture_meta）、照片纹理
    cfg.sfm_solve.enabled = False
    cfg.texture.paint_mode = "photo"
    cfg.photo_texture.texture_size = 2048
    cfg.runner.workers = 1
    cfg.runner.manifest = os.path.join(ROOT, "90_meta", "delivery_manifest.json")
    cfg.report.enabled = True
    # 仅为**验证交付链路**而放宽质量门：真机这件数据本来达不到 5mm/80%，
    # M5 一 fail M6 就不执行（五态契约），交付目录永远为空。这里不放宽就验不到
    # 交付机制本身。放宽只作用于本次核验，不改产品默认值。
    cfg.quality.coverage_threshold = 0.30
    cfg.quality.rmse_threshold_mm = 500.0

    print(f"raw_dir={cfg.project.raw_dir}")
    print(f"manifest={cfg.runner.manifest}")
    print(f"stages={list(__import__('h3d.runner', fromlist=['STAGE_REGISTRY']).STAGE_REGISTRY)}")
    res = run_batch(cfg)
    print("\n=== 批次结果 ===")
    print("总任务", getattr(res, "total", None), "成功", getattr(res, "done", None),
          "失败", getattr(res, "failed", None), "跳过", getattr(res, "skipped", None))
    return res


if __name__ == "__main__":
    main()
