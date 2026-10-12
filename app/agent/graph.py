"""LangGraph 工作流：改写 -> 规划 -> SQL -> (Python?) -> (图表?) -> 结论"""
import json
import re
from typing import Literal

from langgraph.graph import END, START, StateGraph

from app.agent.prompts import (
    CONCLUSION_PROMPT,
    ERROR_CONCLUSION_PROMPT,
    PLANNER_PROMPT,
    REWRITE_PROMPT,
)
from app.agent.state import AgentState, add_step, new_state
from app.llm import get_llm
from app.tools.chart_tool import build_chart
from app.tools.python_tool import analyze as analyze_python
from app.tools.sql_tool import text_to_sql


# ---------- 工具函数 ----------

def _parse_json(text: str) -> dict:
    """从 LLM 输出中提取 JSON"""
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"无法解析 JSON: {text[:120]}")
    return json.loads(match.group(0))

def _stringify(value) -> str:
    """安全地把任意值转成字符串，避免 DataFrame 触发 bool 判断"""
    if value is None:
        return "无"
    return str(value)

def _format_history(history: list[dict]) -> str:
    """把历史问答格式化成文本，喂给改写节点"""
    if not history:
        return "（无历史对话）"
    lines = []
    for i, turn in enumerate(history[-5:], 1):
        lines.append(f"第{i}轮 - 问：{turn.get('question', '')}")
        ans = turn.get("answer", "")
        if ans:
            preview = ans[:100] + ("..." if len(ans) > 100 else "")
            lines.append(f"      答：{preview}")
    return "\n".join(lines)


# ---------- 节点 ----------

def rewrite_node(state: AgentState) -> AgentState:
    """结合历史，把用户的追问改写成完整独立的问题"""
    history = state.get("history") or []
    question = state["question"]

    # 没有历史就直接使用原问题
    if not history:
        state["rewritten_question"] = question
        add_step(state, "问题改写: 无历史，原样使用")
        return state

    llm = get_llm()
    prompt = REWRITE_PROMPT.format(
        history=_format_history(history),
        question=question,
    )
    resp = llm.invoke(prompt)
    rewritten = resp.content.strip().strip('"').strip("'")

    # 保险：LLM 输出了异常内容时回退
    if not rewritten or "\n" in rewritten or len(rewritten) > 200:
        rewritten = question

    state["rewritten_question"] = rewritten
    add_step(state, f"问题改写: {question}  →  {rewritten}")
    return state


def planner_node(state: AgentState) -> AgentState:
    llm = get_llm()
    question = state.get("rewritten_question") or state["question"]

    resp = llm.invoke(PLANNER_PROMPT.format(question=question))
    try:
        plan = _parse_json(resp.content)
    except Exception as e:
        plan = {
            "need_sql": True,
            "need_python": False,
            "need_chart": False,
            "reason": f"解析失败: {e}",
        }

    state["need_chart"] = bool(plan.get("need_chart", False))

    plan_list = []
    if plan.get("need_sql", True):
        plan_list.append("sql")
    if plan.get("need_python"):
        plan_list.append("python")
    if plan.get("need_chart"):
        plan_list.append("chart")
    plan_list.append("summary")
    state["plan"] = plan_list

    add_step(state, f"规划: {plan.get('reason', '')}")
    return state


def sql_node(state: AgentState) -> AgentState:
    question = state.get("rewritten_question") or state["question"]
    ds_id = state.get("datasource_id") or None
    result = text_to_sql(question, datasource_id=ds_id)

    state["sql"] = result.sql
    state["raw_sql"] = result.raw_sql
    state["data"] = result.data
    state["sql_error"] = result.error

    if result.ok:
        add_step(state, f"SQL 成功，返回 {len(result.data)} 行")
    else:
        add_step(state, f"SQL 失败: {result.error}")
    return state


def python_node(state: AgentState) -> AgentState:
    if state.get("sql_error"):
        add_step(state, "跳过 Python 分析（SQL 失败）")
        return state
    question = state.get("rewritten_question") or state["question"]
    result = analyze_python(question, state["data"])

    state["python_code"] = result.code
    state["python_result"] = result.result
    state["python_error"] = result.error

    if result.ok:
        add_step(state, "Python 分析完成")
    else:
        add_step(state, f"Python 失败: {result.error}")
    return state


def chart_node(state: AgentState) -> AgentState:
    if state.get("sql_error"):
        add_step(state, "跳过图表（SQL 失败）")
        return state
    question = state.get("rewritten_question") or state["question"]
    result = build_chart(question, state["data"])

    state["chart_type"] = result.chart_type
    state["chart_path"] = result.file_path
    state["chart_error"] = result.error

    if result.ok:
        add_step(state, f"图表生成: {result.chart_type}")
    else:
        add_step(state, f"图表失败: {result.error}")
    return state


def conclusion_node(state: AgentState) -> AgentState:
    llm = get_llm()
    question = state.get("rewritten_question") or state["question"]

    # 检查是否有任一阶段失败
    error_stage, error_msg = "", ""
    if state.get("sql_error"):
        error_stage, error_msg = "SQL 查询", state["sql_error"]
    elif state.get("python_error"):
        error_stage, error_msg = "Python 分析", state["python_error"]
    elif state.get("chart_error"):
        error_stage, error_msg = "图表生成", state["chart_error"]

    if error_stage:
        prompt = ERROR_CONCLUSION_PROMPT.format(
            question=question,
            stage=error_stage,
            error=error_msg,
        )
    else:
        data = state.get("data") or []
        data_count = len(data)

        # 数据量小时全部给，大时才截断
        MAX_PREVIEW = 30
        if data_count <= MAX_PREVIEW:
            data_preview = data
            preview_note = f"共 {data_count} 行，已全部展示"
        else:
            data_preview = data[:MAX_PREVIEW]
            preview_note = f"共 {data_count} 行，以下仅展示前 {MAX_PREVIEW} 行"

        chart_path = state.get("chart_path") or ""
        chart_info = (
            f"已生成，文件路径：{chart_path}" if chart_path else "本次未生成图表"
        )

        prompt = CONCLUSION_PROMPT.format(
            question=question,
            sql=state.get("sql", ""),
            preview_note=preview_note,
            data_preview=str(data_preview),
            python_result=_stringify(state.get("python_result")),
            chart_info=chart_info,
        )

    resp = llm.invoke(prompt)
    state["conclusion"] = resp.content.strip()
    add_step(state, "生成分析结论")
    return state


# ---------- 路由 ----------

def after_sql(state: AgentState) -> Literal["python", "chart", "conclusion"]:
    if state.get("sql_error"):
        return "conclusion"
    if "python" in state.get("plan", []):
        return "python"
    if state.get("need_chart"):
        return "chart"
    return "conclusion"


def after_python(state: AgentState) -> Literal["chart", "conclusion"]:
    if state.get("need_chart") and not state.get("python_error"):
        return "chart"
    return "conclusion"


# ---------- 构图 ----------

def build_graph():
    g = StateGraph(AgentState)

    g.add_node("rewrite", rewrite_node)
    g.add_node("planner", planner_node)
    g.add_node("sql", sql_node)
    g.add_node("python", python_node)
    g.add_node("chart", chart_node)
    g.add_node("conclusion", conclusion_node)

    g.add_edge(START, "rewrite")
    g.add_edge("rewrite", "planner")
    g.add_edge("planner", "sql")

    g.add_conditional_edges(
        "sql",
        after_sql,
        {"python": "python", "chart": "chart", "conclusion": "conclusion"},
    )
    g.add_conditional_edges(
        "python",
        after_python,
        {"chart": "chart", "conclusion": "conclusion"},
    )

    g.add_edge("chart", "conclusion")
    g.add_edge("conclusion", END)

    return g.compile()


_graph = None


def get_graph():
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph


# ---------- 对外入口 ----------

def run(
    question: str,
    session_id: str = "",
    datasource_id: str = "",
    verbose: bool = True,
) -> AgentState:
    from app.agent.memory import get_store

    store = get_store()
    history = store.get(session_id) if session_id else []

    state = new_state(
        question,
        session_id=session_id,
        datasource_id=datasource_id,
        history=history,
    )
    final = get_graph().invoke(state)

    if session_id and final.get("conclusion"):
        store.append(
            session_id,
            question=final.get("rewritten_question") or question,
            answer=final["conclusion"],
        )

    if verbose:
        _print_result(final)
    return final


def _print_result(state: AgentState) -> None:
    print("=" * 64)
    print(f"[问题] {state['question']}")

    rewritten = state.get("rewritten_question")
    if rewritten and rewritten != state["question"]:
        print(f"[改写] {rewritten}")
    print()

    print("[执行步骤]")
    for i, s in enumerate(state.get("steps", []), 1):
        print(f"  {i}. {s}")
    print()

    if state.get("sql"):
        print(f"[SQL] {state['sql'][:200]}")
    if state.get("python_result") is not None:
        print(f"[Python 结果] {_stringify(state['python_result'])}")
    if state.get("chart_path"):
        print(f"[图表] {state['chart_path']}")
    print()

    print("[结论]")
    print(state.get("conclusion", ""))
    print()