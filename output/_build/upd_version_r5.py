"""更新 VERSION.txt 到 v1.1.0-r5（两遍法：先写包内版，编译后回填真实值）。"""
import io
import os
import sys

p = r"E:\h3d_lablab\release\H3D_v1.1.0\VERSION.txt"
EXE_SIZE = 237793950
EXE_SHA = "2eda316f797f59ae2aa98e3562232687ec07826d"
SIZE_MARK = "见发行 zip 内本文件"
SHA_BLOCK = ("（安装包自身的 SHA-1 记于发行 zip 内的 VERSION.txt，\n"
             "  因写入本文件会导致校验值自指）")

t = io.open(p, encoding="utf-8-sig").read()

# 1) 头部
t = t.replace("【版本号】        v1.1.0-r5", "【版本号】        __V__")
t = t.replace("【版本号】        v1.1.0-r4.1", "【版本号】        v1.1.0-r5")
t = t.replace("__V__", "v1.1.0-r5")
t = t.replace(
    "【本次升级】      S1 残留 dense 深度图清理（静默错误根治）+ 实机 S1 崩溃根治\n"
    "                  + 失败可视化改造",
    "【本次升级】      打包版解包目录隔离（56 分钟崩溃根治）+ 生成页进度可视化\n"
    "                  （含此前：残留 dense 清理 / S1 崩溃根治 / 失败可视化）")

# 2) 文件清单
import re
t = re.sub(r"H3D\.exe(\s+)[\d,]+ B   绿色免安装主程序",
           lambda m: f"H3D.exe{m.group(1)}{EXE_SIZE:,} B   绿色免安装主程序", t)
t = re.sub(r"H3D-Setup-v1\.1\.0\.exe(\s+)[\d,]+ B   Inno Setup",
           lambda m: f"H3D-Setup-v1.1.0.exe{m.group(1)}{SIZE_MARK}   Inno Setup", t)

# 3) 校验值
t = re.sub(r"\n  [0-9a-f]{40}\n\nH3D-Setup", f"\n  {EXE_SHA}\n\nH3D-Setup", t, count=1)
t = re.sub(r"H3D-Setup-v1\.1\.0\.exe\n  [0-9a-f]{40}",
           "H3D-Setup-v1.1.0.exe\n" + SHA_BLOCK, t)
t = re.sub(r"H3D-Setup-v1\.1\.0\.exe\n  " + re.escape(SIZE_MARK), 
           "H3D-Setup-v1.1.0.exe\n  " + SHA_BLOCK, t)

# 4) 插入 r5 段
r5 = """--------------------------------------------------------------------------------
2026-10-17 r5 修复（打包版 56 分钟后崩溃 + 生成页无进度）
--------------------------------------------------------------------------------
【现象】
  30 张照片跑纯照片模式，稀疏与稠密全部完成（56 分钟），最后一步报
  FileNotFoundError: ...\\_MEI000065282\\open3d，两个造像全部失败，
  界面无任何产物，报表只有失败行。同时"生成页面"从第 5 分钟起毫无变化，
  无法判断是在跑还是卡死。

【根因】
  打包版 onefile 用 _PYI_* / _MEIPASS2 环境变量让 --batch 子进程复用
  GUI 进程的解包目录 %TEMP%\\_MEIxxxxxx。GUI 一退出，bootloader 删除该目录，
  而子进程还在跑。S1 全程不 import open3d（照片预处理用 PIL、位姿解析用
  numpy、模型筛选 struct），直到最后 load_point_cloud(fused.ply)
  才第一次 import —— 所以崩溃点被推迟到全部计算完成之后。
  同一 exe 跑最小点云 M1~M6 全通，可证明 open3d 文件本身齐全。

【修复】
  1. 子进程环境剔除 _MEIPASS2 / _PYI_APPLICATION_HOME_DIR /
     _PYI_ARCHIVE_FILE / _PYI_PARENT_PROCESS_LEVEL，独立解包。
  2. 运行中关窗口先确认并终止子进程，不留孤儿进程。
  3. --batch 启动打印「批处理进程 N 解包目录：...」，便于事后判断是否复用。

【生成页进度可视化】
  4. 阶段灯支持"运行中"（橙）：由新增的「▶ S1 开始」日志驱动。
  5. 状态行实时显示：● S1 运行中 · 深度图 12/30　已运行 00:12:34。
  6. 每 3 秒扫 depth_maps 统计深度图完成数（稠密阶段唯一能看出在动的地方）。
  7. COLMAP 输出实时转发并清洗成中文：
       === Processing view 12 / 30 for DSC_0019.JPG ===
         -> 深度图 12/30：DSC_0019.JPG
       另有 去畸变/融合/匹配块/注册图像/全局光束法平差。
  8. 日志按语义着色：错误红、警告橙、阶段切换青、完成绿、加载进度浅蓝加粗。
  9. metrics_summary.csv 写出失败时附带系统原文，区分"被占用/无权限/无目录"。

"""
anchor = ("--------------------------------------------------------------------------------\n"
          "2026-10-17 r4.1 修复")
if anchor in t:
    t = t.replace(anchor, r5 + anchor, 1)

# 5) 验证记录
t = t.replace(
    "· 全量单元测试：508 passed / 0 failed / 0 error",
    "· 全量单元测试：530 passed / 0 failed / 0 error（r5 新增 test_gui_progress.py 19 项）")
t = t.replace(
    "· 空库 feature_extractor 实测：30/30 全部 Camera #1，1.6 分钟",
    "· 空库 feature_extractor 实测：30/30 全部 Camera #1，1.6 分钟\n"
    "· r5 打包冒烟：新 exe 跑最小点云 M1~M6 全通（EXIT=0），\n"
    "  启动行打印独立解包目录 _MEI00004e582\n"
    "· r5 端到端（3 张照片，dense 关）日志时序：\n"
    "  ▶ S1 开始 → 匹配块 1/1 → 全局光束法平差 → 注册图像 #3 →\n"
    "  S1 完成 3/3 张注册 1 相机 18.8s → ▶ M1 开始")

# 6) 模块清单
t = t.replace(
    "h3d.gui.app / preview         ✓ 存在  预览回退 + 失败提示",
    "h3d.gui.app / preview         ✓ 存在  预览回退 + 失败提示 + 进度状态行\n"
    "h3d.gui.runner / logparse     ✓ 存在  子进程解包隔离 + 阶段/子步骤解析\n"
    "h3d.core.proc                 ✓ 存在  run_external 流式进度回调")

io.open(p, "w", encoding="utf-8-sig", newline="\r\n").write(t)
print("[package] size =", os.path.getsize(p), "placeholder_left =", t.count("{SETUP"))
