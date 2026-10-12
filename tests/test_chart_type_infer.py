"""图表类型推断的单元测试（离线）

重点回归：旧实现会在 (question + code) 里按 ['pie','bar','hist','scatter','plot']
的顺序找子串，而柱状图通常写成 df.plot(kind='bar')，其中不含 'bar' 之前的
'pie'/'hist'/'scatter'，却含有 'plot'，所以柱状图被误判成折线图。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.tools import chart_tool  # noqa: E402


# ---------- 回归：柱状图不能被判成折线图 ----------

def test_bar_via_pandas_kind():
    code = "df.plot(kind='bar', x='region', y='total')"
    assert chart_tool._infer_type("各地区销售额", code) == "柱状图"


def test_bar_via_pandas_kind_double_quote():
    code = 'df.plot(kind="bar")'
    assert chart_tool._infer_type("销售额", code) == "柱状图"


def test_line_is_line():
    code = "df.plot(kind='line')"
    assert chart_tool._infer_type("销售趋势", code) == "折线图"


def test_bare_plot_is_line():
    code = "df.plot()"
    assert chart_tool._infer_type("趋势", code) == "折线图"


# ---------- matplotlib 显式函数 ----------

@pytest.mark.parametrize("code,expected", [
    ("plt.bar(x, y)", "柱状图"),
    ("plt.barh(x, y)", "条形图"),
    ("plt.pie(values, labels=labels)", "饼图"),
    ("plt.hist(values, bins=10)", "直方图"),
    ("plt.scatter(x, y)", "散点图"),
    ("plt.boxplot(data)", "箱线图"),
    ("ax.bar(x, y)", "柱状图"),
    ("axs.scatter(x, y)", "散点图"),
])
def test_matplotlib_explicit_funcs(code, expected):
    assert chart_tool._infer_type("画图", code) == expected


# ---------- kind= 优先于其他特征 ----------

def test_kind_takes_priority_over_plt_calls():
    """先 df.plot(kind='bar') 再 plt.plot() 时，应以 kind 为准"""
    code = """
df.plot(kind='bar')
plt.plot([1,2,3])
"""
    assert chart_tool._infer_type("画图", code) == "柱状图"


def test_unknown_kind_is_reported():
    code = "df.plot(kind='hexbin')"
    got = chart_tool._infer_type("画图", code)
    assert "hexbin" in got


# ---------- 注释与字符串不能作为依据 ----------

def test_comment_is_ignored():
    """注释里写了 bar，但代码其实是折线图"""
    code = """
# 这里本来想画 bar 图
df.plot(kind='line')
"""
    assert chart_tool._infer_type("画图", code) == "折线图"


def test_string_literal_is_ignored():
    code = """
title = '饼图对比'
plt.plot([1,2,3])
"""
    assert chart_tool._infer_type("画图", code) == "折线图"


# ---------- 兜底：问题关键词 ----------

@pytest.mark.parametrize("question,expected", [
    ("用饼图展示占比", "饼图"),
    ("画个柱状图", "柱状图"),
    ("看看分布", "直方图"),
])
def test_fallback_to_question_keywords(question, expected):
    assert chart_tool._infer_type(question, "x = 1") == expected


def test_no_signal_returns_other():
    assert chart_tool._infer_type("随便", "x = 1") == "其他"


# ---------- 真实生成的代码形态 ----------

def test_realistic_generated_code():
    """模拟 LLM 实际输出（含 savefig），应识别为柱状图"""
    code = """
fig, ax = plt.subplots(figsize=(10, 6))
bars = ax.bar(df['region'], df['total_amount'], color='steelblue')
for b, v in zip(bars, df['total_amount']):
    ax.text(b.get_x() + b.get_width()/2, v, f'{v:,.0f}', ha='center', va='bottom')
ax.set_title('各地区销售额对比')
ax.set_xlabel('地区')
ax.set_ylabel('销售额')
plt.savefig(OUTPUT_PATH, dpi=120, bbox_inches='tight')
plt.close()
"""
    assert chart_tool._infer_type("用柱状图展示各地区销售额", code) == "柱状图"
