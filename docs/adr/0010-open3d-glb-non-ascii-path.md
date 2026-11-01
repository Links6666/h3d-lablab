# ADR-0010：Open3D 在中文路径下写 GLB 静默失败，并连带毁掉断点续跑

- 状态：已接受
- 日期：2026-11-01
- 关联：ADR-0001（Open3D 写 GLB 静默丢纹理，改用 trimesh）、
  ADR-0007（断点续跑与阶段终态）

## 背景

多件批验证时发现一件怪事：**每次续跑 M4 都重做**，而 M1/M2/M3 都正常跳过。

自检自己报了线索：

```
[自检] 断点续跑状态：3 个「已完成」阶段的产物已不在磁盘上
（ping_a/M4 → ping_a_L1.glb；ping_b/M4 → ping_b_L1.glb；ping_c/M4 → ping_c_L1.glb）
```

manifest 记着 9 个产物，磁盘上只有 6 个——**三个 GLB 全没生成**，而 M4 返回的
`outputs` 里好好地写着它们。

## 根因

```
o3d.io.write_triangle_mesh("output/_build/lodtest/t_L1.glb", mesh)
  -> True，文件存在，2.3 MB

o3d.io.write_triangle_mesh(".../阶段成果/50_tex/ping_a/_probe.glb", mesh)
  -> True，**文件不存在**，0 字节
```

同一个模型、同一个函数、同一个进程，唯一差别是路径。**Open3D 0.19 的 glTF
写出走 ASSIMP，在非 ASCII 路径下返回 `True` 却一个字节都不写**——不抛异常、
不打 warning。

而 `paths.py` 把项目根定名 **`阶段成果`**，所以中文 Windows 用户**天生踩中**。

同一模型换 trimesh 写：两个位置都成功（1.9 MB）。与 ADR-0001 那条
「Open3D 写 GLB 会静默丢纹理，必须 trimesh」是同一根因的另一种表现——那次丢的
是纹理，这次连文件都没有。

## 三重后果

1. **交付包里没有 GLB**：用户勾了 `glb` 格式却拿不到，界面与日志一切正常。
2. **断点续跑在 M4 上完全失效**：`_outputs_exist()` 拿清单核对磁盘，清单里有不
   存在的文件 → 永远判「产物缺失」→ 每次重跑。实测 3 件 × 3 次运行，M1/M2/M3
   都跳过了，只有 M4 每次重做纹理（photo 模式下这是 2 分钟/件）。
3. **诊断成本**：这个缺陷的全部迷惑性来自「返回码说成功」这一句谎。

## 决策

### D1：glTF/GLB 一律用 trimesh 写

`export_colored` 里 `glb`/`gltf` 走 `_write_gltf_trimesh`（顶点色直通）。
PLY/OBJ 保持 Open3D（它们不走 ASSIMP，中文路径正常）。

### D2：写完必须核对磁盘，返回码不是证据

```python
if not os.path.isfile(path) or os.path.getsize(path) == 0:
    log.warning("导出未落盘，已从产物清单剔除：%s —— ...")
    continue          # 不进 outputs
```

**清单必须等于磁盘事实**：`outputs` 是续跑判定的唯一依据，列一个不存在的文件
就等于宣布这个阶段永远没完成。

这一条是通用纪律，不只对 GLB：任何「库函数说成功了」的地方，都该用一次
`isfile` 换取确定性。

### D3：产物缺失要 WARNING，不能静默剔除

`continue` 会少一个格式。所以必须打 WARNING 说明原因（中文路径 + Open3D 静默
失败），并且下游 M6 的 `formats_missing` 检查能发现「请求的格式没产出」。

## 验证

| 运行 | M1 | M2 | M3 | M4 | M5 |
|---|---|---|---|---|---|
| 修前（连续 3 次） | skipped | skipped | skipped | **done 每次重做** | fail |
| 修后第 1 次 | skipped | skipped | skipped | done（补齐 GLB） | fail |
| 修后第 2 次 | skipped | skipped | skipped | **skipped** | fail |

产物：`M4 记录 9 个产物，缺失 0`。自检不再报「产物已不在磁盘上」。

测试 4 项锁住：中文目录下 GLB 存在且非空、三格式全落盘、ASCII 目录不退化、
**写失败的文件不进清单**（用 monkeypatch 模拟 Open3D 的谎）。

## 未决

- 同类风险未普查：其他用 Open3D 写文件的地方（M3 写 `30_dense/*.ply`、M6 复制
  交付物）目前没发现同类问题（PLY 不走 ASSIMP），但没有系统性验证。
- `photo_texture` 侧写 GLB 走的是 trimesh（ADR-0001 已经改过），本轮没动它。
