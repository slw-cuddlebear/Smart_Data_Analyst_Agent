"""图表生成工具：让 LLM 根据数据自动选图表类型并用 matplotlib 出图

LLM 生成的代码属于不可信输入，执行前经两道校验（与 python_tool 同级）：
1. 静态黑名单：禁止 import / 文件读写 / 网络 / 反射相关调用
2. 受限命名空间：不注入完整 __builtins__，并把 pandas / numpy 的 IO 入口换成报错桩
另外加执行超时，避免死循环把服务线程挂死。
"""
import re
import threading
import types
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")  # 无窗口后端，必须放在 pyplot 之前
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from langchain_core.messages import HumanMessage, SystemMessage

from app.config import settings
from app.llm import get_llm
from app.tools.text_utils import strip_comments

# 中文字体（Windows 常见）
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

MAX_RETRY = 3
EXEC_TIMEOUT = 60  # 单次 exec 最长秒数


# ---------- 1. 受限执行环境 ----------

# 只允许这些内置函数（画图不需要 open / eval / __import__ 之类）
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
    r"getattr|setattr|delattr|input|exit|quit|help|breakpoint|"
    r"subprocess|os\.|sys\.|shutil|socket|requests|urllib|"
    r"to_csv|to_excel|to_pickle|to_parquet|to_sql|to_feather|to_hdf|"
    r"read_csv|read_excel|read_pickle|read_parquet|read_sql|read_json|"
    r"read_table|read_html|read_clipboard|__)\b",
    re.IGNORECASE,
)

# pandas / numpy 上被封掉的 IO 入口（plt.savefig 必须保留，出图就靠它）
_PANDAS_BLOCKED = (
    "read_csv", "read_excel", "read_pickle", "read_parquet", "read_json",
    "read_sql", "read_sql_query", "read_table", "read_feather", "read_hdf",
    "read_clipboard", "read_html", "read_xml", "read_orc", "read_sas",
    "read_spss", "read_stata", "read_fwf", "read_gbq",
)
_NUMPY_BLOCKED = (
    "load", "loadtxt", "genfromtxt", "save", "savez", "savez_compressed",
    "savetxt", "fromfile", "memmap", "fromregex",
)


def is_code_safe(code: str) -> tuple[bool, str]:
    """静态检查：禁止 import、文件读写、网络与反射相关调用"""
    if _FORBIDDEN.search(code):
        return False, "代码中包含被禁止的关键字（import / 文件读写 / 网络 / 反射 等）"
    return True, ""


def _blocked(name: str):
    """构造一个调用即报错的桩函数，用于封掉 IO 入口"""
    def _raise(*_args, **_kwargs):
        raise PermissionError(f"沙箱禁止调用 {name}")
    return _raise


def _safe_module(mod: types.ModuleType, blocked_names: tuple[str, ...]) -> types.ModuleType:
    """浅拷贝模块并替换危险入口，避免污染全局模块对象

    注意：用 types.ModuleType 会丢掉原模块的 __path__ / __file__ 等属性，
    但 LLM 代码只需要 pd.DataFrame / np 这些顶层名字，够用。
    """
    proxy = types.ModuleType(getattr(mod, "__name__", "sandbox_mod"))
    proxy.__dict__.update(mod.__dict__)
    for n in blocked_names:
        if hasattr(proxy, n):
            setattr(proxy, n, _blocked(f"{proxy.__name__}.{n}"))
    # DataFrame.to_csv 这类实例方法无法用模块代理拦住，靠 _FORBIDDEN 的正则兜
    return proxy


def _run_with_timeout(code: str, namespace: dict, timeout: int = EXEC_TIMEOUT) -> str:
    """在子线程执行代码并限制时长，返回错误信息（空串表示成功）

    注意：超时只能让调用方停止等待，Python 无法强制杀死线程。
    要彻底隔离需要换成子进程方案（见 README「已知局限」）。
    """
    box: dict[str, str] = {}

    def _target():
        try:
            exec(code, namespace)  # noqa: S102 - 已做静态校验 + 名称空间限制
        except Exception as e:  # noqa: BLE001 - 需要把任意异常转成错误串
            box["error"] = f"{type(e).__name__}: {e}"

    worker = threading.Thread(target=_target, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        return f"代码执行超过 {timeout} 秒，已停止等待（疑似死循环）"
    return box.get("error", "")


def _build_namespace(df: pd.DataFrame, out_path: Path) -> dict[str, Any]:
    """构造受限命名空间"""
    return {
        "pd": _safe_module(pd, _PANDAS_BLOCKED),
        "np": _safe_module(np, _NUMPY_BLOCKED),
        "plt": plt,
        "df": df.copy(),
        "OUTPUT_PATH": str(out_path),
        "__builtins__": _SAFE_BUILTINS,
    }


@dataclass
class ChartResult:
    question: str
    chart_type: str = ""
    code: str = ""
    file_path: str = ""
    error: str = ""
    attempts: int = 0

    @property
    def ok(self) -> bool:
        return bool(self.file_path) and not self.error


# ---------- 提示词 ----------

from app.agent.prompts import CHART_SYSTEM_PROMPT as SYSTEM_PROMPT

# ---------- 工具函数 ----------

def _strip_fence(code: str) -> str:
    fence = re.search(r"```(?:python)?\s*(.*?)```", code, re.DOTALL | re.IGNORECASE)
    return fence.group(1).strip() if fence else code.strip()


def _output_path(prefix: str = "chart") -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return settings.output_dir / f"{prefix}_{ts}.png"


# ---------- 主流程 ----------

def build_chart(
    question: str,
    data: list[dict[str, Any]],
    max_retry: int = MAX_RETRY,
) -> ChartResult:
    result = ChartResult(question=question)

    if not data:
        result.error = "输入数据为空"
        return result

    df = pd.DataFrame(data)
    llm = get_llm()
    out_path = _output_path()
    result.file_path = str(out_path)

    system_msg = SystemMessage(content=SYSTEM_PROMPT.format(
        columns=list(df.columns),
        preview=df.head(3).to_string(),
    ))
    messages = [system_msg, HumanMessage(content=question)]

    for attempt in range(1, max_retry + 1):
        result.attempts = attempt
        resp = llm.invoke(messages)
        code = _strip_fence(resp.content)
        result.code = code

        # 1) 静态校验：LLM 生成的代码属于不可信输入
        safe, reason = is_code_safe(code)
        if not safe:
            result.error = f"代码未通过安全校验: {reason}"
            messages.append(resp)
            messages.append(HumanMessage(
                content=f"{result.error}\n请只用 plt / pd / df 画图，不要读写文件或调用其他模块。"
            ))
            continue

        # 2) 受限命名空间 + 超时执行
        namespace = _build_namespace(df, out_path)
        err = _run_with_timeout(code, namespace)
        if err:
            result.error = err
            messages.append(resp)
            messages.append(HumanMessage(
                content=f"执行报错：{result.error}\n请修正代码后重新输出，只输出 Python 代码。"
            ))
            continue

        if not out_path.exists():
            result.error = "代码执行完毕但未生成图片，请检查是否调用了 plt.savefig"
            messages.append(resp)
            messages.append(HumanMessage(
                content="代码没有生成图片。请确保最后调用 plt.savefig(OUTPUT_PATH, dpi=150, bbox_inches='tight')。"
            ))
            continue

        result.error = ""
        result.chart_type = _infer_type(question, code)
        return result

    return result


_PANDAS_KIND = {
    "barh": "条形图", "bar": "柱状图", "pie": "饼图", "hist": "直方图",
    "scatter": "散点图", "box": "箱线图", "area": "面积图",
    "kde": "密度图", "density": "密度图", "line": "折线图",
}

# matplotlib 显式函数 → 中文名（用词边界精确匹配，避免 'plot' 误吞 'plotly'）
_MPL_FUNCS = [
    ("pie", "饼图"), ("barh", "条形图"), ("bar", "柱状图"),
    ("hist", "直方图"), ("scatter", "散点图"), ("boxplot", "箱线图"),
    ("stackplot", "面积图"), ("fill_between", "面积图"), ("violinplot", "小提琴图"),
    ("errorbar", "误差棒图"),
]

# 可能承载绘图的命名空间：plt / ax / ax1 / axs / axes / axes1 ...
_AXIS_OBJ = r"(?:plt|ax\d*|axs\d*|axes\d*|axis\d*)"


def _infer_type(question: str, code: str) -> str:
    """推断图表类型

    旧实现在 (question + code) 里按 ['pie','bar','hist','scatter','plot'] 顺序找子串。
    因为柱状图通常写成 df.plot(kind='bar')，其中不含 pie/hist/scatter，
    却含有 'plot'，于是柱状图被误判成折线图。这里改为按优先级解析真实调用：

      1. pandas 的 .plot(kind=...) 参数——最准确的信号
      2. matplotlib 的显式函数名（词边界精确匹配）
      3. 裸 .plot() / plt.plot()——才是折线图
      4. 提问里的中文关键词兜底

    只剥离注释、保留字符串：kind='bar' 的值本身就是要读的信号。
    """
    clean = strip_comments(code)

    # 1) pandas kind=（先看整段代码里有没有 kind=，再看有没有 .plot）
    km = re.search(r"kind\s*=\s*['\"]?([A-Za-z_]+)", clean)
    if km:
        return _PANDAS_KIND.get(km.group(1).lower(), f"其他（kind={km.group(1)}）")

    # 2) matplotlib 显式函数
    for name, cn in _MPL_FUNCS:
        if re.search(rf"\b{_AXIS_OBJ}\.{name}\s*\(", clean):
            return cn

    # 3) 有 .plot( 但没给 kind → pandas 默认折线图
    if re.search(r"\.plot\s*\(", clean):
        return "折线图"

    # 4) 兜底：提问里的中文关键词
    q = question.lower()
    for key, cn in [("饼", "饼图"), ("柱", "柱状图"), ("条", "条形图"),
                    ("折线", "折线图"), ("散点", "散点图"), ("直方", "直方图"),
                    ("分布", "直方图"), ("箱线", "箱线图")]:
        if key in q:
            return cn
    return "其他"


# ---------- 便捷入口 ----------

def ask(question: str, data: list[dict[str, Any]], verbose: bool = True) -> ChartResult:
    result = build_chart(question, data)
    if verbose:
        print(f"[问] {question}")
        if result.error:
            print(f"[错误] {result.error}")
        else:
            print(f"[图表] {result.chart_type} -> {result.file_path}")
        print()
    return result