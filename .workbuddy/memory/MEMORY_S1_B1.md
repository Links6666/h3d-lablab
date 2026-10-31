# H3D 细节记忆：B1 档案层与图集 / S1 双场景与 COLMAP

> 从 `MEMORY.md` 拆出来（2026-10-31）。这两块的判据只在动档案层或 S1 时才需要，
> 而 `MEMORY.md` 是每次会话都注入的索引，长度有硬上限。要改这两块时先读这里，
> 再读 `docs/adr/0002-atlas-inheritance.md` 与 `docs/adr/0001-*.md` 的设计过程。

## 档案层与图集（B0–B1，r18）
- 布局 `archive/{artifact.json, epochs/<id>.json, assets/<id>__v<N>.json, atlas/<ver>.json}`；
  **包裹不改** `阶段成果/`；`CanonicalAsset` 只存相对路径+哈希（换盘符不断）。**只读靠代码强制**
  （`open(mode)` 含 `w/a/x/+` 抛 `ReadOnlyViolation`，相对路径锁在项目根内）。
- **`looks_like_project()` 只认旧布局**（新布局在 `阶段成果/` 下判否）→ 建档/迁移用
  `looks_like_h3d_project()`（仍排除 `output`/`report`）。
- `content_key` 必须嵌套+`sort_keys`，禁止拼字符串（`{a:1,b:23}` 与 `{a:12,b:3}` 撞车 → 缓存返回
  错误产物且不报错）；`KEY_SCHEMA` 进 payload。基准期首个时相自动，显式改换必须 WARNING。
- **迁移的 `route` 只是布局推断**（00_raw 有 .ply 就是 A），不是来源验证（1.1.12 有 SfM 冒充
  激光件）；B3 须补真实核验。runner **只在已建档项目**登记产物。
- **建档入口在 GUI「打开项目」**（`ensure_archive` 四态），并在 `_stage_inputs()` **之后**再补一次
  （那步才把数据复制进 00_raw）；`read_status()` 只读刷新绝不建档。
- **空时相必须允许**（`migrate(allow_no_asset=True)`，CLI 默认仍拒）：`_register_archive` 要求
  `baseline()` 非空 → 新建项目首跑前登记永远不发生。`migrate._first_item` 必须探 `00_photo`。
- **「机制写好了但没人调用」**是 B1 最大坑（`AtlasRegistry.register` 曾零调用 → inherit 永远报
  「没有图集记录」）。查这类先 `grep -rn "\.register(" src/`。
- `register_layout(arch, uv_path, item_id=…)`：幂等、变了自动 `atlas-vN+1`+WARNING、坏文件返
  `None`。**图集是件级**（`item_id` 决定幂等范围，否则多件项目第二件判成换版、第一件引用被改写）；
  登记位在 runner `_register_archive`，**先图集后资产**，`size_px` 取 `results["M4"].metrics`。
- **引用必须来自登记者，不能编造**（r16 同类）：曾写死 `uv_atlas_ref="atlas-v1"`。改写入端只对
  新建档生效 → 存量靠 `h3d archive refs` 体检（五态 ok/empty/external/unverified/dangling，判据是
  **档案里的实体**）：`dangling` 置空+原值写 `params.ref_repair`，`unverified` 只报不修；退出码当门
  且要重算**盘上现在**的结论。**修复不能走 `register()`**（命中缓存返回旧记录）→ `rewrite_asset()`。
- **继承不重展开 UV**：用基准 mesh/uv/faces + 复测相机调同一采样器；前提 M4 无条件写
  `50_tex/<item>/<item>_uv.npz`。**uv/faces 属「展开后」拓扑**（xatlas 拆点，顶点数 ≥ 原始 PLY）
  → npz 必须连 `verts/normals` 存，否则越界（index 880 vs size 762）。
- **相机变换** `C'=s·R·C+t`、`R'=R_cam·Rᵀ/s`；`R'` 含 1/s **不是正交阵**（去掉=视野缩放 1/s）。
  判据用**投影恒等式**，不用覆盖率（球各向同性会让反向变换的 in_frame 只差 8 个点）。
- **版本冻结**：同版同 `uv_digest` 幂等、不同则 `AtlasFrozen`；`uv_digest` 先降 float32 再序列化
  （否则 npz 往返判成「图集变了」）。跨项目引用写**限定名** `relic_001@atlas-v1`。
- **五条失败必须显式**（不烘图）：无图集记录/布局缺失损坏/布局与拓扑不符/配准被闸门拒/未建档。
  GUI「先写结论再 emit」会被 `apply()` 覆盖 → **先 emit 再落结论**；测试素材别用纯噪声照片
  （2px 错位跳几百灰度）→ 渐变+棋盘。
- **判据要带反向对照**：只有「做错时明显变大」才算有分辨力（正确 6.33 vs 翻转/旋转 29~32）。

## 双场景 / 尺度
- ①（带扫描件）≠②（纯照片）：S1/P1/X1/X2/X3 在①失败只降级，在② S1 是唯一几何源、失败即整件
  失败；判据 `stage in OPTIONAL_STAGES and bool(task.raw_paths)`。双源几何只 `novel≥0.15` 并入
  （novel=0 照合 → 面数+70%、rmse+34%、覆盖−60%）。
- **无度量基准不得缩放**：`_has_metric_anchor()=bool(task.raw_paths)`，无锚点 factor=1.0 +
  `reason=uncalibrated_sfm_units`；**SfM 的 PLY 塞进 00_raw 会被当锚点 → 假毫米数**。
- 照片目录须同源：优先 `task.root/00_photo` 再回退上级（多写一层 `dirname` → 色卡不生效）。

## COLMAP / 稠密
1. mapper 多子重建，`sparse/0` 未必最大（取图像最多者，图像数读 `images.bin` 头 8B uint64）；
   `--output_path` 有 ExistsDir 校验，目录须先建。
2. 可复现靠 `num_threads 1`；两遍稠密序号各 1..N，判据 grep=2N，进度须识别遍数。
3. `tvec`≠相机中心：`C=-R_co^T@tvec`、`R=-R_co`（前 −Z）；错用致 `projected_cameras=0`、纹理全空。
4. `patch_match_stereo` 跳过已有 photometric.bin（位姿变→点云错乱但 rc=0）；全量重建须清 `<ws>/dense`。
5. 同根两批互毁（`is_open` 失败、GPU 争抢）；`core/lockfile.py` 接管残留锁。
6. 超时覆盖两遍（30 张 3.84MP 实测 6815 s，`views×MP×45×passes` 下限 1800）；续算靠 `_pose_fp`；
   **超时也必须落盘**（否则残留上轮日志 → 误写「需 CUDA」）；取消须杀进程树。
7. `dense_max_image_size` 默认 1600；关 geom_consistency 后 fusion 须 `--input_type photometric`。

