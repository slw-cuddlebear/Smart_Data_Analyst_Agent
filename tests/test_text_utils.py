"""公共文本处理工具的单元测试（离线）

这两个函数原先在 sql_tool 和 chart_tool 里各有一份实现，
现在合并到 app/tools/text_utils.py，这里锁定它们的行为。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.tools.text_utils import (  # noqa: E402
    split_statements,
    strip_comments,
    strip_strings_and_comments,
)


# ---------- strip_strings_and_comments ----------

def test_removes_single_quoted_literal():
    out = strip_strings_and_comments("SELECT * FROM t WHERE note = 'please drop by'")
    assert "drop" not in out.lower()
    assert "select" in out.lower()


def test_removes_double_quoted_literal():
    out = strip_strings_and_comments('SELECT "delete" FROM t')
    assert "delete" not in out.lower()


def test_removes_backtick_identifier():
    out = strip_strings_and_comments("SELECT `drop` FROM t")
    assert "drop" not in out.lower()


def test_handles_escaped_quote_inside_literal():
    # 'it''s a drop' 里的 drop 也要被剥掉
    out = strip_strings_and_comments("SELECT * FROM t WHERE a = 'it''s a drop'")
    assert "drop" not in out.lower()


def test_removes_line_comment():
    out = strip_strings_and_comments("SELECT 1 -- drop table sales\n")
    assert "drop" not in out.lower()
    assert "select" in out.lower()


def test_removes_block_comment():
    out = strip_strings_and_comments("SELECT 1 /* drop table sales */ FROM t")
    assert "drop" not in out.lower()


def test_removes_python_hash_comment():
    out = strip_strings_and_comments("x = 1  # 画 bar 图\nplt.pie(v)")
    assert "bar" not in out.lower()
    assert "pie" in out.lower()


def test_removes_python_docstring():
    out = strip_strings_and_comments('"""这里是 bar 说明"""\nplt.pie(v)')
    assert "bar" not in out.lower()
    assert "pie" in out.lower()


def test_preserves_code_structure():
    out = strip_strings_and_comments("SELECT a, b FROM t WHERE c = 1")
    assert "select a, b from t where c = 1" in out.lower()


def test_empty_input():
    assert strip_strings_and_comments("") == ""


# ---------- strip_comments（保留字符串） ----------

def test_strip_comments_keeps_string_value():
    """kind='bar' 的 bar 必须保留——它是要读取的信号"""
    out = strip_comments("df.plot(kind='bar')  # 画柱状图")
    assert "bar" in out
    assert "画柱状图" not in out


def test_strip_comments_keeps_double_quoted_value():
    out = strip_comments('df.plot(kind="pie")')
    assert "pie" in out


def test_strip_comments_removes_hash_comment_only():
    out = strip_comments("x = 'a#b'  # real comment")
    assert "a#b" in out
    assert "real comment" not in out


def test_strip_comments_handles_escaped_quote():
    out = strip_comments("s = 'it''s bar'  # c")
    assert "bar" in out


def test_strip_comments_keeps_hash_inside_string():
    """字符串里的 # 不能被当成注释起始"""
    out = strip_comments("x = 'a#b'")
    assert "a#b" in out


def test_strip_comments_handles_triple_quote():
    out = strip_comments('"""doc with # hash"""\nplt.pie(v)')
    assert "pie" in out
    assert "hash" not in out


def test_strip_comments_empty():
    assert strip_comments("") == ""


# ---------- split_statements ----------

def test_split_single_statement():
    assert len(split_statements("SELECT 1")) == 1


def test_split_two_statements():
    assert len(split_statements("SELECT 1; SELECT 2")) == 2


def test_split_ignores_semicolon_in_string():
    assert len(split_statements("SELECT * FROM t WHERE a='x;y'")) == 1


def test_split_ignores_trailing_semicolon():
    assert len(split_statements("SELECT 1;")) == 1


def test_split_ignores_empty_fragments():
    assert len(split_statements("SELECT 1;;")) == 1


def test_split_semicolon_in_comment():
    assert len(split_statements("SELECT 1 -- a;b\n")) == 1


@pytest.mark.parametrize("sql,expected", [
    ("SELECT 1; DROP TABLE t", 2),
    ("SELECT 1 ; SELECT 2 ; SELECT 3", 3),
    ("SELECT * FROM t WHERE a = 'a;b' AND c = 'd;e'", 1),
])
def test_split_various(sql, expected):
    assert len(split_statements(sql)) == expected
