# -*- coding: utf-8 -*-
"""生成「文物数字化建模管线色彩来源技术分析与方案设计」content.json。

背景：童永东博士论文技术路线的色彩纹理源自「相机拍摄 + Metashape 纹理映射」，
而当前 h3d 管线仅依赖 PLY 文件自带 RGB——存在技术落差。本文档为纯分析展望，
不改代码。结构：现状分析 -> 缺陷剖析 -> 4 条改进路线 -> 落地建议 -> 总结。
"""
import json

content = [
    # =====================================================================
    {"type": "h1", "text": "一、引言与背景"},
    {"type": "body",
     "text": "文物数字化建模的最终交付质量，几何精度与<b>色彩纹理</b>同等重要——彩塑造像的赋彩、壁画"
             "的笔触、鎏金器的光泽，都依赖高保真颜色信息来承载。童永东博士论文的技术路线为：<b>激光扫描"
             "获取高精度几何；独立高清相机拍摄色彩照片；在 Metashape 中将高清影像投影映射到高精度几何"
             "网格上生成 UV 纹理贴图</b>——几何与色彩采集分离（几何来自激光雷达、色彩来自独立相机影像），"
             "而非点云顶点携带 RGB。而当前 h3d 管线（M1~M6）的色彩来源仅为<b>三维扫描仪输出的 PLY 点云"
             "自带 RGB 颜色</b>，与博士论文路线存在明确技术落差。"},
    {"type": "callout",
     "text": "<b>问题界定（依据方向文档）</b>：真实文物数字化工程实践中，地面三维激光扫描仪（TLS，"
             "如 Faro、Riegl）输出的 las 点云<b>仅包含几何坐标与激光回波强度 intensity，不具备可见光 "
             "RGB</b>；intensity 是单通道回波信号 ≠ 可见光色彩，不能用于色彩还原与 X2-CIEDE2000 色差"
             "评估。行业标准 WW/T 0115-2023、DB11/T 1922-2021 明确：高精度几何（激光扫描）与高分辨率"
             "色彩（单反相机）是<b>两套独立采集设备</b>。当前管线无法接收独立照片作为色彩源，无法复现"
             "上述主流业务工作流。本文档据此系统分析现状差异、剖析缺陷，给出将相机彩色照片作为可导入"
             "源文件接入现有管线的多条技术路线与分阶段落地建议。<b>本文档为分析与展望，不涉及代码修改</b>。"},

    # =====================================================================
    {"type": "h1", "text": "二、现状分析：两种色彩来源方案对比"},
    {"type": "h2", "text": "2.1 方案 A：相机拍摄 + Metashape 纹理映射（主流业务工作流）"},
    {"type": "body",
     "text": "行业标准工作流：TLS 激光扫描 → 获取高精度几何点云；独立单反环绕拍摄高清彩色照片（摆放"
             "色卡用于色彩校准）；将几何导入 Metashape，把照片投影映射到几何网格生成 UV 纹理；输出 "
             "obj+mtl+贴图交付成果。Metashape（Agisoft，商业，Python API/Headless 可脚本化）内部："
             "<b>SfM</b> 从多视角照片解算相机位姿与稀疏点云（特征匹配 + 光束法平差 BA）→ <b>MVS</b> 稠密"
             "重建 → 网格 → <b>纹理映射</b>：为每个三角面片选择视角最优的照片投影颜色（可见性 + 分辨率 + "
             "角度加权），多图混合消除接缝，输出带纹理模型（OBJ+MTL+PNG/TIFF、GLB）。"},
    {"type": "flowchart",
     "nodes": [
         {"id": "las", "label": "TLS 激光扫描（高精度几何，无 RGB）", "shape": "parallelogram"},
         {"id": "cam", "label": "独立单反环绕拍摄（高清彩色照片 + 色卡）", "shape": "parallelogram"},
         {"id": "sfm", "label": "Metashape：SfM 相机位姿对齐", "shape": "rect"},
         {"id": "tex", "label": "BuildTexture：照片投影烘焙 UV 图集", "shape": "rect"},
         {"id": "out", "label": "带纹理交付（obj+mtl+贴图 / GLB）", "shape": "parallelogram"},
     ],
     "edges": [
         {"from": "las", "to": "tex", "label": "网格导入"},
         {"from": "cam", "to": "sfm"}, {"from": "sfm", "to": "tex"},
         {"from": "tex", "to": "out"},
     ],
     "caption": "方案 A（行业标准）：几何（激光）与色彩（相机）两套独立采集，Metashape 投影生成 UV 纹理"},
    {"type": "h2", "text": "2.2 方案 B：PLY 自带 RGB（当前 h3d 管线）"},
    {"type": "body",
     "text": "当前管线（00_raw → M1 预处理 → M2 配准 → M3 网格重建 → M4 纹理 → M5 质检 → M6 发布）的"
             "颜色来源为扫描仪输出的 PLY 点云自带 RGB：扫描仪纹理传感在采样几何时同时记录每点颜色，经 "
             "M1~M2 传递，M4 以顶点色映射（每网格顶点取点云最近邻颜色）或 xatlas UV 图集烘焙（把点云"
             "颜色光栅化到纹理图）产出带色模型。<b>注意：UV-xatlas 模块只改善输出存储形式，色彩源头"
             "依旧是 PLY 点云</b>——点云无 RGB 时它同样无法产出彩色纹理。"},
    {"type": "flowchart",
     "nodes": [
         {"id": "scan", "label": "扫描仪采样（几何 + 点级 RGB 同时记录）", "shape": "parallelogram"},
         {"id": "ply", "label": "PLY 点云（顶点颜色随坐标存储）", "shape": "parallelogram"},
         {"id": "pre", "label": "M1 预处理 / M2 配准（颜色随点云传递）", "shape": "rect"},
         {"id": "m3", "label": "M3 网格重建（顶点色附着）", "shape": "rect"},
         {"id": "m4", "label": "M4 纹理：顶点色映射 / xatlas UV 烘焙（源=点云 RGB）", "shape": "rect"},
         {"id": "out2", "label": "带色模型（PLY/OBJ/GLB，颜色源自点云）", "shape": "parallelogram"},
     ],
     "edges": [
         {"from": "scan", "to": "ply"}, {"from": "ply", "to": "pre"},
         {"from": "pre", "to": "m3"}, {"from": "m3", "to": "m4"},
         {"from": "m4", "to": "out2"},
     ],
     "caption": "方案 B（当前管线）：颜色与几何绑定于 PLY 点云，UV 模块不改色彩源头"},
    {"type": "h2", "text": "2.3 六维度对比总结表"},
    {"type": "table",
     "headers": ["维度", "方案 A：相机 + Metashape", "方案 B：PLY 自带 RGB（当前）"],
     "rows": [
         ["成像质量", "独立单反决定（2400 万像素级照片）；几何与色彩分离、互不拖累",
          "扫描仪纹理传感模块决定；与几何采样一体，无独立成像单元"],
         ["几何精度", "几何来自激光扫描（亚毫米级，独立于照片）；SfM 仅解算相机位姿",
          "扫描几何精度高，但颜色质量随设备差异大"],
         ["纹理清晰度", "2400 万像素原始照片直接投影，贴图分辨率不受点云密度限制，高频细节完整保留",
          "每顶点 1 个 RGB；纹理清晰度受网格顶点密度限制（等效纹理分辨率极低）"],
         ["色彩还原度", "照片经色卡校准（ColorChecker）/白平衡可接近人眼感知；多照片曝光差异需混合校正",
          "扫描仪光源与响应曲线固定，易偏色/噪点；intensity 非可见光，无法用于色彩还原"],
         ["数据体积", "数百张照片 + 纹理图集（100MB~数 GB），纹理独立归档",
          "点云文件小（RGB 内嵌于顶点），无独立纹理资产"],
         ["适用场景", "彩塑、壁画、鎏金等<b>色彩主导</b>文物；行业标准（WW/T 0115-2023、DB11/T 1922-2021）",
          "彩色结构光扫描仪（少数输出 RGB 的设备）场景；快速批量建档；无照片条件下的降级方案"],
     ]},
    {"type": "callout",
     "text": "<b>结论</b>：方案 A 是行业标准与主流实践（童永东博士论文路线同构）；方案 B 依赖「扫描仪"
             "自带 RGB」这一在 TLS 现实场景中<b>几乎不成立</b>的前提——Faro/Riegl 主流设备输出 las 无 RGB，"
             "仅回波强度；只有少数彩色结构光扫描仪输出 PLY 才自带 RGB。理想架构是<b>几何（激光）与色彩"
             "（相机）双源分离</b>，照片作为独立可导入源文件。"},

    # =====================================================================
    {"type": "h1", "text": "三、缺陷剖析：仅依赖 PLY 自带 RGB 的局限"},
    {"type": "h2", "text": "3.1 采集层面：真实 TLS 地面激光扫描几乎得不到顶点 RGB"},
    {"type": "body",
     "text": "地面三维激光扫描仪主流设备（Faro、Riegl）输出 las 点云，<b>只输出几何坐标 + 回波强度 "
             "intensity，不采集可见光 RGB 色彩</b>；intensity 是激光回波信号（单通道灰度），≠ 可见光色彩，"
             "不能用于文物色彩还原与 X2-CIEDE2000 色差评估。现实业务中，绝大多数真实文物激光扫描数据集"
             "（如 WHU-TLS 学术基准）PLY 点云没有顶点 RGB——当前管线只能产出无色彩几何模型，<b>无法"
             "完成完整的带纹理文物交付成果</b>。行业标准 WW/T 0115-2023、DB11/T 1922-2021 亦明确几何与"
             "色彩为两套独立采集设备。"},
    {"type": "h2", "text": "3.2 色彩质量上限受点云采样密度约束"},
    {"type": "body",
     "text": "当前 UV-atlas 模块把点云顶点 RGB 烘焙到 UV 贴图：贴图像素色彩来源于点云顶点，<b>点云稀疏"
             "则色彩采样点少，贴图模糊、色彩细节丢失</b>。童永东路线是原始高清照片直接投影到网格生成"
             "纹理，贴图分辨率不受点云密度限制，可使用 2400 万像素原始照片，得到远高于点云采样的纹理"
             "细节。即使 M4 升级为 xatlas UV 图集烘焙，烘焙源仍是「点云颜色」——信息量上限未变。"},
    {"type": "h2", "text": "3.3 数据来源兼容性窄：公开基准数据集大部分不可做色彩测试"},
    {"type": "body",
     "text": "WHU-TLS 等学术 TLS 基准数据集全部无可见光 RGB；只有彩色结构光扫描仪输出 PLY 才自带 RGB，"
             "这类公开数据集数量极少。管线做色彩相关实机测试（X2 色差评估、纹理质量验证）<b>很难找到"
             "公开可获取的测试素材</b>，制约了色彩模块的验证与推广。"},
    {"type": "h2", "text": "3.4 与主流文保业务工作流脱节"},
    {"type": "body",
     "text": "行业标准工作流为「TLS 激光扫描（几何）+ 独立单反环绕拍摄（色彩，摆放色卡）+ Metashape "
             "投影映射生成 UV 纹理 + obj+mtl+贴图交付」。h3d 当前链路把几何与色彩绑定在 PLY 点云，"
             "<b>无法接收独立照片作为色彩源，无法复现上述主流业务流程</b>——与文保单位实际采集交付"
             "体系不兼容，影响成果对接与验收。"},
    {"type": "h2", "text": "3.5 X2 CIEDE2000 色差评估的短板"},
    {"type": "body",
     "text": "X2 模块目标为评估交付模型色彩保真度，需要<b>原始色卡照片作为真值</b>；现在只能依赖点云"
             "顶点采样颜色，点云无 RGB 时 X2 完全不可用。理想业务是直接从原始高清照片采样色卡真值，"
             "再与 UV 贴图比对——这是当前管线做不到的。同理，M5 质检缺少「纹理清晰度/贴图质量」维度，"
             "色彩质量无法量化把关。"},
    {"type": "h2", "text": "3.6 对下游环节的传导影响（总结）"},
    {"type": "table",
     "headers": ["下游环节", "受点云 RGB 限制的影响"],
     "rows": [
         ["M4 纹理（UV 烘焙）", "烘焙源信息量=点云颜色；UV-xatlas 仅改善输出格式，未解决色彩源头"],
         ["M5 质检", "「色彩质量」维度缺失，无纹理清晰度/贴图质量指标"],
         ["X2 colour 色差评估", "需原始色卡照片作真值；点云无 RGB 则 X2 不可用"],
         ["交付包（80_export）", "带纹理 OBJ/GLB 的纹理图质量取决于烘焙源，细节不足"],
         ["档案与展示", "出版级 / 展陈级资产需独立高清纹理，点云 RGB 无法支撑"],
     ]},

    # =====================================================================
    {"type": "h1", "text": "四、技术改进路线：将相机照片接入现有管线"},
    {"type": "body",
     "text": "以下 4 条路线从「直接集成现成工具」到「自研流程」「神经渲染」再到「基础设施」，覆盖不同"
             "投入与产出形态。共同前提：<b>在管线前端引入照片采集规范</b>（拍摄角度覆盖、重叠度 ≥60%、"
             "固定白平衡 + 色卡入镜、EXIF 完整），并扩展 00_raw 为「几何 + 照片」双源输入。"},

    {"type": "h2", "text": "4.1 路线 A：集成 Metashape（Headless 外部算子）/ COLMAP 纹理重建模块"},
    {"type": "body",
     "text": "<b>架构选型（依据方向文档）</b>：不把 SfM/MVS 照片重建全部逻辑重写进 h3d 内部（开发量巨大、"
             "SfM 调参极其复杂），而是采用<b>子进程调用 Metashape-Professional CLI/Headless API 作为"
             "外部算子</b>，作为<b>可选扩展 stage（默认关闭）</b>，不破坏现有 M1~M6 主链路。两种集成模式："
             "<b>模式 A（P2 优先，低侵入）— 外部纹理映射 stage（只做纹理、不做几何重建）</b>：几何沿用 "
             "h3d 现有链路（M1~M2~M3 输出泊松网格）；新增可选 stage ``photo_texture``，输入为 M3 网格 + "
             "独立照片目录 + 相机位姿（SfM 对齐结果，Metashape 导出）；h3d 子进程启动 ``metashape -r "
             "script.py`` headless 模式，导入网格与已对齐相机，执行 BuildTexture() 投影烘焙 UV 图集，"
             "导出 obj+mtl+diffuse 贴图回传 h3d，进入 M5（新增贴图质量指标）、M6 发布。<b>模式 B（P3 "
             "远期，完整 SfM）</b>：输入只有原始 JPG 照片，管线内调用 Metashape headless 完成照片对齐、"
             "稀疏/稠密点云、输出 PLY 点云，再交给 h3d 原有 M1~M6 链路继续处理。COLMAP（BSD-3）为开源"
             "替代（可作模式 A 的照片对齐与纹理化引擎；纹理化需 MVS-Texturing，注意 GPL 许可）。"},
    {"type": "flowchart",
     "nodes": [
         {"id": "ph", "label": "照片目录 00_photo/<item>/（JPG + 位姿元数据）", "shape": "parallelogram"},
         {"id": "mesh3", "label": "M1~M3 扫描几何链路（泊松网格）", "shape": "rect"},
         {"id": "sub", "label": "h3d 子进程调用 Metashape Headless（外部算子）", "shape": "rect"},
         {"id": "bt", "label": "导入网格 + 对齐相机 → BuildTexture() 投影烘焙", "shape": "rect"},
         {"id": "ret", "label": "导出 obj+mtl+diffuse 贴图回传 h3d", "shape": "parallelogram"},
         {"id": "m5", "label": "M5 质检（新增贴图质量指标）→ M6 发布", "shape": "rect"},
     ],
     "edges": [
         {"from": "ph", "to": "sub"}, {"from": "mesh3", "to": "sub"},
         {"from": "sub", "to": "bt"}, {"from": "bt", "to": "ret"},
         {"from": "ret", "to": "m5"},
     ],
     "caption": "路线 A（模式 A）：photo_texture 可选 stage——Metashape Headless 外部算子只做纹理投影，几何链路不变"},
    {"type": "table",
     "headers": ["评估项", "说明"],
     "rows": [
         ["实现原理", "子进程调用 Metashape headless：导入 h3d 网格 + 已对齐相机 → BuildTexture 投影烘焙 → 回传贴图；或 COLMAP 开源替代"],
         ["所需依赖", "Metashape-Professional（商业 license）/ COLMAP（BSD-3）+ MVS-Texturing（GPL 需评估）；GPU（纹理化可选）"],
         ["改造工作量", "中等（模式 A 约 3~6 周）：新增可选 stage + 子进程调度 + 产物校验；主链路零改动"],
         ["优点", "不改 M1~M6 主链路、PLY RGB 模式完整保留（向后兼容）；复用 Metashape 工程验证的纹理投影算法；满足童永东论文工作流"],
         ["缺点", "依赖商业软件 license（开源环境无法直接运行，pytest 只能 mock）；输入须预先完成相机对齐（SfM）；Docker 需 Linux 版授权"],
         ["适用性", "<b>P2 首推</b>：彩塑/壁画等色彩主导文物；行业标准工作流复现；与既有照片归档对接"],
     ]},
    {"type": "body",
     "text": "配置示意（新增可选 stage，默认关闭）："},
    {"type": "code", "language": "yaml",
     "text": "texture:\n"
             "  paint_mode: vertex-color   # vertex-color / uv-atlas / photo-project\n"
             "photo_texture:               # 可选扩展 stage（模式 A）\n"
             "  enabled: false             # 默认关闭\n"
             "  photo_dir: ./00_photo      # 原始照片目录（按文物 id 子目录）\n"
             "  metashape_script: ./scripts/metashape_texture.py\n"
             "  texture_size: [4096, 4096] # 贴图分辨率"},

    {"type": "h2", "text": "4.2 路线 B：开发照片-几何自动配准与 UV 映射流程（自有）"},
    {"type": "body",
     "text": "<b>实现原理</b>：不重建照片几何，而是把照片<b>配准到扫描几何上</b>：① 从扫描网格渲染"
             "深度图/法线图/灰度图，与照片做 SIFT/LOFTR 特征匹配；② 结合初始位姿（EXIF + 手动控制点）"
             "用 PnP（solvePnP）求每张照片的相机位姿；③ 逐照片对网格做可见性测试（Z-buffer）与 UV "
             "投影，多图按分辨率/角度/曝光加权混合（poisson blending 消缝），生成纹理图集。可复用"
             "本管线 M4 已实装的 xatlas UV 展开与 Open3D 导出链路，只替换「颜色来源」：从「点云最近邻」"
             "升级为「照片投影」。"},
    {"type": "flowchart",
     "nodes": [
         {"id": "ph2", "label": "照片集 + 扫描网格（M3 产物）", "shape": "parallelogram"},
         {"id": "feat", "label": "特征匹配（SIFT/LOFTR）+ PnP 位姿解算", "shape": "rect"},
         {"id": "vis", "label": "可见性测试（Z-buffer）+ 视角加权", "shape": "rect"},
         {"id": "proj", "label": "UV 投影 + 多图混合（poisson blending）", "shape": "rect"},
         {"id": "bake", "label": "纹理图集烘焙（复用 xatlas + Open3D 导出）", "shape": "rect"},
         {"id": "outc", "label": "50_tex 高保真带纹理模型", "shape": "parallelogram"},
     ],
     "edges": [
         {"from": "ph2", "to": "feat"}, {"from": "feat", "to": "vis"},
         {"from": "vis", "to": "proj"}, {"from": "proj", "to": "bake"},
         {"from": "bake", "to": "outc"},
     ],
     "caption": "路线 B：照片-几何配准（不重建照片几何，直接投影到扫描网格）"},
    {"type": "table",
     "headers": ["评估项", "说明"],
     "rows": [
         ["实现原理", "特征匹配（SIFT/LOFTR）+ PnP 求相机位姿 → Z-buffer 可见性 → 多图加权投影混合"],
         ["所需依赖", "OpenCV（Apache-2.0）、scipy、NumPy；可选 LOFTR（深度学习匹配，轻量模型）"],
         ["改造工作量", "较高：配准管线 + 可见性 + 混合全自研（约 8~12 周，可复用 M4 UV/导出）"],
         ["优点", "深度定制、无商业授权；几何精度完全继承扫描（不引入照片几何误差）；可精细控制接缝/权重"],
         ["缺点", "开发量大；配准失败（低纹理表面）需人工干预；多图混合质量依赖工程打磨"],
         ["适用性", "实验室/研究所自研；需要与现有几何管线深度融合；照片数量可控（数十~数百张）"],
     ]},

    {"type": "h2", "text": "4.3 路线 C：引入 3DGS / NeRF 神经渲染"},
    {"type": "body",
     "text": "<b>实现原理</b>：3D 高斯泼溅（3DGS）把场景表示为数万个各向异性高斯分布，从多视角照片"
             "训练优化（位置/协方差/颜色/不透明度），实现实时高质量新视角渲染；NeRF 用 MLP 隐式表示"
             "辐射场。二者输入同为照片集，输出是「可渲染的神经场景」而非传统网格。可作为<b>展示层</b>"
             "（数字展陈、虚拟漫游、VR）与<b>检验层</b>（照片-几何一致性校验）接入，或经 Marching "
             "Cubes / 高斯转网格流程回传统资产。依赖 gsplat（MIT）/ nerfstudio（Apache-2.0）；注意 "
             "Instant-NGP 的 NVIDIA 非商用许可限制。"},
    {"type": "flowchart",
     "nodes": [
         {"id": "ph3", "label": "照片集（密集视角）", "shape": "parallelogram"},
         {"id": "train", "label": "3DGS 训练（高斯参数优化）或 NeRF（MLP 辐射场）", "shape": "rect"},
         {"id": "view", "label": "新视角渲染（实时高质量）", "shape": "rect"},
         {"id": "demo", "label": "数字展陈 / VR 漫游资产", "shape": "parallelogram"},
         {"id": "conv", "label": "（可选）高斯转网格 / 深度导出回传统资产", "shape": "rect"},
     ],
     "edges": [
         {"from": "ph3", "to": "train"}, {"from": "train", "to": "view"},
         {"from": "view", "to": "demo"}, {"from": "view", "to": "conv"},
     ],
     "caption": "路线 C：神经渲染（3DGS/NeRF）作为展示层与检验层"},
    {"type": "table",
     "headers": ["评估项", "说明"],
     "rows": [
         ["实现原理", "照片集 → 优化高斯/辐射场参数 → 任意视角渲染；可转网格回传统管线"],
         ["所需依赖", "gsplat（MIT）/ nerfstudio（Apache-2.0）+ GPU（≥8GB）；Instant-NGP 需评估许可"],
         ["改造工作量", "中高：训练调度 + 资产转换 + 与管线对接（约 6~10 周）"],
         ["优点", "照片级渲染质量、实时交互、支持任意视角；天然多视角一致性；学术前沿"],
         ["缺点", "输出非传统网格/纹理（需转换）；训练耗时与显存要求高；与 M5 质检/交付格式衔接复杂"],
         ["适用性", "数字展陈、VR/AR、虚拟修复预览；照片充分且几何可重建的场景；研究与展示并重"],
     ]},

    {"type": "h2", "text": "4.4 路线 D：照片导入与纹理贴图生成的标准数据格式中间层"},
    {"type": "body",
     "text": "<b>实现原理</b>：定义「照片 + 位姿 + 相机参数 + 纹理」的统一中间格式（可复用 COLMAP "
             "format：cameras.txt / images.txt / sparse 目录；或自研 JSON 清单：EXIF + 外方位元素 + "
             "色卡/白平衡元数据），作为<b>所有上游照片源的唯一入口</b>：无论照片来自 Metashape、COLMAP、"
             "自研配准还是第三方交付，都转换为该标准格式；管线新增「导入器」把中间格式翻译成 M4 可"
             "消费的纹理资产（带纹理网格或照片对齐点云）。该路线是基础设施，与 A/B/C 均可组合。"},
    {"type": "flowchart",
     "nodes": [
         {"id": "src", "label": "任意照片源（Metashape / COLMAP / 自研 / 第三方）", "shape": "parallelogram"},
         {"id": "std", "label": "标准中间格式（cameras + images + 位姿 + 元数据）", "shape": "rect"},
         {"id": "imp", "label": "导入器：校验 + 转换为管线纹理资产", "shape": "rect"},
         {"id": "m4c", "label": "M4 纹理 / 交付包（统一消费）", "shape": "parallelogram"},
     ],
     "edges": [
         {"from": "src", "to": "std"}, {"from": "std", "to": "imp"},
         {"from": "imp", "to": "m4c"},
     ],
     "caption": "路线 D：标准数据格式中间层——统一照片源入口，与 A/B/C 可组合"},
    {"type": "table",
     "headers": ["评估项", "说明"],
     "rows": [
         ["实现原理", "定义照片集 + 相机参数 + 位姿 + 色彩元数据的标准格式，转换器统一接入管线"],
         ["所需依赖", "YAML/JSON（现有依赖）、ExifRead（照片 EXIF）、NumPy"],
         ["改造工作量", "低-中：格式定义 + 转换器 + 校验器（约 2~4 周）；是其它路线的前置地基"],
         ["优点", "解耦上游工具；可追溯（位姿/参数归档）；保证「照片 + 位姿」可复算；便于合规审计"],
         ["缺点", "本身不产生纹理（需配合 A/B/C）；标准制定需兼顾各方工具兼容"],
         ["适用性", "<b>必建地基</b>：任何将照片纳入管线的方案都应先落地；对接行业归档规范（照片+位姿长期保存）"],
     ]},

    {"type": "h2", "text": "4.5 四条路线对比总结表"},
    {"type": "table",
     "headers": ["路线", "核心依赖", "改造量", "优点", "缺点", "适用性"],
     "rows": [
         ["A 集成 Metashape Headless / COLMAP", "Metashape 商业 API 或 COLMAP(BSD-3)", "中（3~6 周模式 A）",
          "不改主链路、复用成熟算法、向后兼容", "商业授权成本 / 输入需预对齐 / pytest 只能 mock", "<b>P2 首推</b>，行业标准工作流"],
         ["B 照片-几何配准+UV 映射", "OpenCV(Apache-2.0)", "高（8~12 周）",
          "深度定制、继承扫描几何精度、可控接缝", "开发量大、低纹理表面需干预", "自研深度集成，实验室/研究所"],
         ["C 3DGS/NeRF 神经渲染", "gsplat(MIT)/nerfstudio(Apache-2.0)+GPU", "中高（6~10 周）",
          "照片级渲染、实时交互、前沿", "非传统网格交付、训练成本高", "数字展陈/VR/虚拟修复预览"],
         ["D 标准数据格式中间层", "YAML/JSON + ExifRead", "低-中（2~4 周）",
          "解耦工具、可追溯、可复算", "本身不产生纹理（需配合 A/B/C）", "<b>必建地基</b>，与任何路线组合"],
     ]},
    {"type": "h2", "text": "4.6 数据目录规范扩展（适配照片输入）"},
    {"type": "body",
     "text": "沿用现有 init 命令生成的项目目录，新增 <b>00_photo</b> 文件夹存放原始高清照片，每个文物 id "
             "独立子目录，附相机位姿元数据（SfM 对齐结果）："},
    {"type": "code", "language": "text",
     "text": "D:\\test_project\\\n"
             "├─ 00_raw\\              # PLY 点云输入（现有）\n"
             "├─ 00_photo\\            # 新增：原始高清照片（每个文物 id 独立子目录）\n"
             "│   └─ rock001\\\n"
             "│       ├─ img_001.jpg\n"
             "│       ├─ img_002.jpg\n"
             "│       └─ capture_meta.json   # 相机位姿元数据（Metashape 导出）\n"
             "├─ 10_clean\\            # ……后续目录不变\n"
             "└─ 90_meta\\             # manifest / 交付清单"},
    {"type": "callout",
     "text": "<b>推荐组合</b>：以 <b>D（中间层）为地基</b>（00_photo 目录 + 位姿元数据规范），先落地 "
             "<b>A 模式 A（photo_texture 可选 stage，Metashape Headless 外部算子）</b>打通「照片→高保真"
             "纹理」主链路且不改 M1~M6；中期以 <b>B</b> 替换纹理化环节（几何完全继承扫描）；远期以 "
             "<b>C</b> 拓展展示层，并以 <b>A 模式 B</b> 支持「纯照片」几何输入。各路线共用同一照片采集"
             "规范与中间格式，改造可逐步叠加、互不冲突。"},

    # =====================================================================
    {"type": "h1", "text": "五、落地建议"},
    {"type": "h2", "text": "5.1 分阶段实施路线（依据方向文档优先级）"},
    {"type": "table",
     "headers": ["阶段", "周期", "目标", "交付物", "对应路线"],
     "rows": [
         ["v0.1 基线（当前）", "已完成", "现有 UV-atlas 模块（色彩仍来自 PLY 顶点 RGB）；文档写明业务局限；实机测试用带 RGB 的 PLY 验证色彩模块", "现有 M1~M6 + UV-atlas；本分析文档", "—"],
         ["P2 迭代（近期）", "1~2 月", "打通「照片 → 纹理」最小闭环且不改主链路", "photo_texture 可选 stage（模式 A：Metashape Headless 只做纹理投影）+ 00_photo 目录 + capture_meta.json 规范 + 实机用例 T-16/T-17", "D + A(模式 A)"],
         ["P3 远期", "2~4 月", "完整 SfM 照片输入 / 自研配准 / 神经渲染", "模式 B（照片→PLY 接入 M1）；可选 B 自研照片-几何配准替换纹理化；可选 C 3DGS 展示层", "A(模式 B) + B + C"],
     ]},
    {"type": "h2", "text": "5.2 技术选型建议"},
    {"type": "table",
     "headers": ["决策点", "建议", "理由"],
     "rows": [
         ["纹理投影引擎", "Metashape-Professional（行业标准，BuildTexture 成熟）；COLMAP 作开源替代",
          "复用工程验证算法、不手写多视图纹理融合；license 成本由业务承担"],
         ["集成方式", "子进程调用 Metashape Headless（外部算子，可选 stage）",
          "不重写 SfM 进 h3d（开发量巨大、调参复杂）；不破坏 M1~M6 主链路"],
         ["纹理化自研（可选）", "阶段后期用 OpenCV + poisson blending 替代 MVS-Texturing（GPL）",
          "规避 GPL 传染；自研可精确控制接缝与权重"],
         ["神经渲染", "gsplat / nerfstudio（宽松许可）；不用 Instant-NGP（非商用限制）",
          "许可证合规（与项目全 MIT/BSD 白名单一致）"],
         ["中间格式", "COLMAP format 为主 + capture_meta.json 扩展（色卡/白平衡/来源元数据）",
          "业界互通 + 可扩展；照片+位姿长期归档；管线只消费标准格式"],
         ["几何来源", "几何仍以扫描为准（模式 A 原则），照片只做纹理",
          "发挥激光扫描亚毫米级几何精度；照片几何（模式 B）仅作远期补充"],
     ]},
    {"type": "h2", "text": "5.3 风险控制要点"},
    {"type": "table",
     "headers": ["风险", "表现", "控制措施"],
     "rows": [
         ["商业授权依赖", "photo_texture stage 无 license 无法运行",
          "stage 默认关闭 + pytest mock 模拟；生产环境预装 Metashape-Professional；Docker 用 Linux 版 + 授权挂载"],
         ["照片相机对齐质量", "SfM 对齐失败 → 纹理错位、漂移",
          "输入须预先完成对齐（Metashape 导出位姿）；拍摄规范（重叠 ≥60%、定焦、包围曝光、色卡入镜）"],
         ["几何-照片坐标系配准误差", "纹理重影、接缝错位",
          "控制点标定（编码靶标/特征点）；配准后 ICP 精化；可见性 Z-buffer 校验"],
         ["多照片色彩不一致", "接缝明显、曝光不均、色偏",
          "统一白平衡 + 色卡入镜（X2 校准）；曝光融合与 poisson blending；色彩一致性质检（新增 M5 贴图质量指标）"],
         ["测试素材匮乏", "公开 TLS 基准数据集（WHU-TLS 等）无 RGB",
          "v0.1 实机测试用带 RGB 的彩色结构光 PLY；P2 起用自采照片集；建立内部照片+位姿归档库"],
         ["许可证合规", "MVS-Texturing(GPL)、Instant-NGP(非商用) 污染",
          "选型规避（5.2）；第三方组件引入前合规审查"],
         ["文物拍摄合规", "拍摄权限 / 数据安全",
          "按项目保密要求归档；拍摄授权与版权声明入 capture_meta.json；X2 色彩管理流程衔接"],
     ]},

    # =====================================================================
    {"type": "h1", "text": "六、总结与展望"},
    {"type": "body",
     "text": "当前管线「PLY 自带 RGB」在真实 TLS 文物扫描场景中<b>几乎不成立</b>：主流地面激光扫描仪（Faro/"
             "Riegl）输出 las 仅含几何 + 回波强度，公开基准数据集（WHU-TLS 等）亦无可见光 RGB；行业标准 "
             "WW/T 0115-2023、DB11/T 1922-2021 明确几何（激光）与色彩（相机）为两套独立采集设备。与"
             "童永东博士论文「相机拍摄 + Metashape 纹理映射」路线相比，当前管线存在<b>数据来源兼容性窄、"
             "纹理清晰度受点云密度限制、X2 色差评估缺真值、与主流文保业务流脱节</b>等现实缺陷；UV-xatlas "
             "模块仅改善输出格式，未解决色彩源头问题。本文给出 4 条可行路线——Metashape Headless 外部"
             "算子集成（A，含模式 A 纹理投影 / 模式 B 完整 SfM）、自研照片-几何配准与 UV 映射（B）、"
             "3DGS/NeRF 神经渲染（C）、标准数据格式中间层（D）——并推荐「<b>v0.1 基线保留 + P2 落地 "
             "photo_texture 可选 stage（D 地基 + A 模式 A）+ P3 拓展（A 模式 B / B / C）</b>」的分阶段路径。"},
    {"type": "callout",
     "text": "<b>展望</b>：照片接入后，本管线将形成「<b>激光扫描几何 + 相机照片纹理</b>」双源架构：几何精度"
             "继承激光扫描（亚毫米级），色彩保真继承 2400 万像素原始照片，X2 colour 的 CIEDE2000 色差评估"
             "直接以照片色卡为真值、全面生效，M5 质检增加贴图质量/色彩一致性维度，交付包同时含高保真纹理"
             "资产与可复算的照片归档（00_photo + capture_meta.json）——与童永东博士论文路线在色彩能力上"
             "对齐，并在几何精度、自动化编排与开源可控性上形成自有优势，支撑彩塑造像数字化、修复比对、"
             "数字展陈与行业标准验收等深化应用。"},

    # =====================================================================
    {"type": "h1", "text": "附录 A：参考技术栈与许可速查"},
    {"type": "table",
     "headers": ["组件", "用途", "许可", "与本管线现有依赖关系"],
     "rows": [
         ["Agisoft Metashape", "照片重建（SfM+MVS+纹理），Python API", "商业（按版本授权）", "无；需评估采购"],
         ["COLMAP", "开源 SfM/MVS（位姿 + 稠密点云）", "BSD-3-Clause", "无；可新增"],
         ["MVS-Texturing", "COLMAP 网格纹理化", "GPL-3.0（规避）", "不建议引入（GPL 传染）"],
         ["OpenCV", "SIFT/LOFTR 特征、solvePnP、混合", "Apache-2.0", "无；可新增（路线 B）"],
         ["gsplat / nerfstudio", "3DGS / NeRF 训练渲染", "MIT / Apache-2.0", "无；可新增（路线 C）"],
         ["ExifRead", "照片 EXIF（焦距/曝光/白平衡）", "MIT", "无；可新增（路线 D）"],
         ["scipy / NumPy / Open3D", "最近邻 / 矩阵 / 网格读写（现有）", "BSD-3 / BSD-3 / MIT", "<b>已在本管线</b>"],
     ]},
]

with open("content.json", "w", encoding="utf-8") as f:
    json.dump(content, f, ensure_ascii=False, indent=2)
print("content.json 生成完成，块数 =", len(content))
