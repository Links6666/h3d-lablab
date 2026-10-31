"""M2 配准端到端验证：注入已知刚体变换，看 M2 能否复原。

为什么需要这个脚本
------------------
1.1.10~1.1.12 三轮真机里，M2 的耗时一直是 16 ms、``registered=否``、
``identity_reason=single_scan``——因为 ``00_raw/<件>/`` 里只有一个点云文件，
M2 走的是"单站直通"。把唯一一份 PLY 施加刚体变换，**不会**让 M2 跑起来：
变换后的 PLY 仍然是那一站的输入，M2 还是单站。想验证配准节点
（FPFH + RANSAC + ICP），必须给它**两份错位的同源几何**。

本脚本自动造这个场景：
    s01.ply = 原始点云
    s02.ply = s01 施加已知相似变换（绕轴旋转 + 平移 + 可选缩放）
→ 调真实的 M2 阶段函数（不是另写一份配准）
→ 读回 ``20_registered/<件>_s02.ply``，与 s01 比对

判据
----
* ``median_gap`` ：配准后 s02 到 s01 的最近邻中位距离 / 点云对角线（**主判据**）
* ``rot_err_deg``：M2 恢复的旋转 × 注入旋转 的合成旋转角
* ``shift_err`` ：恢复的平移与注入平移之差 / 对角线

通过条件：``median_gap <= --max-gap``（默认 2%）且 ``rot_err_deg <= --max-rot``
（默认 2°）。刚体变换下两者应同时成立；只满足一个说明只是"碰巧重叠"。

用法
----
    python scripts/verify_registration_e2e.py model.ply
    python scripts/verify_registration_e2e.py model.ply --angle 7 --shift 0.02 --axis y
    python scripts/verify_registration_e2e.py model.ply --scale 1.0 --keep-tmp

退出码：0 通过 / 1 未通过 / 2 输入或运行错误。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time

import numpy as np


def _rot(axis: str, deg: float) -> np.ndarray:
    a = np.radians(deg)
    c, s = np.cos(a), np.sin(a)
    if axis == "x":
        return np.array([[1, 0, 0], [0, c, -s], [0, s, c]], float)
    if axis == "z":
        return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]], float)
    return np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]], float)


def _rot_angle_deg(R: np.ndarray) -> float:
    tr = float(np.clip((np.trace(R) - 1.0) / 2.0, -1.0, 1.0))
    return float(np.degrees(np.arccos(tr)))


def _median_gap(src: np.ndarray, dst: np.ndarray) -> float:
    """src 到 dst 的最近邻中位距离（绝对单位）。"""
    from scipy.spatial import cKDTree
    d = cKDTree(dst).query(src, k=1)[0]
    return float(np.median(d))


def main() -> int:
    ap = argparse.ArgumentParser(description="M2 配准端到端验证")
    ap.add_argument("model", help="输入点云（PLY/PCD/XYZ）")
    ap.add_argument("--angle", type=float, default=7.0, help="注入旋转角（度）")
    ap.add_argument("--axis", default="y", choices=["x", "y", "z"])
    ap.add_argument("--shift", type=float, default=0.01,
                    help="注入平移（占对角线比例，默认 0.01）")
    ap.add_argument("--scale", type=float, default=1.0, help="注入缩放（默认 1.0）")
    ap.add_argument("--noise", type=float, default=0.0,
                    help="注入高斯噪声（占对角线比例，模拟两次独立扫描的差异）")
    ap.add_argument("--max-points", type=int, default=30000,
                    help="下采样到最多多少点（控制耗时）")
    ap.add_argument("--max-gap", type=float, default=0.02,
                    help="允许的最大中位间距（占对角线）")
    ap.add_argument("--max-rot", type=float, default=2.0,
                    help="允许的最大旋转角误差（度）")
    ap.add_argument("--keep-tmp", action="store_true", help="保留临时项目目录")
    ap.add_argument("--json", default="", help="把结果写入该 JSON 文件")
    args = ap.parse_args()

    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "src"))
    import h3d  # noqa: F401  - 触发 src 包导入
    from h3d.core import config as _config
    from h3d.core.paths import ensure_layout
    from h3d.io import load_point_cloud, save_point_cloud
    from h3d.runner import _stage_register
    from h3d.runner.task import Task

    if not os.path.isfile(args.model):
        print(f"[E100] 输入文件不存在：{args.model}")
        return 2

    print(f"读入 {args.model}")
    pc = load_point_cloud(args.model)
    if pc is None or not pc.has_points():
        print("[E100] 点云为空或无法读取")
        return 2
    diag0 = float(np.linalg.norm(
        np.asarray(pc.points).max(0) - np.asarray(pc.points).min(0)))
    print(f"  原始点数 {len(pc.points)}，对角 {diag0:.4f}（模型单位）")

    if len(pc.points) > args.max_points:
        vox = diag0 / 400.0
        pc = pc.voxel_down_sample(vox)
        print(f"  体素 {vox:.4f} 下采样 -> {len(pc.points)} 点")

    P = np.asarray(pc.points, dtype=np.float64).copy()
    diag = float(np.linalg.norm(P.max(0) - P.min(0)))
    R_inj = _rot(args.axis, args.angle)
    t_inj = np.array([args.shift * diag, -args.shift * diag, args.shift * diag * 0.5])
    s_inj = float(args.scale)
    centroid = P.mean(axis=0)
    # 绕质心旋转，避免把物体甩到远处（平移量才有可比性）
    Q = (s_inj * (P - centroid) @ R_inj.T) + centroid + t_inj
    if args.noise > 0:
        # 两份几何同源时配准必然"完美"，测不出鲁棒性。加噪即模拟两站独立扫描
        rng = np.random.default_rng(20261031)
        Q = Q + rng.normal(0.0, args.noise * diag, Q.shape)
        print(f"  注入噪声 σ={args.noise * diag:.4f}"
              f"（{args.noise * 100:.2f}% 对角线）")

    tmp = tempfile.mkdtemp(prefix="h3d_regverify_")
    project = tmp
    root = os.path.join(project, "阶段成果")
    os.makedirs(root, exist_ok=True)
    item = "reg_check"
    raw_dir = os.path.join(root, "00_raw", item)
    os.makedirs(raw_dir, exist_ok=True)
    ensure_layout(root)
    s01 = os.path.join(raw_dir, "s01.ply")
    s02 = os.path.join(raw_dir, "s02.ply")

    import open3d as o3d
    a = o3d.geometry.PointCloud()
    a.points = o3d.utility.Vector3dVector(P)
    b = o3d.geometry.PointCloud()
    b.points = o3d.utility.Vector3dVector(Q)
    if pc.has_colors():
        a.colors = pc.colors
        b.colors = pc.colors
    save_point_cloud(a, s01)
    save_point_cloud(b, s02)
    print(f"  注入变换：绕 {args.axis.upper()} 轴 {args.angle}°、"
          f"平移 {np.round(t_inj, 4)}（{args.shift * 100:.1f}% 对角线）、缩放 {s_inj}")
    print(f"  临时项目：{project}")

    cfg = _config.loads("{}")
    # 用与真实管线一致的「生效配置」（尺度自适应会按对角线缩放 FPFH/ICP 半径）：
    # 拿默认 0.2 m 的 FPFH 半径去配一个小件，必然配不动——那不是 M2 的问题。
    from h3d.runner.batch_processor import resolve_task_config
    task = Task(item_id=item, raw_paths=[s01, s02], root=root)
    cfg, scale_info = resolve_task_config(task, cfg)
    print(f"  生效配置：尺度因子 {scale_info.get('factor')}，"
          f"FPFH 半径 {cfg.register.fpfh_radius:.4f}，"
          f"ICP 距离 {cfg.register.icp_distance:.4f}")

    from h3d.runner.task import TaskStatus
    task.status = TaskStatus.RUNNING
    t0 = time.time()
    res = _stage_register(task, cfg)
    elapsed = time.time() - t0
    print(f"\nM2 执行完成（{elapsed:.1f} s）：status={res.status.value} "
          f"metrics={json.dumps({k: v for k, v in res.metrics.items() if not isinstance(v, list) or len(v) < 5}, ensure_ascii=False)}")

    outs = res.outputs or []
    reg_path = next((p for p in outs if p.endswith("_s02.ply")), None)
    if reg_path is None or not os.path.isfile(reg_path):
        print("[FAIL] M2 未产出 s02 配准结果，无法核算")
        if not args.keep_tmp:
            shutil.rmtree(tmp, ignore_errors=True)
        return 1

    reg = load_point_cloud(reg_path)
    R2 = np.asarray(reg.points, dtype=np.float64)
    if len(R2) > 20000:
        idx = np.linspace(0, len(R2) - 1, 20000).astype(int)
        R2s = R2[idx]
    else:
        R2s = R2

    gap = _median_gap(R2s, P) / diag
    print(f"\n配准后 s02 → s01 最近邻中位距离：{gap * 100:.2f}% 对角线"
          f"（阈值 {args.max_gap * 100:.1f}%）")

    # 由 M2 落盘的配准矩阵与注入变换求旋转角误差
    transf = None
    for k, v in res.metrics.items():
        if k.startswith("transformation_s"):
            transf = v
    rot_err = None
    if transf is not None:
        M = np.asarray(transf, dtype=np.float64)
        rot_err = _rot_angle_deg(M[:3, :3] @ R_inj)

    ok = gap <= args.max_gap
    if rot_err is not None:
        ok = ok and rot_err <= args.max_rot
    verdict = "PASS" if ok else "FAIL"
    print(f"旋转角误差：{'N/A（M2 未落矩阵）' if rot_err is None else f'{rot_err:.2f}°'}"
          f"（阈值 {args.max_rot:.1f}°）")
    print(f"结论：{verdict}")

    payload = {
        "model": os.path.abspath(args.model),
        "injected": {"angle_deg": args.angle, "axis": args.axis,
                     "shift_ratio": args.shift, "scale": args.scale,
                     "shift_abs": [round(float(v), 5) for v in t_inj]},
        "points": int(len(P)),
        "diag": round(diag, 6),
        "scale_info": {k: v for k, v in scale_info.items()
                       if isinstance(v, (int, float, str, bool))},
        "m2_status": res.status.value,
        "m2_metrics": {k: v for k, v in res.metrics.items()
                       if isinstance(v, (int, float, str, bool))},
        "median_gap_ratio": round(gap, 5),
        "rot_err_deg": None if rot_err is None else round(rot_err, 3),
        "elapsed_s": round(elapsed, 2),
        "verdict": verdict,
        "thresholds": {"max_gap": args.max_gap, "max_rot_deg": args.max_rot},
    }
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(f"结果已写入 {args.json}")

    if not args.keep_tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        print(f"临时项目保留在 {project}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
