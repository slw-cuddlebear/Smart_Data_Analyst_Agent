"""Agent 状态定义：贯穿整个工作流的数据载体"""
from dataclasses import dataclass, field
from typing import Any

from typing_extensions import TypedDict

class AgentState(TypedDict, total=False):
    """LangGraph 状态：每个节点读取并写入这个字典"""
    # 输入
    question: str
    session_id: str                 # 会话 ID
    datasource_id: str
    history: list[dict]             # 历史问答
    rewritten_question: str

    # 规划阶段
    plan: list[str]              # 例如 ["sql", "python", "chart", "summary"]
    need_chart: bool             # 是否需要画图

    # SQL 阶段
    sql: str                     # 实际执行的 SQL（可能被补上 LIMIT 兜底）
    raw_sql: str                 # LLM 原始生成的 SQL（未经包装，用于展示）
    data: list[dict[str, Any]]   # SQL 查询结果
    sql_error: str

    # Python 分析阶段
    python_code: str
    python_result: Any           # 可能是数字、字符串、DataFrame
    python_error: str

    # 图表阶段
    chart_type: str
    chart_path: str
    chart_error: str

    # 结论
    conclusion: str

    # 全程日志
    steps: list[str]             # 每一步做了什么，用于展示和调试


@dataclass
class StepLog:
    """结构化日志（可选，用于更细致的追踪）"""
    node: str
    status: str                  # "ok" | "error"
    detail: str = ""
    duration_ms: int = 0
    extra: dict[str, Any] = field(default_factory=dict)


def new_state(
    question: str,
    session_id: str = "",
    datasource_id: str = "",
    history: list[dict] | None = None,
) -> AgentState:
    return AgentState(
        question=question,
        session_id=session_id,
        datasource_id=datasource_id,
        history=history or [],
        rewritten_question="",
        plan=[],
        need_chart=False,
        sql="",
        raw_sql="",
        data=[],
        sql_error="",
        python_code="",
        python_result=None,
        python_error="",
        chart_type="",
        chart_path="",
        chart_error="",
        conclusion="",
        steps=[],
    )


def add_step(state: AgentState, message: str) -> None:
    """向 steps 追加一条日志（原地修改）"""
    state.setdefault("steps", []).append(message)