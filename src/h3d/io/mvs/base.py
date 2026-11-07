"""MVS 后端的契约（P0-1）。

为什么要有这一层
----------------
稠密重建原先写死在 ``sfm_solve.run_dense_stereo`` 里：三条 COLMAP 命令就地拼接，
路径写死。后果是「换后端」必须改业务层——而位姿已经是 S1 的产物，稠密重建本可以
按契约切开。论文（刘建祥等，2026）把 ``(K, R, t)`` 当作输入这一事实，反过来证明了
这个切分是成立的。

**已知边界（写进 ADR-0028）**：本契约只覆盖「外部命令式」后端——后端把工作表达为
一串有序命令 + 一个产物路径 + 一个进度探针。纯 Python 推理后端（如法向引导 MVS）
要接进来，需要给 :class:`MvsStep` 增加「进程内调用」形态；那是有明确扩展点的改动，
不是改契约语义。**不假装现在就能支持它**——那样只会让两边都不好用。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

__all__ = ["MvsRequest", "MvsStep", "MvsPlan", "MvsBackend"]


@dataclass(frozen=True)
class MvsRequest:
    """一次稠密重建的请求。

    ``options`` 只放**后端无关的语义参数**（最少/最多像素、内部分辨率上限、
    是否做几何一致性）。各后端自己解释；不认识的键必须忽略而不是报错，
    否则每加一个后端就要改业务层。

    ``exe`` 是后端可执行文件路径。命令式后端用它拼 ``argv[0]``；
    非命令式后端留空即可。
    """

    photo_dir: str
    sparse_model: str
    workspace: str
    exe: str = ""
    options: Mapping[str, Any] = field(default_factory=dict)

    def opt(self, key: str, default: Any = None) -> Any:
        return self.options.get(key, default)


@dataclass(frozen=True)
class MvsStep:
    """一条要执行的外部命令。

    ``heavy`` 标记「耗时段」：它拿到完整超时预算，其余步骤只拿四分之一。
    这个划分必须由后端声明而不是业务层按序号猜——序号一改就错配。
    """

    argv: tuple[str, ...]
    label: str
    heavy: bool = False


@dataclass(frozen=True)
class MvsPlan:
    """后端给出的完整工作计划。业务层只负责执行它，不再拼命令。"""

    dense_dir: str
    steps: tuple[MvsStep, ...]
    fused_path: str
    progress: Callable[[], str]

    def budgets(self, total_s: int) -> list[int]:
        """按 ``heavy`` 把总预算分给各步（耗时段全拿，其余拿 1/4，下限 600s）。"""
        return [int(total_s) if s.heavy else max(600, int(total_s) // 4)
                for s in self.steps]


class MvsBackend(ABC):
    """稠密重建后端。新增后端只需继承它并实现 :meth:`plan`。"""

    #: 写进资产元数据的后端标识（P0-3 用它做跨期「后端版本冻结」的判据）
    backend_id: str = "unknown"

    @property
    def backend_version(self) -> str:
        """后端版本。拿不到时返回 ``"unknown"``，**不抛**——它不该阻断重建。"""
        return "unknown"

    @abstractmethod
    def plan(self, req: MvsRequest) -> MvsPlan:
        """把请求翻译成有序步骤。纯函数：不建目录、不跑命令、不读大文件。"""
        raise NotImplementedError
