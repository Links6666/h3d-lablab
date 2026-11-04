"""打包清单不许手写 `h3d.*` 模块。

`h3d_gui.spec` 的 `hiddenimports` 是一张**手写清单**——本项目"八份副本"里的一份，
历史上已经漂过一次（漏登记导致打包版起来就 ImportError，见 spec 里的注释）。

2026-11-04 又漂了：当天新增的 `h3d.delivery_verify`（GUI「核验交付包」按钮用）
没登记。这类模块都是**函数内延迟 import**，静态分析抓不到，打包版点击时才
ModuleNotFoundError——表现是「按钮点了没反应」，正是最难看的那种。

结构性修法：`h3d.*` 全部由 `collect_submodules('h3d')` 自动收集，只有**第三方**
才手写（那些没法按包名枚举）。这条测试守住这个结构。
"""
import os
import re

SPEC = os.path.join(os.path.dirname(__file__), "..", "h3d_gui.spec")


def _spec_text():
    with open(SPEC, encoding="utf-8") as fh:
        return fh.read()


def _hiddenimports_block(text):
    """截出 hiddenimports=[ ... ] 那段（spec 是普通 Python 文本）。"""
    m = re.search(r"hiddenimports=\[(.*?)\n\s*\],", text, re.S)
    return m.group(1) if m else ""


class TestPackagingListIsNotHandWritten:
    def test_h3d_modules_are_collected_automatically(self):
        text = _spec_text()
        assert "collect_submodules('h3d')" in text or 'collect_submodules("h3d")' in text, (
            "spec 必须用 collect_submodules('h3d') 自动收集本项目的子模块；"
            "手写清单一定会漂（已漂过两次）")

    def test_no_h3d_literal_left_in_hiddenimports(self):
        """`h3d.*` 不许再以字面量形式出现在清单里。

        留一个就是留一处要人记得改的地方。
        """
        block = _hiddenimports_block(_spec_text())
        leftovers = sorted(set(re.findall(r"['\"](h3d(?:\.[\w_]+)+)['\"]", block)))
        assert not leftovers, (
            "hiddenimports 里还有手写的 h3d 模块：" + "、".join(leftovers)
            + "（应由 collect_submodules 自动收集）")

    def test_third_party_still_listed(self):
        """第三方包没法按包名枚举，必须继续手写——别把有用的也删了。"""
        block = _hiddenimports_block(_spec_text())
        for mod in ("trimesh", "psutil", "yaml", "jinja2"):
            assert f"'{mod}'" in block or f'"{mod}"' in block, f"{mod} 从清单里丢了"

    def test_new_module_would_be_covered(self):
        """行为层证据：随便取一个现有子模块，它必须能被自动收集覆盖。

        （不真的跑 PyInstaller——那要几分钟；这里验的是"机制在"，不是"这一次全"）
        """
        src = os.path.join(os.path.dirname(__file__), "..", "src", "h3d")
        names = []
        for dp, _dn, fns in os.walk(src):
            for f in fns:
                if f.endswith(".py"):
                    rel = os.path.relpath(os.path.join(dp, f), src)
                    mod = "h3d." + rel[:-3].replace(os.sep, ".")
                    names.append(mod.replace(".__init__", ""))
        assert "h3d.delivery_verify" in names, "测试自己扫错了目录"

        text = _spec_text()
        if "collect_submodules('h3d')" in text or 'collect_submodules("h3d")' in text:
            return                      # 自动收集 → 天然覆盖
        block = _hiddenimports_block(text)
        missing = [m for m in names if f"'{m}'" not in block]
        assert not missing, "这些模块没登记也不会被自动收集：" + "、".join(missing[:8])
