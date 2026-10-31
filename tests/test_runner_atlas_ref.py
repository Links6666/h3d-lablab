"""tests.test_runner_atlas_ref : runner 的图集登记接线（B1 第七片）。

真机 2026-10-31 的第二个缺陷：``_register_archive`` 里写着

    uv_atlas_ref="atlas-v1"          # 手写常量

不管项目有没有图集、布局指纹是什么，资产一律声称自己挂在 ``atlas-v1`` 上。
这是 r16「manifest_metrics 手写白名单」的同构问题——引用没有实体，报表却
一切正常。这一组测试锁住「引用必须来自登记结果」：

* 基准期 + 有 uv.npz  → 登记图集，资产的 ref 等于登记版本；
* 基准期 + 没有 uv.npz → ref 为空（**不许编造**）；
* 复测期 → 不登记图集（不得改写基准图集），ref 也为空（继承由 inherit 自己写）。
"""

from __future__ import annotations

import os

import numpy as np
import pytest

from h3d.archive import Archive, Artifact, Epoch
from h3d.archive.atlas import ATLAS_DIR, AtlasRegistry, save_uv_layout
from h3d.core import RunManifest
from h3d.runner.batch_processor import BatchProcessor
from h3d.runner.task import StageResult, Task, TaskStatus


def _project(tmp_path, *, baseline: bool = True, epoch_id: str = "E1"):
    """建项目骨架：``<tmp>/阶段成果/{50_tex,90_meta}`` + 已建档。

    返回 ``(阶段根, Archive)``。``task.root`` 就是阶段根（r18 起下移）。
    """
    root = tmp_path / "proj"
    stage = root / "阶段成果"
    (stage / "50_tex" / "relic_001").mkdir(parents=True)
    (stage / "90_meta").mkdir(parents=True)
    arch = Archive.init(str(root), Artifact(artifact_id="relic_001",
                                           title="彩绘兵马俑"))
    arch.add_epoch(Epoch(epoch_id=epoch_id, artifact_id="relic_001", route="A",
                         captured_on="2026-10-26", is_baseline=baseline))
    return str(stage), Archive.open(str(root))


def _done_task(stage_root: str, *, with_uv: bool = True) -> Task:
    tex_dir = os.path.join(stage_root, "50_tex", "relic_001")
    mesh = os.path.join(tex_dir, "relic_001_L1.0.ply")
    tex = os.path.join(tex_dir, "relic_001_L1.0_diffuse.png")
    for p in (mesh, tex):
        with open(p, "wb") as fh:
            fh.write(b"stub")
    if with_uv:
        uv = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]], dtype=np.float64)
        faces = np.array([[0, 1, 2]], dtype=np.int64)
        save_uv_layout(os.path.join(tex_dir, "relic_001_uv.npz"), uv, faces)
    cam = os.path.join(stage_root, "90_meta", "relic_001_capture_meta.json")
    with open(cam, "w", encoding="utf-8") as fh:
        fh.write("{}")

    task = Task(item_id="relic_001", root=stage_root)
    task.status = TaskStatus.DONE
    task.results["M3"] = StageResult(stage="M3", status=TaskStatus.DONE,
                                     outputs=[mesh])
    task.results["M4"] = StageResult(
        stage="M4", status=TaskStatus.DONE, outputs=[tex],
        metrics={"atlas_size_px": 2048})
    return task


class TestBaselineRegistersAtlas:
    def test_baseline_asset_points_at_registered_version(self, tmp_path):
        """基准期：资产 ref 必须等于刚登记的图集版本，且记录真的落盘。"""
        stage, arch = _project(tmp_path)
        task = _done_task(stage)

        BatchProcessor._register_archive(task, RunManifest())

        registry = AtlasRegistry(arch)
        versions = [r.atlas_version for r in registry.versions()]
        assert versions == ["atlas-v1"], f"图集没登记：{versions}"
        asset = Archive.open(arch.root).latest("E1")
        assert asset is not None
        assert asset.uv_atlas_ref == "atlas-v1"
        assert asset.uv_atlas_ref != "", "资产声称没有图集，但 uv.npz 明明落了盘"
        rec = registry.get("atlas-v1")
        assert rec.size_px == 2048, "纹理尺寸应取自 M4 metrics"

    def test_no_uv_npz_leaves_ref_empty(self, tmp_path):
        """没有 uv.npz → 不登记图集，ref 留空（绝不编造 ``atlas-v1``）。"""
        stage, arch = _project(tmp_path)
        task = _done_task(stage, with_uv=False)

        BatchProcessor._register_archive(task, RunManifest())

        assert not os.path.isdir(os.path.join(arch.root, "archive", ATLAS_DIR))
        asset = Archive.open(arch.root).latest("E1")
        assert asset is not None, "产物登记不应因为缺 UV 而整体放弃"
        assert asset.uv_atlas_ref == ""


class TestMultiItemProject:
    def test_second_item_gets_own_version_and_first_stays(self, tmp_path):
        """多件项目：第二件有自己的 UV 布局 → 新版本，且第一件的引用不动。

        runner 一律把产物登记到基准期，件号来自 ``task.item_id``。若图集记录
        不按件区分，第二件会被判成「同一件的布局变了」→ 第一件的 uv_atlas_ref
        指向的图集定义被悄悄换掉。
        """
        stage, arch = _project(tmp_path)
        first = _done_task(stage)

        second = _done_task(stage)
        second.item_id = "relic_002"
        uv2 = np.array([[0.0, 0.0], [0.9, 0.0], [0.9, 0.9]], dtype=np.float64)
        save_uv_layout(os.path.join(stage, "50_tex", "relic_001",
                                    "relic_002_uv.npz"),
                       uv2, np.array([[0, 1, 2]], dtype=np.int64))

        BatchProcessor._register_archive(first, RunManifest())
        BatchProcessor._register_archive(second, RunManifest())

        records = AtlasRegistry(arch).versions()
        assert [r.atlas_version for r in records] == ["atlas-v1", "atlas-v2"]
        assert [r.item_id for r in records] == ["relic_001", "relic_002"]
        assert records[0].uv_digest != records[1].uv_digest
