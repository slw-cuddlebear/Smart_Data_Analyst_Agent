"""数据源注册表的单元测试（离线，不联网）

重点：SQLite 连接串的**相对路径存储**约定。

背景：早期实现把绝对路径写进 data/datasources.json，
一旦项目换目录/换机器/被 clone，注册表里的路径就全部失效，
运行时报 "unable to open database file"。
"""
import json
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import BASE_DIR  # noqa: E402
from app.db import registry  # noqa: E402


# ---------- 1. 路径转换函数 ----------

def test_to_relative_converts_project_internal_abs_path():
    abs_url = f"sqlite:///{BASE_DIR / 'data' / 'sample.db'}"
    assert registry._to_relative_url(abs_url) == "sqlite:///data/sample.db"


def test_to_relative_keeps_already_relative():
    assert registry._to_relative_url("sqlite:///data/sample.db") == "sqlite:///data/sample.db"


def test_to_relative_keeps_outside_path():
    outside = Path(tempfile.gettempdir()) / "outside.db"
    url = f"sqlite:///{outside}"
    # 项目外的数据库保持原样，不做相对化
    assert registry._to_relative_url(url) == url


def test_to_absolute_expands_relative():
    got = registry._to_absolute_url("sqlite:///data/sample.db")
    assert got == f"sqlite:///{(BASE_DIR / 'data' / 'sample.db').resolve()}"


def test_to_absolute_keeps_legacy_absolute():
    """兼容历史数据：文件里存的还是绝对路径时也要能用"""
    abs_url = f"sqlite:///{BASE_DIR / 'data' / 'sample.db'}"
    assert registry._to_absolute_url(abs_url) == abs_url


def test_non_sqlite_urls_untouched():
    mysql = "mysql+pymysql://user:pw@host:3306/db?charset=utf8mb4"
    assert registry._to_relative_url(mysql) == mysql
    assert registry._to_absolute_url(mysql) == mysql


def test_round_trip_is_stable():
    """相对 → 绝对 → 相对 必须回到原值"""
    rel = "sqlite:///data/sample.db"
    assert registry._to_relative_url(registry._to_absolute_url(rel)) == rel


def test_empty_url_is_safe():
    assert registry._to_relative_url("") == ""
    assert registry._to_absolute_url("") == ""


def test_external_absolute_path_round_trip():
    """项目外路径无论怎么转换都保持不变"""
    outside = Path(tempfile.gettempdir()).resolve() / "ext.db"
    url = f"sqlite:///{outside}"
    assert registry._to_absolute_url(registry._to_relative_url(url)) == url


# ---------- 2. 写盘后文件里必须是相对路径 ----------

@pytest.fixture
def temp_registry(tmp_path, monkeypatch):
    """把注册表指向临时文件，避免动到项目真实数据"""
    reg = tmp_path / "datasources.json"
    monkeypatch.setattr(registry, "REGISTRY_FILE", reg)
    monkeypatch.setattr(registry, "UPLOAD_DIR", tmp_path / "uploads")
    return reg


def test_save_writes_relative_path(temp_registry):
    registry.register(registry.DataSource(
        id="ds_test",
        name="测试",
        type="sqlite",
        url=f"sqlite:///{BASE_DIR / 'data' / 'sample.db'}",
    ))

    on_disk = json.loads(temp_registry.read_text(encoding="utf-8"))
    stored = [d for d in on_disk["datasources"] if d["id"] == "ds_test"][0]
    assert stored["url"] == "sqlite:///data/sample.db"
    # 关键断言：文件里不能出现项目绝对路径
    assert str(BASE_DIR) not in temp_registry.read_text(encoding="utf-8")


def test_load_returns_absolute_path(temp_registry):
    registry.register(registry.DataSource(
        id="ds_test",
        name="测试",
        type="sqlite",
        url=f"sqlite:///{BASE_DIR / 'data' / 'sample.db'}",
    ))
    ds = registry.get_datasource("ds_test")
    assert Path(ds.url.replace("sqlite:///", "")).is_absolute()


def test_registered_url_can_be_opened(temp_registry):
    """注册后拿到的 url 必须能真的连上数据库"""
    from sqlalchemy import create_engine, text

    registry.register(registry.DataSource(
        id="ds_test",
        name="测试",
        type="sqlite",
        url=f"sqlite:///{BASE_DIR / 'data' / 'sample.db'}",
    ))
    ds = registry.get_datasource("ds_test")
    engine = create_engine(ds.url)
    with engine.connect() as conn:
        n = conn.execute(text("select count(*) from sales")).scalar()
    engine.dispose()
    assert n == 500


def test_loads_legacy_absolute_path_file(temp_registry):
    """兼容历史文件：里面存的是绝对路径时也要能读且能连"""
    legacy = {
        "datasources": [{
            "id": "legacy",
            "name": "老数据源",
            "type": "sqlite",
            "url": f"sqlite:///{BASE_DIR / 'data' / 'sample.db'}",
            "builtin": False,
            "created_at": "",
        }]
    }
    temp_registry.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    ds = registry.get_datasource("legacy")
    assert Path(ds.url.replace("sqlite:///", "")).is_absolute()


def test_legacy_file_is_normalized_on_next_save(temp_registry):
    """历史绝对路径文件在下次保存时会被自动规范化成相对路径"""
    legacy = {
        "datasources": [{
            "id": "legacy",
            "name": "老数据源",
            "type": "sqlite",
            "url": f"sqlite:///{BASE_DIR / 'data' / 'sample.db'}",
            "builtin": False,
            "created_at": "",
        }]
    }
    temp_registry.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    # 触发一次写盘
    registry.register(registry.DataSource(id="new", name="新", type="sqlite",
                                         url="sqlite:///data/sample.db"))
    content = temp_registry.read_text(encoding="utf-8")
    assert str(BASE_DIR) not in content
    on_disk = json.loads(content)
    urls = {d["id"]: d["url"] for d in on_disk["datasources"]}
    assert urls["legacy"] == "sqlite:///data/sample.db"


def test_save_does_not_mutate_caller_object(temp_registry):
    """_save_raw 不能把调用方对象里的 url 改成相对路径"""
    original = f"sqlite:///{BASE_DIR / 'data' / 'sample.db'}"
    data = {"datasources": [{"id": "x", "name": "n", "type": "sqlite", "url": original}]}
    registry._save_raw(data)
    assert data["datasources"][0]["url"] == original


def test_first_run_creates_relative_registry(temp_registry):
    """首次运行生成的默认注册表也要是相对路径"""
    assert not temp_registry.exists()
    registry.list_datasources()
    content = temp_registry.read_text(encoding="utf-8")
    assert "sqlite:///data/sample.db" in content
    assert str(BASE_DIR) not in content


def test_outside_db_path_stays_absolute_in_file(temp_registry):
    """项目外的 sqlite 路径不做相对化"""
    outside = Path(tempfile.gettempdir()).resolve() / "ext_test.db"
    registry.register(registry.DataSource(
        id="ext", name="外部", type="sqlite", url=f"sqlite:///{outside}",
    ))
    on_disk = json.loads(temp_registry.read_text(encoding="utf-8"))
    stored = [d for d in on_disk["datasources"] if d["id"] == "ext"][0]
    assert str(outside) in stored["url"]


# ---------- 3. 项目真实注册表不含绝对路径（防止回归） ----------

def test_project_registry_has_no_absolute_path():
    real = BASE_DIR / "data" / "datasources.json"
    if not real.exists():
        pytest.skip("项目注册表不存在")
    content = real.read_text(encoding="utf-8")
    assert str(BASE_DIR) not in content, "项目注册表里又出现绝对路径了"
    assert "sqlite:///data/sample.db" in content
