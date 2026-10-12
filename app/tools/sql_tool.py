"""Text-to-SQL 工具：自然语言 -> SQL -> 执行 -> 返回数据"""
import re
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage

from app.agent.prompts import SQL_SYSTEM_PROMPT as SYSTEM_PROMPT
from app.db.engine import get_all_schemas, run_query
from app.llm import get_llm
from app.tools.text_utils import split_statements, strip_strings_and_comments

# 写操作关键字。注意用 \b...\b 且不吞掉下划线：
# re 里 \b 在 "_" 与字母之间不成立，所以 created_at 不会匹配到 create。
FORBIDDEN_KEYWORDS = re.compile(
    r"\b(insert|update|delete|drop|truncate|alter|create|replace|grant|revoke|"
    r"attach|detach|pragma|vacuum|reindex|analyze|begin|commit|rollback|"
    r"savepoint|release|copy|merge|call|do|set|lock|unlock)\b",
    re.IGNORECASE,
)

# 默认行数上限：防止 SELECT * FROM 大表把内存打满
DEFAULT_LIMIT = 1000

MAX_RETRY = 3


@dataclass
class SQLResult:
    question: str
    sql: str = ""                # 实际执行的 SQL（可能被补上 LIMIT 兜底）
    raw_sql: str = ""            # LLM 原始生成的 SQL，未经包装
    data: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    attempts: int = 0

    @property
    def ok(self) -> bool:
        return bool(self.sql) and not self.error


# ---------- 1. Schema 描述 ----------

def build_schema_text(datasource_id: str | None = None) -> str:
    """把所有表结构格式化成文本，喂给 LLM"""
    schemas = get_all_schemas(datasource_id)
    lines = []
    for table, columns in schemas.items():
        lines.append(f"表名: {table}")
        for col in columns:
            nullable = "" if col["nullable"] else " NOT NULL"
            lines.append(f"  - {col['name']} ({col['type']}{nullable})")
        lines.append("")
    return "\n".join(lines).strip()


# ---------- 2. SQL 提取与校验 ----------

def extract_sql(text: str) -> str:
    fence = re.search(r"```(?:sql)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1)
    return text.strip().rstrip(";").strip()


def _ensure_limit(sql: str, limit: int = DEFAULT_LIMIT) -> str:
    """外层没有 LIMIT 时补一个，避免全表拉回内存"""
    if re.search(r"\blimit\b", strip_strings_and_comments(sql), re.IGNORECASE):
        return sql
    return f"SELECT * FROM (\n{sql.rstrip().rstrip(';')}\n) AS _capped LIMIT {limit}"


def is_safe(sql: str) -> tuple[bool, str]:
    """只读校验：单语句 + SELECT/WITH 开头 + 无写操作关键字"""
    plain = strip_strings_and_comments(sql).strip()

    if not plain:
        return False, "SQL 为空"

    # 1) 拒绝多语句：SELECT 1; ATTACH DATABASE ... 这类绕过全靠这一步
    statements = split_statements(sql)
    if len(statements) > 1:
        return False, "只允许单条 SQL 语句，检测到多条语句（分号分隔）"

    # 2) 必须以 SELECT 或 WITH 开头
    lowered = plain.lower()
    if not (lowered.startswith("select") or lowered.startswith("with")):
        return False, "只允许 SELECT 查询"

    # 3) 写操作关键字（已排除字符串字面量里的误判）
    hit = FORBIDDEN_KEYWORDS.search(plain)
    if hit:
        return False, f"SQL 中包含被禁止的写操作关键字：{hit.group(0).upper()}"

    # 4) WITH 后面必须跟 SELECT，不能是 WITH ... DELETE/UPDATE
    if lowered.startswith("with"):
        body = lowered
        if not re.search(r"\)\s*select\b", body) and "select" not in body:
            return False, "WITH 子句后必须是 SELECT"

    return True, ""


# ---------- 3. 主流程 ----------

def text_to_sql(
    question: str,
    datasource_id: str | None = None,
) -> SQLResult:
    """自然语言 -> SQL -> 执行，带自我纠错"""
    result = SQLResult(question=question)
    llm = get_llm()
    schema = build_schema_text(datasource_id)

    system_msg = SystemMessage(content=SYSTEM_PROMPT.format(schema=schema))
    messages = [system_msg, HumanMessage(content=question)]

    for attempt in range(1, MAX_RETRY + 1):
        result.attempts = attempt
        resp = llm.invoke(messages)
        sql = extract_sql(resp.content)
        result.sql = sql
        result.raw_sql = sql

        safe, reason = is_safe(sql)
        if not safe:
            result.error = f"SQL 未通过安全校验: {reason}"
            return result

        # 补 LIMIT，防止全表拉回内存
        capped = _ensure_limit(sql)
        if capped != sql:
            result.sql = capped

        try:
            data = run_query(capped, datasource_id=datasource_id)
            result.data = data
            result.error = ""
            return result
        except Exception as e:
            err = str(e)
            result.error = err
            messages.append(resp)
            messages.append(HumanMessage(
                content=f"执行报错：{err}\n请修正 SQL 后重新输出，只输出 SQL。"
            ))

    return result


# ---------- 4. 便捷入口 ----------

def ask(
    question: str,
    datasource_id: str | None = None,
    verbose: bool = True,
) -> SQLResult:
    result = text_to_sql(question, datasource_id=datasource_id)
    if verbose:
        print(f"[问] {question}")
        print(f"[SQL] {result.sql}")
        if result.error:
            print(f"[错误] {result.error}")
        else:
            print(f"[结果] {len(result.data)} 行")
            for row in result.data[:5]:
                print(f"   {row}")
            if len(result.data) > 5:
                print(f"   ... 另有 {len(result.data) - 5} 行")
        print()
    return result