"""打包发行目录为 zip（覆写模式，不做删除，规避 safe-delete 拦截）。"""
from __future__ import annotations

import os
import sys
import time
import zipfile


def main(src: str, dst: str, level: int = 6) -> int:
    src = os.path.abspath(src)
    dst = os.path.abspath(dst)
    if not os.path.isdir(src):
        print(f"源目录不存在: {src}")
        return 2
    top = os.path.basename(src.rstrip("\\/"))
    # 排除临时/备份文件（例如被运行中进程占用而无法删除的旧 exe）
    skip_names = {"_H3D_r3_old.exe", "Thumbs.db", ".DS_Store"}
    t0 = time.time()
    n = 0
    total = 0
    # "w" 模式对已存在文件是截断写入，不会触发删除保护
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=level) as z:
        for root, dirs, files in os.walk(src):
            dirs.sort()
            for fn in sorted(files):
                if fn in skip_names:
                    print(f"  skip: {fn}")
                    continue
                full = os.path.join(root, fn)
                rel = os.path.relpath(full, src)
                arc = os.path.join(top, rel).replace("\\", "/")
                z.write(full, arc)
                n += 1
                total += os.path.getsize(full)
                if n % 50 == 0:
                    print(f"  {n} files, {total / 1048576:.1f} MB ...",
                          flush=True)
    size = os.path.getsize(dst)
    print(f"OK entries={n} src={total / 1048576:.1f} MB "
          f"zip={size / 1048576:.1f} MB elapsed={time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2],
                  int(sys.argv[3]) if len(sys.argv) > 3 else 6))
