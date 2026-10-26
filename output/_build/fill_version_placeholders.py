"""VERSION.txt 的安装包自指信息处理（两遍法）。

第一遍（不带参数）：把安装包大小与 SHA-1 换成「包内嵌版」指针文本 ——
    真实值记于发行 zip 内的本文件，避免安装包内嵌自己的校验值。
第二遍（带 size sha）：把磁盘这份替换成真实值。

之所以不能让安装包内嵌自己的 SHA-1：写入后安装包内容变化，SHA 又变，
永远对不上。
"""
import io
import os
import re
import sys

P = r"E:\h3d_lablab\release\H3D_v1.1.0\VERSION.txt"
SIZE_MARK = "见发行 zip 内本文件"
SHA_BLOCK = ("（安装包自身的 SHA-1 记于发行 zip 内的 VERSION.txt，\n"
             "  因写入本文件会导致校验值自指）")
NAME = "H3D-Setup-v1.1.0.exe"


def _set_sha_block(text: str, new: str) -> str:
    """替换 `NAME` 行之后、空行之前的 SHA 段（可能是多行）。"""
    marker = NAME + "\n"
    i = text.index(marker) + len(marker)
    j = text.index("\n\n", i)
    return text[:i] + new + text[j:]


text = io.open(P, encoding="utf-8-sig").read()

if len(sys.argv) > 2:
    size, sha = sys.argv[1], sys.argv[2]
    text = re.sub(rf"({re.escape(NAME)}\s+)(\S+ B|{re.escape(SIZE_MARK)})",
                  rf"\g<1>{int(size):,} B", text, count=1)
    text = _set_sha_block(text, f"  {sha}")
    mode = "disk(real values)"
else:
    text = re.sub(rf"({re.escape(NAME)}\s+)([\d,]+ B|{re.escape(SIZE_MARK)})",
                  rf"\g<1>{SIZE_MARK}", text, count=1)
    text = _set_sha_block(text, SHA_BLOCK)
    mode = "package(pointer text)"

io.open(P, "w", encoding="utf-8-sig", newline="\r\n").write(text)
print(f"[{mode}] size={os.path.getsize(P)} "
      f"placeholders_left={text.count('{SETUP')}")
