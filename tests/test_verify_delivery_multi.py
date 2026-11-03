"""交付核验的多件批行为。

真机历史上核验逻辑只在单件项目上跑过（relic_001）。多件批是唯一还没验的路径，
而它最容易静默丢东西：**清单说交付了 3 件、磁盘只有 2 件**，脚本照样报 PASS。

判据来自五态契约：M6 状态为 done 的件，交付目录里必须有对应子目录。
（M6 没执行/失败的件不要求交付——那不是缺陷，是契约。）

接缝：``h3d.delivery_verify.main(项目根)`` —— CLI 与脚本共用这一个入口。
"""
import json
import os
import sys

TESTS = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(TESTS, "..", "src"))
sys.path.insert(0, TESTS)

from _delivery_fixture import build_project, item_dir, write  # noqa: E402

import h3d.delivery_verify as vd  # noqa: E402


def _project(tmp_path, m6_done, delivered, extra_items=(),
             atlas=True, uv_baked=True, colored=True):
    return build_project(tmp_path, m6_done, delivered, extra_items,
                         atlas=atlas, uv_baked=uv_baked, colored=colored)


def _run(root):
    """跑一次核验，返回 (退出码, FAIL 文本拼接)。"""
    code = vd.main(root)
    return code, "\n".join(vd.FAILS)


class TestMultiItemCoverage:
    def test_missing_item_is_a_failure(self, tmp_path):
        """M6 说三件都交付了，磁盘只有两件 → 必须 FAIL 并点名缺谁。"""
        root = _project(tmp_path,
                        m6_done=["ping_a", "ping_b", "ping_c"],
                        delivered=["ping_a", "ping_b"])

        code, fails = _run(root)

        assert code == 1, f"缺了一件的交付包竟然通过：{fails}"
        assert "ping_c" in fails, f"FAIL 文本没点名缺哪件：{fails}"

    def test_all_items_delivered_passes(self, tmp_path):
        """三件都在 → 不该有 FAIL（其余检查由完整件构造满足）。"""
        root = _project(tmp_path,
                        m6_done=["ping_a", "ping_b", "ping_c"],
                        delivered=["ping_a", "ping_b", "ping_c"])

        code, fails = _run(root)

        assert "交付件数" not in fails, fails
        assert code == 0, f"完整的三件交付不该 FAIL：{fails}"

    def test_item_without_delivery_dir_is_named(self, tmp_path):
        """单件批回归：只有一件、且没交付 → 也要点出它。"""
        root = _project(tmp_path, m6_done=["solo"], delivered=[])

        code, fails = _run(root)

        assert code == 1
        assert "solo" in fails

    def test_stale_item_dir_only_warns(self, tmp_path):
        """交付目录里多出一个本次没交付的件 → 只 WARN（上一次运行的残留）。"""
        root = _project(tmp_path, m6_done=["ping_a"], delivered=["ping_a"],
                        extra_items=["ping_old"])

        code, fails = _run(root)

        assert code == 0, fails
        assert any("ping_old" in w for w in vd.WARNS), vd.WARNS


class TestAtlasRequirement:
    """图集该不该有，要问 M4 的记录，不能一律要求。

    真机 batch3 是**顶点色**项目（没有照片、`paint_mode=vertex`、`uv_baked=False`），
    交付包里本来就没有 `_diffuse.png`。核验逻辑却报
    `[FAIL] 交付包里没有图集 PNG` —— **假 FAIL**。

    假报警比缺检查更糟：它教会人「FAIL 可以先放过去」，那真 FAIL 也会被一起忽略。
    """

    def test_vertex_mode_project_does_not_need_atlas(self, tmp_path):
        """uv_baked=False（顶点色）→ 没有 diffuse.png 也不算错。"""
        root = _project(tmp_path, m6_done=["ping_a"], delivered=["ping_a"],
                        atlas=False, uv_baked=False)

        code, fails = _run(root)

        assert "图集" not in fails, f"顶点色项目被误判成缺图集：{fails}"
        assert code == 0, fails

    def test_photo_mode_project_without_atlas_still_fails(self, tmp_path):
        """uv_baked=True（照片纹理）却拿到没有图集的交付包 → 必须 FAIL。"""
        root = _project(tmp_path, m6_done=["ping_a"], delivered=["ping_a"],
                        atlas=False, uv_baked=True)

        code, fails = _run(root)

        assert code == 1, f"照片纹理项目丢了图集竟然通过：{fails}"
        assert "图集" in fails, fails

    def test_vertex_mode_grey_model_is_a_failure(self, tmp_path):
        """顶点色模式 + `colored=False` → 交付的是灰模，必须 FAIL。"""
        root = _project(tmp_path, m6_done=["ping_a"], delivered=["ping_a"],
                        atlas=False, uv_baked=False, colored=False)

        code, fails = _run(root)

        assert code == 1, fails
        assert "灰模" in fails, fails

    def test_vertex_mode_without_colored_flag_only_warns(self, tmp_path):
        """老 manifest 没有 colored 字段 → WARN，不 FAIL。

        没有证据不等于证据显示没问题，但也不等于证据显示有问题——两头都不能硬判。
        """
        root = _project(tmp_path, m6_done=["ping_a"], delivered=["ping_a"],
                        atlas=False, uv_baked=False)
        mp = os.path.join(root, "90_meta", "run_manifest.json")
        man = json.load(open(mp, encoding="utf-8"))
        for k in list(man["stages"]):
            if k.endswith("/M4"):
                man["stages"][k].pop("colored", None)
        json.dump(man, open(mp, "w", encoding="utf-8"), ensure_ascii=False)

        code, fails = _run(root)

        assert code == 0, fails
        assert any("colored" in w for w in vd.WARNS), vd.WARNS


class TestEveryItemIsChecked:
    """每件的表都要查，不能只看第一件。

    `acc = [f for f in files if basename == "accuracy.csv"]` 然后 `acc[0]`：
    多件批下第二、三件的 CSV 列数不一致（或干脆是空的）完全不会被发现。
    这正是「3 件里 1 件坏了、报表看着正常」的典型形态。
    """

    def test_inconsistent_csv_in_second_item_fails(self, tmp_path):
        root = _project(tmp_path, m6_done=["ping_a", "ping_b"],
                        delivered=["ping_a", "ping_b"])
        bad = os.path.join(root, "80_export", "ping_b", "accuracy.csv")
        write(bad, "item,stage\nping_b,M4\n")

        code, fails = _run(root)

        assert code == 1, f"第二件表结构不一致竟然通过：{fails}"
        assert "accuracy" in fails.lower(), fails

    def test_all_items_consistent_passes(self, tmp_path):
        root = _project(tmp_path, m6_done=["ping_a", "ping_b"],
                        delivered=["ping_a", "ping_b"])

        code, fails = _run(root)

        assert code == 0, fails
        assert any("份" in m and "accuracy" in m for m in vd.OKS), vd.OKS


class TestPhotoModeWithoutUvBakedFlag:
    """照片模式的老 manifest 没有 `uv_baked` 键 —— 不能因此判成顶点色。

    真机 relic_001（photo 模式）的 M4 条目标签是
    `paint_mode=photo / paint_mode_effective=photo / atlas_png=...`，
    **没有 `uv_baked` 这个键**（它只出现在顶点色那条路径上）。
    只用 `uv_baked` 判断会把照片项目误判成顶点色，于是「交付包里没有图集」
    这件真缺陷被降级成"顶点色模式不需要图集"。**假阴性比假阳性更危险。**
    """

    def test_photo_mode_without_flag_still_requires_atlas(self, tmp_path):
        root = _project(tmp_path, m6_done=["ping_a"], delivered=["ping_a"],
                        atlas=False)
        mp = os.path.join(root, "90_meta", "run_manifest.json")
        man = json.load(open(mp, encoding="utf-8"))
        for k in list(man["stages"]):
            if k.endswith("/M4"):
                man["stages"][k]["paint_mode"] = "photo"
                man["stages"][k].pop("uv_baked", None)
        json.dump(man, open(mp, "w", encoding="utf-8"), ensure_ascii=False)

        code, fails = _run(root)

        assert code == 1, f"照片模式丢了图集竟然通过：{fails}"
        assert "图集" in fails, fails


class TestNewLayout:
    """新布局（root 下同时有 阶段成果/ 和 交付成果/）必须认得。

    真机 batch3 就是这个形状，而核验逻辑只找 `root/90_meta` →
    `[FAIL] 找不到 manifest`，把正常的项目报成坏的。
    """

    def test_manifest_under_stage_dir_is_found(self, tmp_path):
        root = str(tmp_path)
        stage = os.path.join(root, "阶段成果")
        write(os.path.join(stage, "00_raw", "x", "scan.ply"), b"cloud")
        write(os.path.join(stage, "90_meta", "run_manifest.json"), json.dumps(
            {"stages": {"x/M4": {"status": "done", "outputs": [],
                                 "uv_baked": False, "colored": True,
                                 "paint_mode": "vertex"},
                        "x/M6": {"status": "done", "outputs": []}}},
            ensure_ascii=False))
        # 交付在 <root>/交付成果/x
        item_dir(root, "x", atlas=False)
        os.makedirs(os.path.join(root, "交付成果"), exist_ok=True)
        os.rename(os.path.join(root, "80_export", "x"),
                  os.path.join(root, "交付成果", "x"))
        write(os.path.join(root, "交付成果", "report", "report.html"),
              "<html>完成率 1/1 X3 X1 X2</html>")

        code, fails = _run(root)

        assert "找不到 manifest" not in fails, fails
        assert code == 0, fails


class TestPerItemFormatCompleteness:
    """M6 已经记了每件**请求了哪些格式、实际交付了哪些**，核验要读它。

    真机历史（r16）：`publish.formats` 请求了 M4 没产的格式 → `formats_missing`
    记下来了，但没人看，用户拿到一个没有 glb 的交付包而日志一切正常。
    只在「manifest 产物都在磁盘」这一层查是不够的——**根本没产出的格式不会出现在
    outputs 里**，那条检查天然看不见它。
    """

    def test_missing_format_in_one_item_is_a_failure(self, tmp_path):
        root = _project(tmp_path, m6_done=["ping_a", "ping_b"],
                        delivered=["ping_a", "ping_b"])
        mp = os.path.join(root, "90_meta", "run_manifest.json")
        man = json.load(open(mp, encoding="utf-8"))
        man["stages"]["ping_b/M6"].update({
            "formats_requested": ["ply", "obj", "glb"],
            "formats_delivered": ["ply", "obj"],
            "formats_missing": ["glb"]})
        json.dump(man, open(mp, "w", encoding="utf-8"), ensure_ascii=False)

        code, fails = _run(root)

        assert code == 1, f"少一个格式竟然通过：{fails}"
        assert "ping_b" in fails and "glb" in fails, fails

    def test_orphan_texture_is_a_failure(self, tmp_path):
        """M6 自己记了 texture_orphan=True（有图集但没载体）→ 必须 FAIL。"""
        root = _project(tmp_path, m6_done=["ping_a"], delivered=["ping_a"])
        mp = os.path.join(root, "90_meta", "run_manifest.json")
        man = json.load(open(mp, encoding="utf-8"))
        man["stages"]["ping_a/M6"]["texture_orphan"] = True
        json.dump(man, open(mp, "w", encoding="utf-8"), ensure_ascii=False)

        code, fails = _run(root)

        assert code == 1, fails
        assert "孤立" in fails, fails
