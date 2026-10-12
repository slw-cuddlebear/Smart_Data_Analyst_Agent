"""SQL 只读校验的单元测试（全部离线，不需要数据库和 API Key）

覆盖两个真实缺陷：
1. 多语句绕过：SELECT 1; ATTACH DATABASE ... 以前能通过校验
2. 误杀：列名 created_at 里的 "create" 会被当成写操作，导致正常查询被拒
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.tools import sql_tool  # noqa: E402


# ---------- 1. 必须拦下的多语句绕过 ----------

@pytest.mark.parametrize("sql", [
    "SELECT 1; ATTACH DATABASE 'evil.db' AS y",
    "SELECT 1; PRAGMA journal_mode=DELETE",
    "SELECT 1; DROP TABLE sales",
    "SELECT 1; DELETE FROM sales",
    "SELECT * FROM sales; VACUUM",
    "SELECT * FROM sales; UPDATE sales SET amount=0",
    "SELECT 1;;SELECT 2",
    "WITH x AS (SELECT 1) SELECT * FROM x; DROP TABLE sales",
])
def test_blocks_multi_statement(sql):
    safe, reason = sql_tool.is_safe(sql)
    assert safe is False, f"多语句应当被拦下: {sql}"
    assert "多条语句" in reason or "写操作" in reason


# ---------- 2. 必须拦下的写操作 ----------

@pytest.mark.parametrize("sql", [
    "INSERT INTO sales VALUES (1)",
    "UPDATE sales SET amount = 0",
    "DELETE FROM sales",
    "DROP TABLE sales",
    "TRUNCATE TABLE sales",
    "ALTER TABLE sales ADD COLUMN x INT",
    "CREATE TABLE t (a INT)",
    "ATTACH DATABASE 'evil.db' AS y",
    "PRAGMA query_only = 0",
    "VACUUM",
])
def test_blocks_write_statements(sql):
    safe, reason = sql_tool.is_safe(sql)
    assert safe is False, f"写操作应当被拦下: {sql}"


# ---------- 3. 正常只读查询必须放行（不能误杀） ----------

@pytest.mark.parametrize("sql", [
    "SELECT * FROM sales",
    "SELECT region, SUM(amount) FROM sales GROUP BY region",
    "SELECT * FROM sales WHERE created_at > '2024-01-01'",
    "SELECT created_at, updated_at FROM sales",          # 回归：create/update 子串
    "SELECT * FROM sales ORDER BY amount DESC LIMIT 10",
    "WITH t AS (SELECT region, SUM(amount) AS s FROM sales GROUP BY region) SELECT * FROM t",
    "SELECT * FROM sales WHERE note = 'please drop by tomorrow'",   # 字符串里的关键字
    "SELECT * FROM sales WHERE region = 'a;b'",                     # 字符串里的分号
])
def test_allows_read_only_queries(sql):
    safe, reason = sql_tool.is_safe(sql)
    assert safe is True, f"正常只读查询被误拦: {sql} -> {reason}"


def test_created_at_regression():
    """回归测试：以前 created_at 会被 \\bcreate\\b 误判（下划线边界处理）"""
    safe, reason = sql_tool.is_safe("SELECT created_at FROM sales")
    assert safe is True, f"created_at 被误判: {reason}"


def test_updated_at_regression():
    safe, reason = sql_tool.is_safe("SELECT updated_at FROM sales")
    assert safe is True, f"updated_at 被误判: {reason}"


# ---------- 4. 注释与字符串剥离 ----------

def test_comment_does_not_hide_write():
    """块注释里藏写操作也要能识别"""
    safe, _ = sql_tool.is_safe("SELECT 1 /* */ ; DROP TABLE sales")
    assert safe is False


def test_line_comment_is_stripped():
    safe, reason = sql_tool.is_safe("SELECT * FROM sales -- drop table sales\n")
    assert safe is True, f"行注释被误判: {reason}"


def test_split_statements_handles_semicolon_in_string():
    """切分逻辑已抽到 app/tools/text_utils.py（单一实现）"""
    from app.tools.text_utils import split_statements

    assert len(split_statements("SELECT * FROM t WHERE a='x;y'")) == 1
    assert len(split_statements("SELECT 1; SELECT 2")) == 2


# ---------- 5. LIMIT 兜底 ----------

def test_ensure_limit_adds_wrapper():
    capped = sql_tool._ensure_limit("SELECT * FROM sales")
    assert "LIMIT" in capped.upper()
    assert "_capped" in capped


def test_ensure_limit_keeps_existing():
    sql = "SELECT * FROM sales LIMIT 5"
    assert sql_tool._ensure_limit(sql) == sql


# ---------- 6. extract_sql ----------

def test_extract_sql_strips_fence_and_trailing_semicolon():
    text = "```sql\nSELECT * FROM sales;\n```"
    assert sql_tool.extract_sql(text) == "SELECT * FROM sales"


# ---------- 7. raw_sql 与 LIMIT 兜底的关系 ----------

def test_sql_result_has_raw_sql_field():
    """SQLResult 必须同时保留原始 SQL 与实际执行 SQL 两个字段"""
    r = sql_tool.SQLResult(question="q")
    assert r.raw_sql == ""
    assert r.sql == ""


def test_ensure_limit_does_not_change_raw_sql_semantics():
    """raw_sql 应等于 LLM 原始输出；被包装的是 sql，不是 raw_sql"""
    raw = "SELECT region, SUM(amount) FROM sales GROUP BY region"
    capped = sql_tool._ensure_limit(raw)
    # 原始 SQL 保持不动
    assert raw == "SELECT region, SUM(amount) FROM sales GROUP BY region"
    # 实际执行的加了兜底
    assert capped != raw
    assert "LIMIT" in capped.upper()
