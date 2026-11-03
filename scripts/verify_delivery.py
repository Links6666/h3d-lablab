"""交付物核验（命令行包装器）。

真正的规则在 :mod:`h3d.delivery_verify` —— `h3d.cli verify` 也走那一份。
这里只保留「从仓库里直接跑」的入口，不再自己实现检查（两份实现必然漂移）。

用法：
    python scripts/verify_delivery.py D:/h3d_relic_test
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from h3d.delivery_verify import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "output/_build/batch3"))
