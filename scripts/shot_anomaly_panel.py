#!/usr/bin/env python
"""X3「异常筛查」页的无头截图（ADR-0003 D14 的证据图）。

用途
----
界面问题用脚本打印是看不出来的：坐标列换行、占比被压成同一格，**测试全绿、
打印全对，只有渲染出来才知道**。这个脚本把面板离屏抓成 PNG，供人工复核或
贴进 ADR。

    python scripts/shot_anomaly_panel.py -p "E:/兵马俑照片/1.1.12" --item relic_001
    python scripts/shot_anomaly_panel.py -p <项目> -o shot.png --row 2

``--row`` 指定选中第几处图斑（0 起）。不带则选第 1 处。
离屏用 ``QT_QPA_PLATFORM=offscreen``：无头环境也能跑，不需要真显示器。
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "src"))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-p", "--path", required=True, help="项目根目录")
    ap.add_argument("--item", default="", help="件号（留空取第一件有产物的）")
    ap.add_argument("-o", "--out", default="anomaly_panel.png")
    ap.add_argument("--row", type=int, default=0, help="选中第几处图斑（0 起）")
    ap.add_argument("--size", default="1280x760", help="窗口尺寸 WxH")
    args = ap.parse_args()

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    from h3d.gui.app import MainWindow
    from h3d.gui.config_edit import ProjectConfig

    cfg = ProjectConfig()
    cfg.name = os.path.basename(os.path.abspath(args.path))
    cfg.root_dir = os.path.abspath(args.path)
    cfg.item_id = args.item
    w, h = (int(x) for x in args.size.lower().split("x"))
    win = MainWindow(cfg)
    win.resize(w, h)
    panel = win.anomaly_panel
    win.tabs.setCurrentIndex(win.tabs.indexOf(panel))
    if panel.table.rowCount():
        panel.table.selectRow(min(args.row, panel.table.rowCount() - 1))
    app.processEvents()

    print(f"件号    {panel.summary.item_id or '（空）'}")
    print(f"状态    {panel.summary.status}")
    print(f"摘要    {panel.lbl_summary.text()}")
    print(f"说明    {panel.lbl_note.text()}")
    print(f"图斑    {panel.table.rowCount()} 处")
    if panel.viewer.has_image():
        print(f"查看器  窗口 {panel.viewer.current_bbox}  "
              f"并排原图 {'有' if panel.viewer.has_reference else '无'}  "
              f"尺寸 {panel.viewer.composite_shape}")
    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    win.grab().save(out)
    print(f"已保存  {out}（{os.path.getsize(out)} 字节）")
    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
