"""工具层公共的小函数

抽取原因：字符串/注释剥离逻辑原先在 sql_tool（校验 SQL）和 chart_tool
（推断图表类型）里各写了一份，行为容易走偏，这里统一成单一实现。
"""
from __future__ import annotations

import re


def strip_strings_and_comments(code: str) -> str:
    """把字符串字面量与注释替换成空白

    用途：后续做关键字匹配/特征匹配时，不会误判字面量或注释里的内容。
    例如：
      - SQL 里 WHERE note = 'please drop by' 不应被当成 DROP
      - Python 里 # 画柱状图 这行注释不应被当成真的在用 bar

    支持：
      - Python / SQL 通用的单引号、双引号字符串（含 '' 与 "" 转义）
      - 反引号标识符（MySQL 风格）
      - `--` 行注释与 `/* */` 块注释
      - Python 的 `#` 行注释与三引号文档字符串
    """
    if not code:
        return ""

    # Python 三引号与 # 注释（SQL 里不会出现，先处理掉不会误伤）
    out = re.sub(r"#[^\n]*", " ", code)
    out = re.sub(r'"""(?:.|\n)*?"""', " ", out)
    out = re.sub(r"'''(?:.|\n)*?'''", " ", out)

    chars: list[str] = []
    i, n = 0, len(out)
    quote: str | None = None

    while i < n:
        ch = out[i]

        if quote is not None:
            if ch == quote:
                # '' 或 "" 是转义，不算结束
                if i + 1 < n and out[i + 1] == quote:
                    i += 2
                    continue
                quote = None
            i += 1
            continue

        # -- 行注释（SQL）
        if ch == "-" and i + 1 < n and out[i + 1] == "-":
            while i < n and out[i] != "\n":
                i += 1
            continue

        # /* */ 块注释（SQL）
        if ch == "/" and i + 1 < n and out[i + 1] == "*":
            i += 2
            while i + 1 < n and not (out[i] == "*" and out[i + 1] == "/"):
                i += 1
            i += 2
            continue

        if ch in "'\"`":
            quote = ch
            chars.append(" ")
            i += 1
            continue

        chars.append(ch)
        i += 1

    return "".join(chars)


def strip_comments(code: str) -> str:
    """只去掉注释，保留字符串字面量

    用途：需要读取字符串内容时（例如解析 pandas 的 kind='bar'）。
    注意不能用 strip_strings_and_comments——那会把 'bar' 一起抹掉，
    而这些值恰恰是要读取的信号。

    实现是单遍状态机，注释识别必须在"是否处于字符串内"的判定之后，
    否则 x = 'a#b' 这种字符串里的 # 会被误当成注释起始。
    """
    if not code:
        return ""

    # 先去掉三引号字符串（多行说明文字），它们不应作为图表类型的判断依据；
    # 单双引号的短字符串要保留，因为 kind='bar' 的值本身是信号。
    code = re.sub(r'"""(?:.|\n)*?"""', " ", code)
    code = re.sub(r"'''(?:.|\n)*?'''", " ", code)

    chars: list[str] = []
    i, n = 0, len(code)
    quote: str | None = None      # 当前字符串引号（' " `）

    while i < n:
        ch = code[i]

        # --- 字符串内 ---
        if quote is not None:
            chars.append(ch)
            if ch == quote:
                if i + 1 < n and code[i + 1] == quote:   # '' / "" 转义
                    chars.append(code[i + 1])
                    i += 2
                    continue
                quote = None
            i += 1
            continue

        # --- 不在字符串内：才判定注释 ---
        if ch in "'\"`":
            quote = ch
            chars.append(ch)
            i += 1
            continue

        if ch == "#":                       # Python / shell 行注释
            while i < n and code[i] != "\n":
                i += 1
            continue

        if ch == "-" and i + 1 < n and code[i + 1] == "-":   # SQL 行注释
            while i < n and code[i] != "\n":
                i += 1
            continue

        if ch == "/" and i + 1 < n and code[i + 1] == "*":   # SQL 块注释
            i += 2
            while i + 1 < n and not (code[i] == "*" and code[i + 1] == "/"):
                i += 1
            i += 2
            continue

        chars.append(ch)
        i += 1

    return "".join(chars)


def split_statements(sql: str) -> list[str]:
    """按分号切分 SQL 语句（先剥离字符串与注释），过滤空片段"""
    plain = strip_strings_and_comments(sql)
    return [part.strip() for part in plain.split(";") if part.strip()]
