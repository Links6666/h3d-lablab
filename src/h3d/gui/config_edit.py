"""h3d.gui.config_edit : 配置编辑（表单 <-> config.yaml）。

把 PipelineConfig 的关键参数映射为可编辑控件值；生成/读取 YAML。
向导第三步入参（极简模式）+ 主界面高级折叠（可扩展）。
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field

import yaml

# 默认配置模板（场景①：激光几何 + 照片纹理，真实数据最优参数）
SCENE1_CONFIG = {
    "project": {"name": "h3d_project", "raw_dir": "", "formats": ["ply"]},
    # 尺度自适应（跨尺度文物通用化）：以包围盒对角线为基准线性缩放
    # 体素 / FPFH 半径 / RANSAC 与 ICP 距离等有量纲参数。
    # reference_diag 是「下面这套参数被调出来时所对应的物体对角线」，
    # 3.7 ≈ 1.8 m 高文物的外接盒对角线。改它可迁移到其它尺度体系。
    "scale": {"enabled": True, "reference_diag": 3.7},
    "preprocess": {"voxel_down_sample": 0.02, "remove_outlier_std": 2.0,
                   "normal_estimate_k": 20},
    "register": {"fpfh_radius": 0.2, "ransac_distance": 0.5,
                 "icp_distance": 0.2, "max_iter": 300},
    "mesh": {"poisson_depth": 8, "density_ratio": 0.02},
    "texture": {"paint_mode": "photo", "export_formats": ["obj", "glb"],
                "lod_levels": [1.0]},
    "photo_texture": {"enabled": False, "texture_size": 2048, "top_k": 5,
                      "samples_per_tri": 4, "icp_align": True,
                      "poisson_blend": True,
                      "export_formats": ["obj", "glb"]},
    "sfm_solve": {"enabled": True, "max_image_size": 2400, "dense": False,
                  "dense_gate": True, "auto_calibrate": True,
                  "colmap_path": "E:/colmap/colmap-x64-windows-cuda/bin/colmap.exe",
                  "matcher": "auto", "max_features": 8192,
                  "peak_threshold": 0.004},
    "quality": {"coverage_threshold": 0.30, "samples": 4000},
    "publish": {"formats": ["ply", "obj", "glb"]},
    "runner": {"workers": 1, "x1_enabled": False, "x2_enabled": False},
    "report": {"enabled": True, "formats": ["csv", "png", "html"],
               "output_dir": "report"},
}

# 场景②：纯照片（dense 开启、无激光）
SCENE2_CONFIG = dict(SCENE1_CONFIG)
SCENE2_CONFIG["sfm_solve"] = dict(SCENE1_CONFIG["sfm_solve"])
SCENE2_CONFIG["sfm_solve"]["dense"] = True
SCENE2_CONFIG["texture"] = dict(SCENE1_CONFIG["texture"])


@dataclass
class ProjectConfig:
    """GUI 项目配置（映射到 config.yaml）。"""

    name: str = "h3d_project"
    scene: str = "scene1"              # scene1: 激光+照片 / scene2: 纯照片
    root_dir: str = ""                 # 项目根（00_raw/00_photo 的父目录）
    item_id: str = "relic_001"
    laser_ply: str = ""                # 场景①激光点云文件
    photo_dir: str = ""                # 照片文件夹
    ctrl_json: str = ""                # 控制点（可选）
    texture_size: int = 2048
    export_formats: list = field(default_factory=lambda: ["obj", "glb"])
    voxel: float = 0.02
    poisson_depth: int = 8
    dense: bool = False                # 场景②默认 True
    dense_gate: bool = True
    quality_enabled: bool = True
    x1_enabled: bool = False
    x2_enabled: bool = False
    cuda_auto: bool = True
    # 尺度自适应（2026-10-09）：按几何尺度自动缩放距离类参数，
    # 使同一套配置覆盖大中型文物与小型器物。
    autoscale: bool = True
    ref_diag: float = 3.7              # 参数基准尺度（米）
    # —— 下列字段此前**只在界面上存在、从不写进 config.yaml**（假控件）——
    # 2026-10-25：主界面「接缝平滑」「几何质检」与高级面板的 top-k / 每面采样
    # / 泊松融合 / 密度过滤全部没有回写通道，用户改完再跑，输出一模一样。
    top_k: int = 5
    samples_per_tri: int = 4
    poisson_blend: bool = True
    icp_align: bool = True
    density_ratio: float = 0.02
    #: 打开历史项目时读到的**原始 config.yaml 内容**。
    #:
    #: 实机缺陷（2026-10-25）：``to_yaml`` 原先无条件用模板重写整份配置，
    #: 而「打开历史项目」只传了 root/name/scene（配置对象是全新默认值）。
    #: 于是用户第二天打开昨天调好的项目、点一次「一键生成」，config.yaml
    #: 就被默认模板覆盖：``sfm_solve.dense`` 从 true 变 false（直接跳过稠密
    #: 重建）、``max_image_size``/``top_k``/``poisson_threads`` 等全部回到
    #: 默认值。现在把原文件留作底稿，只覆盖 GUI 真正管理的字段。
    base: dict | None = None

    # ---------------------------------------------------------- 读入
    @classmethod
    def from_root(cls, root_dir: str, name: str = "",
                  scene: str = "scene1") -> "ProjectConfig":
        """按项目根读取既有 ``config.yaml``（缺失/损坏时回落默认值）。

        只做「控件能表达的那部分」回填，其余字段原样保留在 :attr:`base` 里，
        由 :meth:`to_yaml` 原封不动写回。
        """
        self = cls()
        self.root_dir = root_dir or ""
        self.name = name or os.path.basename(os.path.normpath(root_dir or "")) \
            or "h3d_project"
        self.scene = scene or "scene1"
        path = os.path.join(root_dir or "", "config.yaml")
        data: dict = {}
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8-sig") as fh:
                    data = yaml.safe_load(fh) or {}
                if not isinstance(data, dict):
                    data = {}
            except (OSError, yaml.YAMLError, UnicodeDecodeError):
                data = {}
        if not data:
            return self
        self.base = data
        proj = data.get("project") or {}
        self.name = proj.get("name") or self.name
        self.scene = ("scene2" if (data.get("sfm_solve") or {}).get(
            "enabled") and not os.path.isdir(
                os.path.join(root_dir, "00_raw")) else "scene1") \
            if scene in ("", None) else scene
        self.dense = bool((data.get("sfm_solve") or {}).get("dense", False))
        self.dense_gate = bool((data.get("sfm_solve") or {}).get(
            "dense_gate", True))
        self.voxel = float((data.get("preprocess") or {}).get(
            "voxel_down_sample", self.voxel) or self.voxel)
        self.poisson_depth = int((data.get("mesh") or {}).get(
            "poisson_depth", self.poisson_depth) or self.poisson_depth)
        self.texture_size = int((data.get("photo_texture") or {}).get(
            "texture_size", self.texture_size) or self.texture_size)
        # 纹理导出格式取自 texture.export_formats —— **不是** publish.formats。
        # publish 是交付阶段的全格式清单（含 ply），拿它回填会让「打开历史
        # 项目再来一次」把纹理导出从 [obj,glb] 扩成 [ply,obj,glb]。
        tex = (data.get("texture") or {}).get("export_formats") \
            or (data.get("photo_texture") or {}).get("export_formats")
        if isinstance(tex, list) and tex:
            self.export_formats = [str(f) for f in tex]
        sc = data.get("scale") or {}
        self.autoscale = bool(sc.get("enabled", True))
        self.ref_diag = float(sc.get("reference_diag", 3.7) or 3.7)
        pt = data.get("photo_texture") or {}
        self.top_k = int(pt.get("top_k", self.top_k) or self.top_k)
        self.samples_per_tri = int(pt.get("samples_per_tri",
                                          self.samples_per_tri)
                                   or self.samples_per_tri)
        self.poisson_blend = bool(pt.get("poisson_blend", True))
        self.icp_align = bool(pt.get("icp_align", True))
        self.density_ratio = float((data.get("mesh") or {}).get(
            "density_ratio", self.density_ratio) or self.density_ratio)
        run = data.get("runner") or {}
        self.x1_enabled = bool(run.get("x1_enabled", False))
        self.x2_enabled = bool(run.get("x2_enabled", False))
        self.quality_enabled = bool((data.get("quality") or {}).get(
            "coverage_threshold") is not None)
        # 文物编号从 00_raw 反查（与任务发现同口径），找不到就沿用默认
        try:
            from h3d.gui.items import discover_items
            items = discover_items(root_dir)
            if items:
                self.item_id = items[0]
        except Exception:  # noqa: BLE001 - 反查失败不影响打开项目
            pass
        # 照片目录：00_photo/<item> 优先，其次项目根
        cand = os.path.join(root_dir, "00_photo", self.item_id)
        if os.path.isdir(cand):
            self.photo_dir = cand
        return self

    def validate(self) -> list:
        """校验：返回错误列表（空 = 通过）。"""
        errs = []
        if not self.root_dir:
            errs.append("请选择项目根目录")
        if self.scene == "scene1" and not self.laser_ply:
            errs.append("场景①需要激光点云文件（.ply/.pcd）")
        if not self.photo_dir:
            errs.append("请拖入照片文件夹"
                        "（内含 .jpg/.jpeg/.jpe/.jfif/.png/.tif/.tiff 照片）")
        elif not os.path.isdir(self.photo_dir):
            errs.append(
                f"照片位置不是文件夹：{self.photo_dir}\n请拖入含全部照片的"
                "文件夹，或全选照片直接拖入（将自动识别所在目录）")
        else:
            # 统一口径（2026-10-17）：图像识别集中到 h3d.core.media，
            # 覆盖 .jpg/.jpeg/.jpe/.jfif/.png/.tif/.tiff，大小写不敏感。
            from h3d.core.media import count_images
            n_photo = count_images(self.photo_dir)
            if n_photo == 0:
                errs.append(
                    f"照片文件夹内未找到照片（{self.photo_dir}）\n支持格式："
                    ".jpg/.jpeg/.jpe/.jfif/.png/.tif/.tiff（大小写均可），"
                    "请确认照片在所选文件夹内")
            elif n_photo < 3:
                errs.append(f"照片过少（{n_photo} 张，建议 ≥30 张环绕）")
        if self.ctrl_json and not os.path.exists(self.ctrl_json):
            errs.append("控制点文件不存在")
        return errs

    def build_layout(self) -> dict:
        """在项目根目录建立 00_raw/00_photo/00_ctrl 目录并返回路径。"""
        root = self.root_dir
        raw = os.path.join(root, "00_raw", self.item_id)
        photo = os.path.join(root, "00_photo", self.item_id)
        ctrl = os.path.join(root, "00_ctrl", self.item_id)
        os.makedirs(raw, exist_ok=True)
        os.makedirs(photo, exist_ok=True)
        os.makedirs(ctrl, exist_ok=True)
        return {"raw": raw, "photo": photo, "ctrl": ctrl}

    def to_yaml(self, config_path: str) -> str:
        """按场景生成 config.yaml 并写入。

        若本对象由 :meth:`from_root` 读入（:attr:`base` 非空），则以**原文件**
        为底稿，只覆盖 GUI 真正管理的字段 —— 否则用户的 ``max_image_size``
        /``top_k``/``poisson_threads``/``workers`` 等非控件参数会被默认值抹掉。
        """
        # 深拷贝：SCENE*_CONFIG 是模块级模板，浅拷贝会让下面的嵌套赋值
        # （cfg["scale"]["enabled"] 等）**就地改写全局模板**，导致同一进程中
        # 第二次生成配置时继承上一次的用户选择（2026-10-09 修复）。
        if self.base:
            cfg = copy.deepcopy(self.base)
        else:
            cfg = copy.deepcopy(
                SCENE2_CONFIG if self.scene == "scene2" else SCENE1_CONFIG)
        # 底稿可能缺段（手写 yaml），统一补齐，避免 KeyError
        for sec in ("project", "scale", "preprocess", "register", "mesh",
                    "texture", "photo_texture", "sfm_solve", "quality",
                    "publish", "runner", "report"):
            cfg.setdefault(sec, {})
        cfg["project"]["name"] = self.name
        cfg["project"]["raw_dir"] = os.path.join(self.root_dir, "00_raw")
        cfg["sfm_solve"]["dense"] = self.dense or self.scene == "scene2"
        cfg["sfm_solve"]["dense_gate"] = self.dense_gate
        cfg["photo_texture"]["texture_size"] = self.texture_size
        cfg["photo_texture"]["export_formats"] = self.export_formats
        cfg["texture"]["export_formats"] = self.export_formats
        cfg["preprocess"]["voxel_down_sample"] = self.voxel
        cfg["mesh"]["poisson_depth"] = self.poisson_depth
        cfg["mesh"]["density_ratio"] = float(self.density_ratio)
        cfg["scale"]["enabled"] = bool(self.autoscale)
        cfg["scale"]["reference_diag"] = float(self.ref_diag)
        # 覆盖率阈值是**专业参数**（WW/T 0115 分级），界面没有控件；
        # 原实现无条件写 0.30，会把用户调过 0.8 的项目改回宽松档。
        cfg["quality"].setdefault("coverage_threshold", 0.30)
        cfg["quality"]["enabled"] = bool(self.quality_enabled)
        # 纹理采样三项：此前只在界面上存在，从不落盘
        cfg["photo_texture"]["top_k"] = int(self.top_k)
        cfg["photo_texture"]["samples_per_tri"] = int(self.samples_per_tri)
        cfg["photo_texture"]["poisson_blend"] = bool(self.poisson_blend)
        cfg["photo_texture"]["icp_align"] = bool(self.icp_align)
        cfg["runner"]["x1_enabled"] = self.x1_enabled
        cfg["runner"]["x2_enabled"] = self.x2_enabled
        # manifest 与 CLI 默认一致（output/run_manifest.json）：
        # 历史项目 resume 命中完整交付状态
        cfg["runner"]["manifest"] = os.path.join(
            self.root_dir, "output", "run_manifest.json")
        cfg["report"]["output_dir"] = os.path.join(self.root_dir, "report")
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as fh:
            yaml.safe_dump(cfg, fh, allow_unicode=True, sort_keys=False)
        return config_path


__all__ = ["ProjectConfig", "SCENE1_CONFIG", "SCENE2_CONFIG"]
