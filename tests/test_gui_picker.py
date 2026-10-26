"""tests.test_gui_picker : 向导文件选择对话框（2026-10-17 实机缺陷回归）。

缺陷现象
--------
"文件选择板块无法选择照片文件，JPG/PNG 均无法显示"。

根因
----
``DropArea._browse`` 只按 ``accept_dirs`` 分流，照片区 ``accept_dirs``
默认为 True，于是双击进入 ``getExistingDirectory``——该对话框只列目录、
不列任何图片文件。且文件对话框未设置名称过滤器。

本测试锁三件本质行为：
1. 照片区浏览必须走**文件多选**（而非目录对话框）；
2. 图片过滤器与 ``IMAGE_EXTS`` 同源，JPG/PNG 等出现在过滤器内；
3. 多选文件后仍正确收敛为所在目录（否则校验会报"不是文件夹"）。
"""
from __future__ import annotations

import os

import pytest


def _qapp():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class _FakeFileDialog:
    """替换 wizard.QFileDialog，记录调用并返回预设结果（避免弹真窗口）。"""

    open_files_result: list[str] = []
    dir_result: str = ""
    calls: list[str] = []

    @staticmethod
    def getOpenFileNames(parent=None, caption="", directory="", filt=""):
        _FakeFileDialog.calls.append(f"files|{filt}")
        return list(_FakeFileDialog.open_files_result), filt

    @staticmethod
    def getExistingDirectory(parent=None, caption="", directory=""):
        _FakeFileDialog.calls.append("dirs")
        return _FakeFileDialog.dir_result


@pytest.fixture()
def fake_dialog(monkeypatch):
    _qapp()
    from h3d.gui import wizard
    monkeypatch.setattr(wizard, "QFileDialog", _FakeFileDialog)
    _FakeFileDialog.calls = []
    _FakeFileDialog.open_files_result = []
    _FakeFileDialog.dir_result = ""
    return _FakeFileDialog


class TestImageDialogFilter:
    def test_filter_covers_all_supported_exts(self):
        from h3d.core.media import IMAGE_EXTS, image_dialog_filter
        f = image_dialog_filter()
        for ext in IMAGE_EXTS:
            assert f"*{ext}" in f, f"过滤器缺少 {ext}"
        # 实机反馈点名的两种格式
        assert "*.jpg" in f and "*.png" in f

    def test_filter_has_all_files_fallback(self):
        from h3d.core.media import image_dialog_filter
        assert "所有文件 (*.*)" in image_dialog_filter()


class TestDropAreaBrowseMode:
    def test_photo_area_uses_file_dialog_not_directory(self, fake_dialog):
        from h3d.gui.wizard import DropArea
        d = DropArea("照片", collapse_to_dir=True,
                     name_filter="图像文件 (*.jpg *.png)")
        # 关键断言：即便 accept_dirs 为 True，浏览也必须走文件模式
        assert d.accept_dirs is True
        assert d.browse_mode == "files"

    def test_dir_only_area_uses_directory_dialog(self, fake_dialog):
        from h3d.gui.wizard import DropArea
        d = DropArea("目录", accept_dirs=True, accept_files=False)
        assert d.browse_mode == "dirs"

    def test_laser_area_is_file_mode(self, fake_dialog):
        from h3d.gui.wizard import DropArea
        d = DropArea("点云", accept_dirs=False,
                     name_filter="点云/网格 (*.ply *.pcd *.obj)")
        assert d.browse_mode == "files"


class TestBrowseCallPath:
    def test_photo_browse_calls_open_file_names(self, fake_dialog, tmp_path):
        from h3d.gui.wizard import DropArea
        jpg = tmp_path / "IMG_0001.JPG"
        jpg.write_bytes(b"x")
        png = tmp_path / "IMG_0002.png"
        png.write_bytes(b"x")
        fake_dialog.open_files_result = [str(jpg), str(png)]

        d = DropArea("照片", collapse_to_dir=True, browse_mode="files",
                     name_filter="图像文件 (*.jpg)")
        d.browse()

        assert fake_dialog.calls and fake_dialog.calls[0].startswith("files|")
        assert "dirs" not in fake_dialog.calls
        # 多选文件必须收敛为所在目录（单值语义）
        assert d.paths() == [str(tmp_path)]

    def test_photo_browse_replaces_previous_selection(self, fake_dialog, tmp_path):
        from h3d.gui.wizard import DropArea
        old = tmp_path / "old"
        old.mkdir()
        new = tmp_path / "new"
        new.mkdir()
        img = new / "a.jpg"
        img.write_bytes(b"x")

        d = DropArea("照片", collapse_to_dir=True, browse_mode="files")
        d.add_path(str(old))
        fake_dialog.open_files_result = [str(img)]
        d.browse()
        assert d.paths() == [str(new)], "改选照片后不应残留旧目录"

    def test_browse_dir_still_available(self, fake_dialog, tmp_path):
        from h3d.gui.wizard import DropArea
        fake_dialog.dir_result = str(tmp_path)
        d = DropArea("照片", collapse_to_dir=True, browse_mode="files")
        d.browse_dir()
        assert d.paths() == [str(tmp_path)]

    def test_file_picker_passes_image_filter(self, fake_dialog, tmp_path):
        from h3d.core.media import image_dialog_filter
        from h3d.gui.wizard import DropArea
        img = tmp_path / "a.png"
        img.write_bytes(b"x")
        fake_dialog.open_files_result = [str(img)]
        d = DropArea("照片", collapse_to_dir=True, browse_mode="files",
                     name_filter=image_dialog_filter())
        d.browse()
        assert "*.png" in fake_dialog.calls[0]
        assert "*.jpg" in fake_dialog.calls[0]


class TestWizardPhotoRow:
    def test_wizard_photo_area_configured_for_files(self, fake_dialog):
        from h3d.gui.wizard import WizardWindow
        w = WizardWindow(on_done=lambda cfg: None)
        try:
            assert w.drop_photo.browse_mode == "files"
            assert "*.jpg" in w.drop_photo.name_filter
            assert "*.png" in w.drop_photo.name_filter
            # 两个显式入口都存在
            assert w.btn_pick_photos.text() == "选择照片…"
            assert w.btn_pick_dir.text() == "选择文件夹…"
        finally:
            w.deleteLater()

    def test_wizard_photo_pick_collapses_to_dir(self, fake_dialog, tmp_path):
        from h3d.gui.wizard import WizardWindow
        imgs = []
        for i, name in enumerate(("a.JPG", "b.jpeg", "c.PNG")):
            p = tmp_path / name
            p.write_bytes(b"x")
            imgs.append(str(p))
        fake_dialog.open_files_result = imgs

        w = WizardWindow(on_done=lambda cfg: None)
        try:
            w.drop_photo.browse()
            assert w.drop_photo.paths() == [str(tmp_path)]
            # 普通目录：项目根取上级（产物与源素材并列，不污染照片目录）
            assert os.path.abspath(w.edit_root.text()) == os.path.abspath(
                os.path.dirname(str(tmp_path)))
        finally:
            w.deleteLater()


class TestInferRootFromPhoto:
    def test_standard_layout(self, tmp_path):
        from h3d.gui.wizard import infer_root_from_photo
        item = tmp_path / "00_photo" / "relic_001"
        item.mkdir(parents=True)
        assert infer_root_from_photo(str(item)) == str(tmp_path)

    def test_bare_photo_dir(self, tmp_path):
        from h3d.gui.wizard import infer_root_from_photo
        d = tmp_path / "00_photo"
        d.mkdir()
        assert infer_root_from_photo(str(d)) == str(tmp_path)

    def test_plain_photo_dir_uses_parent(self, tmp_path):
        from h3d.gui.wizard import infer_root_from_photo
        d = tmp_path / "photos"
        d.mkdir()
        assert infer_root_from_photo(str(d)) == str(tmp_path)

    def test_drive_root_falls_back_to_dir_itself(self):
        """父级是盘符根时，不能在盘根建 00_raw/00_photo。"""
        import os
        from h3d.gui.wizard import infer_root_from_photo
        drive = os.path.splitdrive(os.path.abspath(os.sep))[0] + os.sep
        p = os.path.join(drive, "h3d_photos_not_exist")
        assert infer_root_from_photo(p) == p

    def test_single_file_input_uses_its_dir(self, tmp_path):
        from h3d.gui.wizard import infer_root_from_photo
        d = tmp_path / "batch"
        d.mkdir()
        f = d / "IMG_0001.jpg"
        f.write_bytes(b"x")
        # 文件 → 所在目录 → 再按普通目录取上级
        assert infer_root_from_photo(str(f)) == str(tmp_path)
