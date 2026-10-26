# GPU 环境说明（H3D v1.0）

H3D 的 GPU 加速依赖链：**cupy-cuda12x（Python）+ COLMAP CUDA 版（C++）**，两者必须对齐
**CUDA 12 运行时**（cublas64_12.dll）。

## 一、版本对齐（关键）

| 组件 | 版本 | 说明 |
|---|---|---|
| cupy-cuda12x | >=13.0 | Python GPU 计算（纹理融合/深度图/泊松） |
| COLMAP CUDA | 3.x（CUDA 12 构建） | 特征/匹配/稠密重建（patch_match_stereo） |
| NVIDIA 驱动 | 560+（支持 CUDA 12.x） | 向后兼容 |

**验证**：
```bash
python -c "import cupy; print(cupy.__version__)"   # cupy-cuda12x
python -c "import cupy as cp; a=cp.zeros(3); print(a.sum())"  # GPU 可用
```
> ⚠️ 若报 `DLL load failed while importing cublas`：cupy 与 COLMAP 的 CUDA 版本不匹配
> （如 cupy-cuda13x 需要 cublas64_13 而 COLMAP 只有 cublas64_12）——统一安装 cupy-cuda12x。

## 二、开启 GPU 路径

1. **配置**：`sfm_solve.dense: true`（稠密重建走 COLMAP GPU）
2. **纹理融合**：photo_texture 泊松融合自动检测 cupy（不可用自动降级 CPU）
3. **深度图（创新点 3）**：`depth_from_stereo_gpu*` 系列（cupy 自动检测）
4. **GUI**：主界面「CUDA 加速」= 自动检测（有 GPU 自动启用）

## 三、打包版（H3D.exe）GPU 说明

- **H3D.exe 内含 GPU 管线所需 Python 依赖**（open3d 等），但 **cupy 未打包**（体积原因）
- 打包版 GUI 内一键生成：默认 CPU 几何路径（sparse/纹理）可用
- **稠密重建（dense）**：H3D.exe 内嵌 COLMAP 调用——需系统 PATH 有 COLMAP CUDA 版
  （或配置 colmap_path 指向）
- 完整 GPU 路径（dense + cupy 加速）：建议用源码安装（pip install -r requirements.txt）
  后运行，或在本机安装 cupy-cuda12x 后使用源码版 GUI

## 四、无 GPU 环境

- `sfm_solve.dense: false`（跳过稠密重建，走激光几何 + 照片纹理场景①）
- 纹理/深度自动降级 CPU（可运行，速度较慢）
- COLMAP 可换 CPU 版（特征/匹配可用）

## 五、常见问题

| 现象 | 原因 | 解决 |
|---|---|---|
| cublas DLL 加载失败 | cupy 与 COLMAP CUDA 版本不匹配 | 统一 cupy-cuda12x |
| patch_match_stereo 崩 0xC0000005 | Windows CUDA 构建 + 大图 | `max_image_size: 2400`（照片预处理） |
| GPU 内存不足 | 大图稠密 | 降低 max_image_size / dense 分块 |
| 无 GPU 但启用 dense | 无 CUDA 设备 | dense: false 或 CPU 版 COLMAP |
