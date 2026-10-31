"""tests.test_runner.test_fingerprint : 阶段输入指纹与续跑判定（B0 切片 5）。

背景
----
B0 只做了「登记侧」，执行器仍按 ``manifest.is_done()`` 判定续跑 —— 那个判据
**只看状态，不看输入**。于是：

* 换了照片 / 改了参数，只要 manifest 说 done、产物还在，就会被静默跳过；
* 用户只能靠手动开 ``force_rerun`` 才能让它重算，而 ``force_rerun`` 是布尔，
  一开就是整批全重跑。

本切片给每个阶段算一个**输入内容指纹**写进 manifest，续跑按指纹判定：
输入没变就跳过，变了就重跑并说明原因。
"""

from __future__ import annotations

import os

import pytest

from h3d.runner.task import Task


# ---------------------------------------------------------------------------
# 配置快照：哪些配置段参与指纹
# ---------------------------------------------------------------------------
def test_content_params_drops_execution_control_sections():
    """``project`` / ``runner`` 是执行控制（路径、并发、续跑开关），
    改它们不该让几何重算 —— 否则改一下 workers 就要重跑 80 秒的 M4。"""
    from h3d.runner.fingerprint import content_params

    cfg_dict = {
        "project": {"name": "x", "raw_dir": "D:/raw"},
        "runner": {"workers": 4, "resume": True, "manifest": "m.json"},
        "mesh": {"poisson_depth": 9},
    }
    got = content_params(cfg_dict)
    assert set(got) == {"mesh"}
    assert got["mesh"] == {"poisson_depth": 9}


def test_content_params_keeps_unknown_sections():
    """**新配置段默认参与指纹**。

    反向设计（白名单式「只取认识的段」）抄错了方向：将来新增一个段、忘了
    登记，就会出现「改了参数却不重跑」—— 正是 r16 那类「修了但没效果」。
    漏算的代价（多跑一次）远小于漏判的代价（悄悄给出旧结果）。
    """
    from h3d.runner.fingerprint import content_params

    got = content_params({"mesh": {}, "some_future_section": {"k": 1}})
    assert "some_future_section" in got


def test_content_params_report_and_publish_stay():
    """``report`` / ``publish`` 影响 M5/M6 产物（报表与交付包），必须参与指纹。"""
    from h3d.runner.fingerprint import content_params

    got = content_params({"report": {"title": "t"}, "publish": {"formats": ["glb"]}})
    assert set(got) == {"report", "publish"}


# ---------------------------------------------------------------------------
# 文件摘要（带缓存）
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _clear_cache():
    from h3d.runner.fingerprint import clear_digest_cache

    clear_digest_cache()
    yield
    clear_digest_cache()


def test_digest_path_is_stable(tmp_path):
    from h3d.runner.fingerprint import digest_path

    p = tmp_path / "a.ply"
    p.write_bytes(b"PLY\n1\n")
    assert digest_path(str(p)) == digest_path(str(p))
    assert len(digest_path(str(p))) == 64


def test_digest_path_changes_with_content(tmp_path):
    from h3d.runner.fingerprint import digest_path

    p = tmp_path / "a.ply"
    p.write_bytes(b"v1")
    first = digest_path(str(p))
    p.write_bytes(b"v2-different-length")
    assert digest_path(str(p)) != first


def test_digest_path_missing_file_raises(tmp_path):
    """缺失文件不能返回空串 —— 空串会被当成「一个大文件算出空哈希」而下沉。"""
    from h3d.runner.fingerprint import digest_path

    with pytest.raises(FileNotFoundError):
        digest_path(str(tmp_path / "nope.ply"))


def test_digest_path_cached_hits_file_once(tmp_path, monkeypatch):
    """同一进程内重复摘要必须命中缓存（照片 200 张 × 5 MB，重算会拖慢跳过判定）。"""
    from h3d.runner import fingerprint

    p = tmp_path / "a.ply"
    p.write_bytes(b"x" * 1024)

    calls = {"n": 0}
    real = open

    def counting_open(*a, **kw):
        calls["n"] += 1
        return real(*a, **kw)

    fingerprint.digest_path(str(p))          # 预热（真实一次 IO）
    monkeypatch.setattr("builtins.open", counting_open)
    fingerprint.digest_path(str(p))
    fingerprint.digest_path(str(p))
    assert calls["n"] == 0


# ---------------------------------------------------------------------------
# 外部输入
# ---------------------------------------------------------------------------
def test_external_inputs_include_raw_and_photos(tmp_path):
    from h3d.runner.fingerprint import external_inputs

    raw = tmp_path / "00_raw" / "r1"
    raw.mkdir(parents=True)
    (raw / "a.ply").write_bytes(b"ply-a")
    photo = tmp_path / "00_photo" / "r1"
    photo.mkdir(parents=True)
    (photo / "1.jpg").write_bytes(b"jpg-1")
    (photo / "2.jpg").write_bytes(b"jpg-2")

    task = Task(item_id="r1", raw_paths=[str(raw / "a.ply")],
                photo_paths=[str(photo / "1.jpg"), str(photo / "2.jpg")],
                root=str(tmp_path))
    got = external_inputs(task)
    assert any(k.startswith("raw/") for k in got)
    assert any(k.startswith("photos/") for k in got)
    assert len(got) == 3


def test_external_inputs_empty_task():
    from h3d.runner.fingerprint import external_inputs

    assert external_inputs(Task(item_id="x")) == {}


def test_external_inputs_skip_missing_files(tmp_path, caplog):
    """文件已被删掉时不能炸掉整条管线，也不能静默算成空哈希。"""
    from h3d.runner.fingerprint import external_inputs

    task = Task(item_id="r1", raw_paths=[str(tmp_path / "gone.ply")])
    with caplog.at_level("WARNING"):
        got = external_inputs(task)
    assert got == {}
    assert any("未计入指纹" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 阶段输入键
# ---------------------------------------------------------------------------
def _task_with_raw(tmp_path, content=b"ply"):
    raw = tmp_path / "00_raw" / "r1"
    raw.mkdir(parents=True, exist_ok=True)
    p = raw / "a.ply"
    p.write_bytes(content)
    return Task(item_id="r1", raw_paths=[str(p)], root=str(tmp_path))


def test_stage_key_stable_for_same_inputs(tmp_path):
    from h3d.runner.fingerprint import stage_input_key

    task = _task_with_raw(tmp_path)
    k1 = stage_input_key("M1", task, {"mesh": {"a": 1}}, {})
    k2 = stage_input_key("M1", task, {"mesh": {"a": 1}}, {})
    assert k1 == k2 and len(k1) == 64


def test_stage_key_changes_with_params(tmp_path):
    from h3d.runner.fingerprint import stage_input_key

    task = _task_with_raw(tmp_path)
    assert (stage_input_key("M1", task, {"mesh": {"a": 1}}, {})
            != stage_input_key("M1", task, {"mesh": {"a": 2}}, {}))


def test_stage_key_ignores_runner_section(tmp_path):
    """execution-control 段变化不得改变键（否则改 workers 就要全批重算）。"""
    from h3d.runner.fingerprint import stage_input_key

    task = _task_with_raw(tmp_path)
    assert (stage_input_key("M1", task, {"runner": {"workers": 1}}, {})
            == stage_input_key("M1", task, {"runner": {"workers": 8}}, {}))


def test_stage_key_changes_with_input_content(tmp_path):
    from h3d.runner.fingerprint import stage_input_key

    task = _task_with_raw(tmp_path, b"v1")
    k1 = stage_input_key("M1", task, {}, {})
    (tmp_path / "00_raw" / "r1" / "a.ply").write_bytes(b"v2")
    from h3d.runner.fingerprint import clear_digest_cache

    clear_digest_cache()
    assert stage_input_key("M1", task, {}, {}) != k1


def test_stage_key_changes_with_upstream(tmp_path):
    """上游阶段键变 → 下游键必须跟着变（跨轮级联重算的机制）。"""
    from h3d.runner.fingerprint import stage_input_key

    task = _task_with_raw(tmp_path)
    assert (stage_input_key("M2", task, {}, {"M1": "a" * 64})
            != stage_input_key("M2", task, {}, {"M1": "b" * 64}))


def test_stage_key_differs_between_stages(tmp_path):
    from h3d.runner.fingerprint import stage_input_key

    task = _task_with_raw(tmp_path)
    assert stage_input_key("M1", task, {}, {}) != stage_input_key("M2", task, {}, {})


# ---------------------------------------------------------------------------
# 续跑判定
# ---------------------------------------------------------------------------
def _done_entry(key=None):
    entry = {"status": "done", "outputs": ["a"]}
    if key:
        entry["input_key"] = key
    return entry


class TestResumeDecision:
    def test_no_record(self):
        from h3d.runner.fingerprint import RESUME_NO_RECORD, resume_decision

        assert resume_decision(None, "k" * 64, True) == RESUME_NO_RECORD

    def test_not_done(self):
        from h3d.runner.fingerprint import RESUME_NOT_DONE, resume_decision

        assert resume_decision({"status": "fail"}, "k" * 64, True) == RESUME_NOT_DONE

    def test_missing_outputs(self):
        from h3d.runner.fingerprint import RESUME_MISSING, resume_decision

        assert resume_decision(_done_entry("k" * 64), "k" * 64, False) == RESUME_MISSING

    def test_legacy_record_without_key_still_resumes(self):
        """1.1.0~1.1.13 的 manifest 没有 input_key。

        不能因此把所有老项目重跑一遍（那是几十小时），只能按旧规则跳过，
        并在日志里说明「该记录无指纹」。用户想按内容判定就 --force-rerun 跑一次。
        """
        from h3d.runner.fingerprint import RESUME_LEGACY, resume_decision

        assert resume_decision(_done_entry(), "k" * 64, True) == RESUME_LEGACY

    def test_same_key_resumes(self):
        from h3d.runner.fingerprint import RESUME_OK, resume_decision

        assert resume_decision(_done_entry("k" * 64), "k" * 64, True) == RESUME_OK

    def test_changed_key_reruns(self):
        from h3d.runner.fingerprint import RESUME_CHANGED, resume_decision

        assert resume_decision(_done_entry("a" * 64), "b" * 64, True) == RESUME_CHANGED

    def test_empty_current_key_never_matches(self):
        """指纹算不出来（磁盘异常等）时**不能**当成匹配，宁可重跑。"""
        from h3d.runner.fingerprint import RESUME_CHANGED, resume_decision

        assert resume_decision(_done_entry("a" * 64), "", True) == RESUME_CHANGED

    @pytest.mark.parametrize("code,skippable", [
        ("ok", True), ("legacy", True),
        ("no_record", False), ("not_done", False),
        ("missing_outputs", False), ("input_changed", False),
    ])
    def test_only_ok_and_legacy_skip(self, code, skippable):
        from h3d.runner.fingerprint import is_skippable

        assert is_skippable(code) is skippable
