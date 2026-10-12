"""Python 代码执行工具：让 LLM 生成 pandas 代码分析 DataFrame"""
import re
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from langchain_core.messages import HumanMessage, SystemMessage

from app.llm import get_llm

MAX_RETRY = 3


# ---------- 1. 受限执行环境 ----------

# 只允许这些内置函数
_SAFE_BUILTINS = {
    "abs": abs, "min": min, "max": max, "sum": sum, "len": len,
    "round": round, "sorted": sorted, "enumerate": enumerate,
    "zip": zip, "range": range, "list": list, "dict": dict,
    "tuple": tuple, "set": set, "str": str, "int": int,
    "float": float, "bool": bool, "print": print,
    "isinstance": isinstance, "type": type,
    "True": True, "False": False, "None": None,
}

_FORBIDDEN = re.compile(
    r"\b(import|__import__|open|exec|eval|compile|globals|locals|"
    r"getattr|setattr|delattr|input|exit|quit|help|"
    r"subprocess|os\.|sys\.|shutil|socket|requests)\b",
    re.IGNORECASE,
)


@dataclass
class PythonResult:
    """代码执行结果"""
    question: str
    code: str = ""
    result: Any = None
    error: str = ""
    attempts: int = 0

    @property
    def ok(self) -> bool:
        return bool(self.code) and not self.error


def is_code_safe(code: str) -> tuple[bool, str]:
    """静态检查：禁止 import 和危险函数"""
    if _FORBIDDEN.search(code):
        return False, "代码中包含被禁止的关键字（import / open / exec 等）"
    return True, ""


def run_code(code: str, df: pd.DataFrame) -> tuple[Any, str]:
    """在受限命名空间中执行代码，返回 (result, error)"""
    # 预处理：去掉 markdown 代码块
    fence = re.search(r"```(?:python)?\s*(.*?)```", code, re.DOTALL | re.IGNORECASE)
    if fence:
        code = fence.group(1)

    namespace = {
        "pd": pd,
        "np": np,
        "df": df.copy(),
        "__builtins__": _SAFE_BUILTINS,
    }

    try:
        exec(code, namespace)
    except Exception as e:
        return None, f"{type(e).__name__}: {e}"

    if "result" not in namespace:
        return None, "代码未定义 result 变量，请把最终结果赋值给 result"

    return namespace["result"], ""


# ---------- 2. 提示词 ----------

from app.agent.prompts import PYTHON_SYSTEM_PROMPT as SYSTEM_PROMPT


# ---------- 3. 主流程 ----------

def analyze(
    question: str,
    data: list[dict[str, Any]],
    max_retry: int = MAX_RETRY,
) -> PythonResult:
    """接收 SQL 结果（list[dict]），让 LLM 生成 pandas 代码分析"""
    result = PythonResult(question=question)

    if not data:
        result.error = "输入数据为空"
        return result

    df = pd.DataFrame(data)
    llm = get_llm()

    system_msg = SystemMessage(content=SYSTEM_PROMPT.format(
        columns=list(df.columns),
        dtypes={c: str(t) for c, t in df.dtypes.items()},
        preview=df.head(3).to_string(),
    ))
    messages = [system_msg, HumanMessage(content=question)]

    for attempt in range(1, max_retry + 1):
        result.attempts = attempt
        resp = llm.invoke(messages)
        code = resp.content.strip()
        result.code = code

        safe, reason = is_code_safe(code)
        if not safe:
            result.error = f"代码未通过安全校验: {reason}"
            return result

        value, err = run_code(code, df)
        if not err:
            result.result = value
            result.error = ""
            return result

        # 把错误反馈给 LLM 让它重写
        result.error = err
        messages.append(resp)
        messages.append(HumanMessage(
            content=f"执行报错：{err}\n请修正代码后重新输出，只输出 Python 代码。"
        ))

    return result


# ---------- 4. 便捷入口 ----------

def ask(question: str, data: list[dict[str, Any]], verbose: bool = True) -> PythonResult:
    result = analyze(question, data)
    if verbose:
        print(f"[问] {question}")
        print(f"[代码]\n{result.code}")
        if result.error:
            print(f"[错误] {result.error}")
        else:
            print(f"[结果] {result.result}")
        print()
    return result