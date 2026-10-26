"""bug-bash 巡检驱动的 charter 集合（临时脚本，位于 _bugbash/，不进版本库）。

每个 charter 只走**公开入口**（CLI 子进程 / 库函数），把自己当成一个第一次
用这个软件的人，记录「期望 vs 实际」。输出 JSON 供合并、triage。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

ROOT = Path(r"E:\h3d_lablab")
SRC = ROOT / "src"
PY = sys.executable
BASE = (ROOT / "output" / "_pkg_smoke" / "config.yaml").read_text(encoding="utf-8")
# 加一行中文注释：真实现场 config.yaml 几乎都有中文注释/中文路径，
# 而全 ASCII 的模板无论存成 UTF-8 还是 GBK 字节都一样，测不出编码问题。
BASE = "# 文物三维建模配置（示例）\n" + BASE
F: list[dict] = []


def add(cid, charter, severity, expected, actual, **extra):
    F.append({"id": cid, "charter": charter, "severity": severity,
              "expected": expected, "actual": actual, **extra})


def run(args, cwd=None, timeout=180, skip_preflight=True, shell=False):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    env["PYTHONIOENCODING"] = "utf-8"
    if skip_preflight:
        env["H3D_SKIP_PREFLIGHT"] = "1"
    else:
        env.pop("H3D_SKIP_PREFLIGHT", None)
    try:
        p = subprocess.run(args, cwd=str(cwd or ROOT), env=env, timeout=timeout,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", shell=shell)
        return p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired:
        return "TIMEOUT", "", ""


def cli(*args, **kw):
    return run([PY, "-m", "h3d.cli", *args], **kw)


def lastline(text: str) -> str:
    lines = [l for l in (text or "").strip().splitlines() if l.strip()]
    return lines[-1][:300] if lines else ""


def has_tb(text: str) -> bool:
    return "Traceback (most recent call last)" in (text or "")


# ---------------------------------------------------------------------------
# C1 · 首次使用者：命令行在「输入不对」时说什么
# ---------------------------------------------------------------------------
def c1_cli(w: Path):
    cases = []

    def case(name, cfg_bytes, expect_clean=True, cmd="validate", expect_ok=False):
        p = w / f"{name}.yaml"
        p.write_bytes(cfg_bytes)
        exp = "以可读的错误信息退出（非 0），不打印 Python 栈"
        if cmd == "validate":
            rc, out, err = cli("validate", "-c", str(p))
        elif cmd == "report":
            rc, out, err = cli("report", "-c", str(p))
        else:
            rc, out, err = cli("batch", "-c", str(p), "--dry-run")
        blob = out + err
        if has_tb(blob):
            add(f"C1-{name}", "cli", "bug", exp,
                f"打印了 Python 栈（rc={rc}）：{lastline(err)}",
                detail=err.strip().splitlines()[-6:])
        elif expect_ok:
            # 合法输入（如记事本存成 UTF-8 带 BOM）：必须 rc=0 且不报错
            if rc == 0:
                cases.append({"case": name, "rc": rc, "msg": "UTF-8 BOM 被正确接受"})
            else:
                add(f"C1-{name}", "cli", "bug", "UTF-8 BOM 的配置应被正常接受",
                    f"rc={rc} {lastline(err) or lastline(out)}")
        elif rc == 0:
            add(f"C1-{name}", "cli", "bug", exp, f"非法输入却 rc=0：{lastline(out)}")
        else:
            cases.append({"case": name, "rc": rc, "msg": lastline(err) or lastline(out)})
        return rc

    # 1) 文件根本不存在
    rc, out, err = cli("validate", "-c", str(w / "nope.yaml"))
    if rc == 0 or has_tb(out + err):
        add("C1-missing_file", "cli", "bug", "干净报错", f"rc={rc} {lastline(err)}")
    else:
        cases.append({"case": "missing_file", "rc": rc, "msg": lastline(err)})

    # 2) 空文件
    case("empty", b"")

    # 3) **记事本存成 UTF-8 带 BOM**（Windows 用户最常见的保存方式）
    case("bom", b"\xef\xbb\xbf" + BASE.encode("utf-8"), expect_ok=True)

    # 4) **记事本存成 ANSI/GBK**（中文 Windows 默认"ANSI"）
    case("gbk", BASE.encode("gbk"))

    # 5) 段落被写成标量（手改缩进最常见的事故）
    case("scalar_section", b"project: 5\n")

    # 6) 未知段落（拼错/遗留字段）
    case("unknown_section", b"zzz_not_a_section: 1\n")

    # 7) 越界数值
    case("range", b"mesh:\n  poisson_depth: 99\n")

    # 8) 负值
    case("negative", b"preprocess:\n  voxel_down_sample: -1\n")

    # 9) 拼错字段名（用户以为生效）
    case("typo_field", b"project:\n  rawdir: ./00_raw\n")

    # 10) 合法但目录不存在
    p = w / "nodir.yaml"
    p.write_text(BASE.replace(str(ROOT / "output" / "_pkg_smoke"), str(w / "ghost")),
                 encoding="utf-8")
    rc, out, err = cli("batch", "-c", str(p), "--dry-run")
    cases.append({"case": "raw_dir_missing", "rc": rc,
                  "msg": lastline(out) or lastline(err)})

    # 11) report 在没有 manifest 时
    p2 = w / "noman.yaml"
    p2.write_text(BASE.replace(str(ROOT / "output" / "_pkg_smoke"), str(w / "noman")),
                  encoding="utf-8")
    rc, out, err = cli("report", "-c", str(p2))
    if has_tb(out + err):
        add("C1-report_no_manifest", "cli", "bug",
            "无报表数据时干净报错/空报表", f"Python 栈：{lastline(err)}",
            detail=err.strip().splitlines()[-6:])
    else:
        cases.append({"case": "report_no_manifest", "rc": rc,
                      "msg": lastline(out) or lastline(err)})

    add("C1-ok", "cli", "pass", "-", f"{len(cases)} 个用例行为正常", detail=cases)


# ---------------------------------------------------------------------------
# C2 · 数据布局：没有一件文物 / 目录很乱时会不会谎报
# ---------------------------------------------------------------------------
def c2_discovery(w: Path):
    sys.path.insert(0, str(SRC))
    from h3d.runner.batch_processor import discover_items

    def layout(name, make, photo_only=False, raw_name="00_raw"):
        d = w / name
        (d / raw_name).mkdir(parents=True, exist_ok=True)
        make(d)
        try:
            tasks = discover_items(str(d / raw_name), ["ply", "pcd", "obj"],
                                   photo_dir=str(d / "00_photo"),
                                   allow_photo_only=photo_only)
            return [(t.item_id, len(t.raw_paths), len(getattr(t, "photo_paths", []) or []))
                    for t in tasks]
        except Exception as exc:  # noqa: BLE001
            return f"EXC {type(exc).__name__}: {exc}"

    def touch(p: Path, n=1):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"ply\nformat ascii 1.0\nend_header\n" * n)

    # 目录存在但完全空 -> 0 件（应让上层报"没有任务"，不能假成功）
    r = layout("empty", lambda d: None)
    add("C2-empty", "discovery", "pass" if r == [] else "bug",
        "空目录 -> 0 件", f"{r}")

    # 只有不可识别后缀（用户放的是 .e57/.las）
    def bad(d):
        (d / "00_raw" / "a").mkdir(parents=True)
        (d / "00_raw" / "a" / "x.e57").write_bytes(b"x")
    r = layout("bad_ext", bad)
    add("C2-bad_ext", "discovery", "bug" if r != [] else "pass",
        "无可用格式 -> 0 件（并提示允许的扩展名）",
        f"{r}（若静默 0 件，用户看不出为什么）")

    # 中文 + 空格 + 括号路径
    def cn(d):
        touch(d / "00_raw" / "青瓷 碗 (1)" / "站 1.ply")
    r = layout("cn_path", cn)
    add("C2-cn_path", "discovery", "pass" if r == [("青瓷 碗 (1)", 1, 0)] else "bug",
        "中文/空格/括号目录名 -> 1 件", f"{r}")

    # 隐藏目录 / 系统垃圾（.DS_Store、Thumbs.db、__MACOSX）
    def junk(d):
        touch(d / "00_raw" / "relic" / "a.ply")
        (d / "00_raw" / "__MACOSX").mkdir()
        touch(d / "00_raw" / "__MACOSX" / "._a.ply")
        (d / "00_raw" / ".git").mkdir()
        touch(d / "00_raw" / ".git" / "b.ply")
    r = layout("junk", junk)
    add("C2-junk_dirs", "discovery", "bug" if (isinstance(r, list) and len(r) > 1) else "pass",
        "系统垃圾目录不该变成文物（期望只剩 1 件）", f"{r}")

    # 同一件里重复照片（同名不同大小写）
    def dup(d):
        touch(d / "00_raw" / "relic" / "a.ply")
        for n in ("IMG_1.JPG", "img_1.jpg", "IMG_2.JPG"):
            (d / "00_photo" / "relic").mkdir(parents=True, exist_ok=True)
            (d / "00_photo" / "relic" / n).write_bytes(b"jpg")
    r = layout("dup_photo", dup, photo_only=True)
    add("C2-dup_photo", "discovery", "pass" if isinstance(r, list) and len(r) == 1 else "bug",
        "大小写重复照片仍是 1 件（或明确提示重复）", f"{r}")

    # 纯照片：00_photo/<item>/ 有图、00_raw 不存在
    d = w / "photo_only"
    (d / "00_photo" / "p1").mkdir(parents=True)
    (d / "00_photo" / "p1" / "a.jpg").write_bytes(b"jpg")
    try:
        tasks = discover_items(str(d / "00_raw"), ["ply"], photo_dir=str(d / "00_photo"),
                               allow_photo_only=True)
        r = [(t.item_id, len(t.raw_paths)) for t in tasks]
        add("C2-photo_only", "discovery", "pass" if r == [("p1", 0)] else "bug",
            "纯照片件 -> 1 件、raw_paths=0", f"{r}")
    except Exception as exc:  # noqa: BLE001
        add("C2-photo_only", "discovery", "bug", "纯照片件 -> 1 件",
            f"EXC {type(exc).__name__}: {exc}")

    # 00_photo 下有空目录（用户建了目录还没导照片）
    d = w / "photo_empty_dir"
    (d / "00_photo" / "ghost").mkdir(parents=True)
    try:
        tasks = discover_items(str(d / "00_raw"), ["ply"], photo_dir=str(d / "00_photo"),
                               allow_photo_only=True)
        r = [t.item_id for t in tasks]
        add("C2-photo_empty_dir", "discovery", "bug" if r else "pass",
            "空照片目录不该产出任务（否则 S1 必然失败）", f"{r}")
    except Exception as exc:  # noqa: BLE001
        add("C2-photo_empty_dir", "discovery", "bug", "空照片目录不产出任务",
            f"EXC {type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# C3 · 自检本身：脚本可用性 / 退出码 / 误报
# ---------------------------------------------------------------------------
def c3_health(w: Path):
    proj = w / "proj"
    (proj / "00_raw" / "r1").mkdir(parents=True)
    (proj / "00_raw" / "r1" / "a.ply").write_bytes(b"ply\nformat ascii 1.0\nend_header\n")
    cfg = proj / "config.yaml"
    cfg.write_text(BASE.replace(str(ROOT / "output" / "_pkg_smoke"), str(proj)),
                   encoding="utf-8")

    # 1) doctor --json 的 stdout 必须是纯 JSON（脚本/CI 依赖）
    rc, out, err = cli("doctor", "-c", str(cfg), "--json", skip_preflight=False)
    try:
        json.loads(out)
        ok = True
        parsed = json.loads(out)
    except Exception as exc:  # noqa: BLE001
        ok = False
        parsed = None
        add("C3-json_purity", "health", "bug",
            "--json 的 stdout 是纯 JSON，日志走 stderr",
            f"stdout 无法解析（{type(exc).__name__}）；首行：{out.strip().splitlines()[:1]}",
            detail=[(err or '').strip().splitlines()[:3]])
    if ok:
        add("C3-json_purity", "health", "pass", "-", f"keys={list(parsed)}")
        for scope in ("boot", "project"):
            if scope in parsed and not isinstance(parsed[scope].get("results"), list):
                add(f"C3-json_shape_{scope}", "health", "bug", "results 为列表",
                    str(type(parsed[scope].get("results"))))

    # 2) doctor 的退出码语义：仅有 WARNING 时应为 0（CI 不该被提示卡死）
    blockers = [r for s in ("boot", "project") if parsed and s in parsed
                for r in parsed[s].get("results", []) if r.get("severity") == "blocker"]
    warns = [r for s in ("boot", "project") if parsed and s in parsed
             for r in parsed[s].get("results", []) if r.get("severity") == "warning"]
    add("C3-exitcode", "health", "pass" if (bool(blockers) == (rc != 0)) else "bug",
        "rc=0 当且仅当无 blocker",
        f"rc={rc} blockers={len(blockers)} warnings={len(warns)}")

    # 3) 全新项目（有输入、无产物）不该被阻断
    add("C3-fresh_not_blocked", "health", "pass" if not blockers else "bug",
        "带输入的全新项目不产生 blocker",
        f"blockers={[r.get('name') for r in blockers]}")


# ---------------------------------------------------------------------------
# C4 · 断点续跑：manifest 被写坏 / 版本不匹配
# ---------------------------------------------------------------------------
def c4_resume(w: Path):
    sys.path.insert(0, str(SRC))
    from h3d.core import RunManifest

    # 1) JSON 被截断（进程被 kill 时最常见）
    p = w / "truncated.json"
    p.write_text('{"run_id": "r1", "stages": {"r1/M1": {"status": "do', encoding="utf-8")
    try:
        RunManifest.load(str(p))
        add("C4-truncated", "resume", "pass", "-", "可加载")
    except Exception as exc:  # noqa: BLE001
        msg = f"{type(exc).__name__}: {exc}"
        add("C4-truncated", "resume",
            "warn" if "JSONDecodeError" in msg else "bug",
            "manifest 损坏时应重建（并备份旧文件），而不是让整个项目跑不动",
            msg, detail=["调用方是否有 try/except 决定这是 bug 还是 warn"])

    # 2) 结构缺字段（旧版本写的 manifest）
    p2 = w / "old.json"
    p2.write_text('{"run_id": "r1"}', encoding="utf-8")
    try:
        m = RunManifest.load(str(p2))
        add("C4-old_shape", "resume", "pass", "-", f"stages={len(getattr(m, 'stages', {}))}")
    except Exception as exc:  # noqa: BLE001
        add("C4-old_shape", "resume", "bug", "旧版 manifest 可加载",
            f"{type(exc).__name__}: {exc}")

    # 3) 阶段键没有 "/"（手改/早期格式）
    p3 = w / "flatkey.json"
    p3.write_text('{"run_id":"r1","stages":{"M1":{"status":"done","outputs":[]}}}',
                  encoding="utf-8")
    try:
        m = RunManifest.load(str(p3))
        ok = m.is_done("r1/M1") is not None
        add("C4-flat_key", "resume", "pass", "-", f"is_done(r1/M1)={m.is_done('r1/M1')}")
    except Exception as exc:  # noqa: BLE001
        add("C4-flat_key", "resume", "bug", "异常键不应崩",
            f"{type(exc).__name__}: {exc}")

    # 4) stages 是 list 而不是 dict
    p4 = w / "badtype.json"
    p4.write_text('{"run_id":"r1","stages":[]}', encoding="utf-8")
    try:
        RunManifest.load(str(p4))
        add("C4-badtype", "resume", "pass", "-", "可加载")
    except Exception as exc:  # noqa: BLE001
        add("C4-badtype", "resume", "bug", "stages 类型错误应给可读错误",
            f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# C5 · 报表：退化数据不能说谎、不能崩
# ---------------------------------------------------------------------------
def c5_report(w: Path):
    sys.path.insert(0, str(SRC))
    from h3d.core import RunManifest, ReportConfig
    from h3d.report import generate

    def build(name, stages):
        m = RunManifest(run_id="r1")
        m.stages = stages
        return m

    cases = {
        "empty": {},
        "all_fail": {"a/M1": {"status": "fail", "reason": "[E100] 参考点云为空",
                              "elapsed_s": 0.0, "outputs": []}},
        "weird": {
            "中文件/M5": {"status": "done", "elapsed_s": float("nan"),
                          "outputs": ["x.ply"], "rmse": float("nan"),
                          "coverage": float("inf")},
            "a/b/M2": {"status": "done", "elapsed_s": 1e12, "outputs": ["y.ply"]},
        },
        "unknown_status": {"a/M1": {"status": "??", "outputs": []}},
        "missing_keys": {"a/M1": {}},
    }
    for name, stages in cases.items():
        out = w / f"rep_{name}"
        try:
            files = generate(build(name, stages), [], ReportConfig(
                enabled=True, output_dir=str(out),
                formats=["csv", "png", "html"], project_name="巡检测试"))
            html = ""
            for f in files:
                if f.endswith(".html"):
                    html = Path(f).read_text(encoding="utf-8")
            low = html.lower()
            bad_nan = ">nan<" in low or ">inf<" in low or " nan " in low
            add(f"C5-{name}", "report", "bug" if bad_nan else "pass",
                "缺值一律 N/A，绝不出现 nan/inf",
                f"files={[Path(x).name for x in files]} nan_in_html={bad_nan}")
        except Exception as exc:  # noqa: BLE001
            add(f"C5-{name}", "report", "bug", "退化数据仍能出报表",
                f"{type(exc).__name__}: {exc}",
                detail=traceback.format_exc().strip().splitlines()[-4:])


# ---------------------------------------------------------------------------
# C6 · 打包版契约（用户拿到的是 exe，不是源码）
# ---------------------------------------------------------------------------
def c6_exe(w: Path):
    exes = sorted(ROOT.glob("build_out_v*/H3D.exe"), key=lambda p: p.stat().st_mtime)
    if not exes:
        add("C6-none", "exe", "warn", "-", "找不到打包产物，跳过")
        return
    exe = exes[-1]
    rc, out, err = run([str(exe), "--version"], skip_preflight=True, timeout=120)
    add("C6-version", "exe", "pass" if rc == 0 and out.strip() else "bug",
        "exe --version 正常输出", f"rc={rc} out={out.strip()[:80]!r} err={lastline(err)}")

    rc, out, err = run([str(exe), "doctor", "--json"], skip_preflight=False, timeout=180)
    try:
        json.loads(out[out.find("{"):])
        add("C6-doctor_json", "exe", "pass", "-", "JSON 可解析")
    except Exception as exc:  # noqa: BLE001
        add("C6-doctor_json", "exe", "bug", "打包版 doctor --json 的 stdout 是纯 JSON",
            f"{type(exc).__name__}；前 200 字符：{out[:200]!r}")

    cfg = w / "exe_cfg.yaml"
    cfg.write_text(BASE.replace(str(ROOT / "output" / "_pkg_smoke"), str(w / "exe_proj")),
                   encoding="utf-8")
    (w / "exe_proj").mkdir(parents=True, exist_ok=True)
    rc, out, err = run([str(exe), "batch", "-c", str(cfg), "--dry-run"],
                       skip_preflight=False, timeout=180)
    add("C6-batch_empty", "exe", "pass" if rc == 0 or "没有" in (out + err) else "bug",
        "空项目批量应给明确结论（0 件 / 无任务）", f"rc={rc} {lastline(out) or lastline(err)}")


CHARTERS = {"c1": c1_cli, "c2": c2_discovery, "c3": c3_health,
            "c4": c4_resume, "c5": c5_report, "c6": c6_exe}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("charter", choices=list(CHARTERS))
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    w = ROOT / "_bugbash" / "tmp" / a.charter
    if w.exists() and not a.keep:
        shutil.rmtree(w)
    w.mkdir(parents=True, exist_ok=True)
    try:
        CHARTERS[a.charter](w)
    except Exception:  # noqa: BLE001
        add(f"{a.charter}-harness", a.charter, "warn", "charter 跑完",
            "charter 自身异常", detail=traceback.format_exc().splitlines()[-5:])
    print("###FINDINGS###")
    print(json.dumps(F, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
