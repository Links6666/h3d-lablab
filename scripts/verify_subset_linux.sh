#!/usr/bin/env bash
# verify_subset_linux.sh —— Linux COLMAP 子集模型验证（P2，绕 Windows 构建缺陷）
#
# 背景：Windows CUDA 预编译 COLMAP 对「子集模型」解析崩溃（exit 3221226505，
# 详见开发说明第四章）。本脚本在 Linux 环境验证同一子集模型是否可用——
# 若 Linux 正常，则子集 dense 增量路径在 Linux 服务器/容器可闭环。
#
# 用法：
#   bash verify_subset_linux.sh <project_root> <item_id> [colmap_bin]
# 示例：
#   bash verify_subset_linux.sh /data/h3d rock001 /usr/local/bin/colmap
#
# 成功判据：脚本输出 "SUBSET_MODEL_OK"，且 dense_subset/images 含子集图像。

set -e
ROOT="${1:?用法: verify_subset_linux.sh <project_root> <item_id> [colmap_bin]}"
ITEM="${2:?缺少 item_id}"
COLMAP="${3:-colmap}"
MODEL_TXT="$ROOT/99_runs/sfm/sparse/0_txt"
PHOTO_DIR="${ROOT}/../00_photo/$ITEM"
OUT="$ROOT/99_runs/sfm/sparse/0_subset"
DENSE="$ROOT/99_runs/sfm/dense_subset"

# 1) 构建子集模型（6 张已注册图，可改 KEEP 列表）
KEEP=(img_00_04.jpg img_00_03.jpg img_01_03.jpg img_01_04.jpg img_02_03.jpg img_02_04.jpg)
python - "$MODEL_TXT" "$OUT" "${KEEP[@]}" << 'PYEOF'
import sys
from h3d.subset_model import build_subset_model
build_subset_model(sys.argv[1], sys.argv[3:], sys.argv[2])
print("subset model built")
PYEOF

# 2) undistorter（全量照片目录 + 子集模型）
rm -rf "$DENSE"
"$COLMAP" image_undistorter \
  --image_path "$PHOTO_DIR" \
  --input_path "$OUT" \
  --output_path "$DENSE" \
  --output_type COLMAP

N_IMG=$(ls "$DENSE/images" 2>/dev/null | wc -l)
echo "undistorted images: $N_IMG"
if [ "$N_IMG" -ge 4 ]; then
  echo "SUBSET_MODEL_OK"
else
  echo "SUBSET_MODEL_FAIL (images=$N_IMG)" >&2
  exit 1
fi
