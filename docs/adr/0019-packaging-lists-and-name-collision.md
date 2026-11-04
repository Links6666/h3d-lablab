# ADR-0019：打包链路的三处手写清单与一个命名撞车

- 状态：已实装（2026-11-04）
- 相关：ADR-0017（交付核验）、ADR-0018（迁移搬账）
- 代码：`h3d_gui.spec`、`src/h3d/gui/launcher.py`、`src/h3d/runner/__init__.py`
- 触发：给 GUI 加「核验交付包」按钮之后，按惯例真打一次包

## 为什么现在查打包

这一版新增了模块（`h3d.delivery_verify`）和子命令（`h3d verify`）。打包版
**只能靠真打一次才能验**——三条手写清单里任何一条没跟上，表现都是「点了没反应」
或「跑到某个阶段才崩」，而这些在本机源码运行时永远看不到。

真打了两次（各约 3.5 分钟，onefile 238 MB），抓到三个缺陷。

## D1：`hiddenimports` 手写清单漂了 —— 打包版跑到 X3 会崩

`h3d_gui.spec` 的 `hiddenimports` 是手写的，而且当天就漏了：

| 漏掉的 | 后果 |
|---|---|
| `h3d.anomaly` / `h3d.anomaly.*` | **X3 阶段 ModuleNotFoundError**（runner 里是函数内延迟 import，静态分析抓不到） |
| `h3d.archive.*` | 档案层不可用 |
| `h3d.delivery_verify` | 「核验交付包」按钮点了没反应 |
| `h3d.dense_gate` | 双源闸门缺失 |

这份清单**已经漂过两次**（`h3d.core.lockfile`、`h3d.health.*`），每次都是
「新增模块 → 打包版运行时才崩」。

修法：`h3d.*` 全部交给 `collect_submodules('h3d')`（本次收集 111 个模块），
只把**第三方**留在手写清单里（那些没法按包名枚举）。同时把四条历史教训写进
注释——注释是给下一个加模块的人看的。

## D2：打包版的 CLI 透传白名单也漂了 —— 新命令叫不动

`launcher.py` 的 `_CLI_PASSTHROUGH` 决定 `H3D.exe <第一个参数>` 交给 CLI 还是
起 GUI。它写着 `init validate batch report doctor`，而 CLI 实际还有
**`verify` / `archive` / `anomaly` / `anomaly-track`**。

后果：`H3D.exe verify D:/proj` 被当成**无参启动 GUI**——用户以为什么都没发生，
而 `verify` 正是这一版新加的命令。

修法：`cli_passthrough()` 从 `h3d.cli._build_parser()` 的 subparsers 派生，
不再手写；派生失败才退回历史白名单（宁可少支持一个命令，也不能让 GUI 起不来）。

## D3：两个 schema 撞了同一个文件名

- `runner.manifest`（默认 `output/run_manifest.json`）= **阶段账本**，键 `stages`
- `90_meta/delivery_manifest.json` = M6 的**交付摘要**，键 `deliveries`

我自己写的核验配置把 `runner.manifest` 指到了 `delivery_manifest.json`，于是
**M6 每跑一次就用交付摘要覆盖阶段账本**：30 条阶段记录变成 0 条 → 断点续跑全废、
`h3d verify` 报「manifest 里没有任何阶段记录」。这是打包冒烟时撞出来的。

两条修法：

1. **M6 拒绝覆盖账本**：`_delivery_manifest_path()` 发现输出路径与
   `runner.manifest` 相同就返回 `None`，跳过写出并留 WARNING。宁可少一份交付
   摘要，也不能把账本弄没。
2. **核验按「最完整」挑账本**：原来优先读 `delivery_manifest.json`（会被 M6
   的摘要骗），改成盲信 `run_manifest.json` 也不行——探针脚本各自写过残缺账本
   （真机 relic_001 的 `90_meta/run_manifest.json` 只有 6 条、M4 覆盖率还是
   0.0010 的陈年值，照着它核验会把正确产物判成「对不上」）。判据改为
   **阶段记录数 + 产物还在磁盘的个数**，谁更了解这个项目用谁。

## 真机验证

```
打包   ：PyInstaller 3m17s → dist/H3D.exe（238 MB，PE 版本 1.1.17）
         日志：[h3d.spec] h3d 子模块自动收集：111 个
--version：h3d 1.1.17
verify   ：PASS 23 / WARN 0 / FAIL 0     ← 新命令在打包版里叫得动
--batch  ：total=3 done=3 failed=0       ← X3 阶段真执行（模块加载成功，
                                            跳过原因是顶点色没有图集，属正常）
```

`dist/H3D.exe` 已用这次验证过的构建替换（原先是 10-31 的旧包）。

## 未决

- relic_001 的阶段账本历史地落在 `90_meta/delivery_manifest.json`（11 条、产物
  0 缺失）。改名属数据整理，没动；M6 现在会拒绝覆盖并留 WARNING，所以不会再丢。
- 三条清单的收敛这次做完了两条（hiddenimports、透传白名单）。**第三方包**那部分
  只能手写，无法结构上防漂——但漏了会立刻 ImportError，比静默失效好。
