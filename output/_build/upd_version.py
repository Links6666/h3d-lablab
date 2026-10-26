import io
import os

p = r"E:\h3d_lablab\release\H3D_v1.1.0\VERSION.txt"
old = io.open(p, encoding="utf-8-sig").read()

new = old
new = new.replace("【版本号】        v1.1.0-r4", "【版本号】        v1.1.0-r4.1")
new = new.replace(
    "【本次升级】      实机 S1 崩溃（0xC0000409）根治 + 失败可视化改造\n"
    "                  （含此前：文件选择 / 纯照片模式 / 尺度自适应 / 可复现性加固）",
    "【本次升级】      S1 残留 dense 深度图清理（静默错误根治）+ 实机 S1 崩溃根治\n"
    "                  + 失败可视化改造\n"
    "                  （含此前：文件选择 / 纯照片模式 / 尺度自适应 / 可复现性加固）")
new = new.replace("H3D.exe                             237,782,138 B   绿色免安装主程序（onefile）",
                  "H3D.exe                             237,783,116 B   绿色免安装主程序（onefile）")
new = new.replace("H3D-Setup-v1.1.0.exe                236,386,377 B   Inno Setup 安装包",
                  "H3D-Setup-v1.1.0.exe                {SETUP_SIZE} B   Inno Setup 安装包")
new = new.replace("  6634b4cf3c862db6c2f4a7d3a4d998011533a9e3",
                  "  87c21d22aa5853e1b57fe805488eca986908c684")
new = new.replace("  73a89df0723f71bfcdf22ee8cc17ff020e90a410",
                  "{SETUP_SHA1}")

# 在「r4 修复」段之前插入 r4.1 段
r41 = """--------------------------------------------------------------------------------
2026-10-17 r4.1 修复（全量重建未清 dense —— 静默错误）
--------------------------------------------------------------------------------
【现象】
  上一次运行在 patch_match_stereo 阶段被中断（30 张里做完 30 个 photometric、
  22 个 geometric 深度图）。此时若重新跑全流程，S1 稀疏重建会清库重算，
  但 dense/stereo 里仍留着**旧位姿**算出的深度图。

【根因】
  patch_match_stereo 会跳过已存在的 photometric.bin，只补缺的几张。
  新旧两套位姿的深度图混在一起做 stereo_fusion，点云是错的，
  但**全程不报错**（无异常、无警告、退出码 0）。

【修复】
  1. _reset_sparse_state() 增加 dense 参数：全量重建时一并清空
     <workspace>/dense（保留目录本身，下游按路径写入）。
     增量注册仍不传 dense，不影响复用场景。
  2. run_dense_stereo() 进入前先弃用 <dense>/stereo：
     该函数的语义是「按当前 sparse 重建 dense」，旧深度图永远无效，
     即便被独立调用也不会误复用。

【为什么必须修】
  这是典型的「静默降级」——产物错误、流程全绿、日志干净，
  比直接崩溃更难发现。

"""
new = new.replace("--------------------------------------------------------------------------------\n2026-10-17 r4 修复",
                  r41 + "--------------------------------------------------------------------------------\n2026-10-17 r4 修复")

# 验证记录补充
new = new.replace(
    "· 空库 feature_extractor 实测：30/30 全部 Camera #1，1.6 分钟",
    "· 空库 feature_extractor 实测：30/30 全部 Camera #1，1.6 分钟\n"
    "· r4.1 残留状态清理实测（30 张兵马俑照片，手工制造 db + sparse/0 +\n"
    "  dense/stereo 5 组旧深度图）：\n"
    "    耗时 437 s（7.3 min），dense 清空 ✓，photos_small 保留 ✓，\n"
    "    注册 30/30 ✓，cameras=1 ✓ → RESULT = PASS")

new = new.replace(
    "h3d.sfm_solve                 ✓ 存在  清库 + 日志落盘 + 失败诊断",
    "h3d.sfm_solve                 ✓ 存在  清库/清 dense + 日志落盘 + 失败诊断")

new = new.replace("\n\n\n", "\n\n")
io.open(p, "w", encoding="utf-8-sig", newline="\r\n").write(new)
print("VERSION.txt updated, size =", os.path.getsize(p))
