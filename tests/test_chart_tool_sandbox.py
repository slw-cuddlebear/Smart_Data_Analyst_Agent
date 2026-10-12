"""chart_tool 沙箱与校验的单元测试（全部离线，不需要 API Key）

覆盖三件事：
1. is_code_safe 能拦住危险代码
2. 受限命名空间真正封掉了 pandas / numpy 的 IO 入口，同时不影响正常画图
3. build_chart 在拿到危险代码时不会执行它（用假 LLM，不联网）
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.tools import chart_tool  # noqa: E402


# ---------- 1. 静态校验 ----------

@pytest.mark.parametrize("code", [
    "import os",
    "from pathlib import Path",
    "__import__('os').system('calc')",
    "open('x.txt','w').write('hi')",
    "df.to_csv(r'C:\\temp\\leak.csv')",
    "df.to_pickle('x.pkl')",
    "pd.read_csv('C:\\\\Windows\\\\win.ini')",
    "eval('1+1')",
    "exec('x=1')",
    "subprocess.run(['cmd','/c','dir'])",
    "os.remove('important.db')",
    "getattr(df, 'to_csv')()",
])
def test_is_code_safe_blocks_dangerous(code):
    safe, reason = chart_tool.is_code_safe(code)
    assert safe is False, f"这段代码应该被拦下: {code}"
    assert reason


@pytest.mark.parametrize("code", [
    "df.groupby('region')['amount'].sum().plot(kind='bar')",
    "plt.pie(df['amount'], labels=df['region'])",
    "value = df['amount'].sum()",
    "result = sorted(df['region'].unique().tolist())",
    "plt.savefig(OUTPUT_PATH, dpi=150, bbox_inches='tight')",
])
def test_is_code_safe_allows_normal_chart_code(code):
    safe, reason = chart_tool.is_code_safe(code)
    assert safe is True, f"正常画图代码被误拦了: {code} / {reason}"


# ---------- 2. 受限命名空间 ----------

def _namespace(tmp_path):
    import pandas as pd
    df = pd.DataFrame({"region": ["华东", "华南"], "amount": [10, 20]})
    return chart_tool._build_namespace(df, tmp_path / "chart_test.png")


def test_namespace_has_no_full_builtins(tmp_path):
    ns = _namespace(tmp_path)
    builtins = ns["__builtins__"]
    assert "__import__" not in builtins
    assert "open" not in builtins
    assert "eval" not in builtins
    assert "exec" not in builtins
    # 正常画图要用的仍保留
    assert "len" in builtins
    assert "sorted" in builtins


def test_pandas_io_entries_are_blocked(tmp_path):
    ns = _namespace(tmp_path)
    with pytest.raises(PermissionError):
        ns["pd"].read_csv("anything.csv")


def test_numpy_io_entries_are_blocked(tmp_path):
    ns = _namespace(tmp_path)
    with pytest.raises(PermissionError):
        ns["np"].load("anything.npy")


def test_safe_module_does_not_pollute_real_pandas(tmp_path):
    """代理模块不能把全局 pandas 也改坏"""
    import pandas as pd

    original_read_csv = pd.read_csv
    _namespace(tmp_path)
    # 真实 pandas 的 read_csv 必须还是原来那个函数对象
    assert pd.read_csv is original_read_csv
    # 而且它仍然可调用（不是报错桩）
    assert callable(pd.read_csv)


def test_blocked_stub_raises_permission_error():
    stub = chart_tool._blocked("pd.read_csv")
    with pytest.raises(PermissionError):
        stub("x.csv")


def test_normal_code_runs_in_namespace(tmp_path):
    """正常代码能在受限命名空间里跑通，并生成图片"""
    ns = _namespace(tmp_path)
    code = """
df.groupby('region')['amount'].sum().plot(kind='bar')
plt.savefig(OUTPUT_PATH, dpi=60, bbox_inches='tight')
"""
    err = chart_tool._run_with_timeout(code, ns, timeout=30)
    assert err == "", f"正常代码执行失败: {err}"
    assert (tmp_path / "chart_test.png").exists()


def test_run_with_timeout_detects_dead_loop(tmp_path):
    """死循环应被超时判定拦住（不阻塞调用方太久）"""
    ns = _namespace(tmp_path)
    err = chart_tool._run_with_timeout("while True:\n    pass", ns, timeout=1)
    assert "超过" in err


# ---------- 3. build_chart 不会执行危险代码 ----------

def test_build_chart_rejects_dangerous_code_without_executing(tmp_path, monkeypatch):
    """假 LLM 返回危险代码时，build_chart 必须拒绝执行并给出错误"""
    monkeypatch.setattr(chart_tool, "_output_path",
                        lambda prefix="chart": tmp_path / "chart_x.png")

    canary = tmp_path / "canary.txt"
    evil_code = f"open(r'{canary}', 'w').write('pwned')"

    fake_llm = MagicMock()
    fake_llm.invoke.return_value = MagicMock(content=f"```python\n{evil_code}\n```")

    with patch.object(chart_tool, "get_llm", return_value=fake_llm):
        result = chart_tool.build_chart(
            "画个图",
            [{"region": "华东", "amount": 10}],
            max_retry=1,
        )

    assert result.ok is False
    assert "安全校验" in result.error
    # 关键断言：危险代码真的没有被执行
    assert not canary.exists(), "危险代码被执行了！沙箱失效"


def test_build_chart_rejects_file_write_via_pandas(tmp_path, monkeypatch):
    """LLM 用 df.to_csv 偷写文件也必须被拦"""
    monkeypatch.setattr(chart_tool, "_output_path",
                        lambda prefix="chart": tmp_path / "chart_y.png")

    leak = tmp_path / "leak.csv"
    evil_code = f"df.to_csv(r'{leak}')"

    fake_llm = MagicMock()
    fake_llm.invoke.return_value = MagicMock(content=evil_code)

    with patch.object(chart_tool, "get_llm", return_value=fake_llm):
        result = chart_tool.build_chart(
            "导出数据",
            [{"region": "华东", "amount": 10}],
            max_retry=1,
        )

    assert result.ok is False
    assert not leak.exists(), "df.to_csv 逃过了校验！"
