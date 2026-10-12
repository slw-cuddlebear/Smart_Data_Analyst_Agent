"""数据源注册表：读写 data/datasources.json

**为什么存相对路径**：早期实现把 SQLAlchemy 连接串原样写进 JSON，
而 SQLite 连接串里是绝对路径。这样一旦项目换目录、换机器，
或别人 clone 下来，注册表里记录的路径就全部失效，
表现为运行时报 "unable to open database file"。

现在约定：
- 位于项目目录内的 SQLite 路径，**存盘时**转为相对 BASE_DIR 的 POSIX 形式（data/sample.db）
- **读取时**统一还原成绝对路径，因此调用方拿到的 url 始终可直接 create_engine
- 不在项目目录内的路径（用户自带的外部 db）保持绝对路径不变
- MySQL / PostgreSQL 等非 SQLite 连接串不动
"""
import json
import re
import shutil
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from app.config import BASE_DIR

REGISTRY_FILE = BASE_DIR / "data" / "datasources.json"
UPLOAD_DIR = BASE_DIR / "data" / "uploads"

# 匹配 sqlite:///<path>，且能处理 Windows 盘符
_SQLITE_URL_RE = re.compile(r"^sqlite:///(?P<path>.+)$")


def _to_relative_url(url: str) -> str:
    """存盘前：项目目录内的 SQLite 绝对路径 → 相对 BASE_DIR 的 POSIX 路径"""
    m = _SQLITE_URL_RE.match(url or "")
    if not m:
        return url
    raw = m.group("path")
    # 已是相对路径就不用动
    if not Path(raw).is_absolute():
        return url
    try:
        rel = Path(raw).resolve().relative_to(BASE_DIR.resolve())
    except (ValueError, OSError):
        return url          # 项目外的数据库，保留绝对路径
    return f"sqlite:///{rel.as_posix()}"


def _to_absolute_url(url: str) -> str:
    """读取后：相对路径 → 绝对路径，保证可以直接 create_engine"""
    m = _SQLITE_URL_RE.match(url or "")
    if not m:
        return url
    raw = m.group("path")
    if Path(raw).is_absolute():
        return url          # 兼容历史数据里的绝对路径
    return f"sqlite:///{(BASE_DIR / raw).resolve()}"


def _resolve_entries(raw: dict) -> dict:
    """就地还原所有数据源的 url 为绝对路径"""
    for item in raw.get("datasources", []):
        if "url" in item:
            item["url"] = _to_absolute_url(item["url"])
    return raw


@dataclass
class DataSource:
    id: str
    name: str
    type: str          # "sqlite" | "mysql" | "postgresql" | ...
    url: str           # SQLAlchemy 连接串
    builtin: bool = False
    created_at: str = ""


# ---------- 读写 ----------

def _load_raw() -> dict:
    if not REGISTRY_FILE.exists():
        # 首次运行：初始化内置数据源
        REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
        default = {
            "datasources": [
                {
                    "id": "sample",
                    "name": "示例数据（SQLite）",
                    "type": "sqlite",
                    "url": "sqlite:///data/sample.db",
                    "builtin": True,
                    "created_at": "",
                }
            ]
        }
        REGISTRY_FILE.write_text(
            json.dumps(default, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return _resolve_entries(default)
    raw = json.loads(REGISTRY_FILE.read_text(encoding="utf-8"))
    return _resolve_entries(raw)


def _save_raw(data: dict) -> None:
    """写盘前把项目内的 SQLite 绝对路径转成相对路径"""
    stored = json.loads(json.dumps(data))          # 深拷贝，避免改动调用方对象
    for item in stored.get("datasources", []):
        if "url" in item:
            item["url"] = _to_relative_url(item["url"])
    REGISTRY_FILE.write_text(
        json.dumps(stored, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------- 查询 ----------

def list_datasources() -> list[DataSource]:
    raw = _load_raw()
    return [DataSource(**item) for item in raw.get("datasources", [])]


def get_datasource(ds_id: str) -> DataSource:
    for ds in list_datasources():
        if ds.id == ds_id:
            return ds
    raise KeyError(f"数据源不存在: {ds_id}")


def get_default_id() -> str:
    items = list_datasources()
    if not items:
        raise RuntimeError("数据源列表为空")
    # 优先返回内置的
    for ds in items:
        if ds.builtin:
            return ds.id
    return items[0].id


# ---------- 注册与删除 ----------

def register(ds: DataSource) -> DataSource:
    raw = _load_raw()
    # ID 冲突检查
    for item in raw["datasources"]:
        if item["id"] == ds.id:
            raise ValueError(f"数据源 ID 已存在: {ds.id}")
    raw["datasources"].append(asdict(ds))
    _save_raw(raw)
    return ds


def unregister(ds_id: str) -> None:
    raw = _load_raw()
    target = None
    remaining = []
    for item in raw["datasources"]:
        if item["id"] == ds_id:
            target = item
        else:
            remaining.append(item)

    if target is None:
        raise KeyError(f"数据源不存在: {ds_id}")
    if target.get("builtin"):
        raise PermissionError("内置数据源不可删除")

    raw["datasources"] = remaining
    _save_raw(raw)

    # 清理上传目录
    upload_path = UPLOAD_DIR / ds_id
    if upload_path.exists():
        shutil.rmtree(upload_path)


# ---------- CSV 上传 ----------

def import_csv(file_name: str, file_bytes: bytes) -> DataSource:
    """把上传的 CSV 转成 SQLite，注册为新数据源"""
    ds_id = "ds_" + uuid.uuid4().hex[:8]
    ds_dir = UPLOAD_DIR / ds_id
    ds_dir.mkdir(parents=True, exist_ok=True)

    # 保存原始文件
    safe_name = Path(file_name).name or "data.csv"
    csv_path = ds_dir / safe_name
    csv_path.write_bytes(file_bytes)

    # 读入 pandas
    try:
        if safe_name.lower().endswith((".xlsx", ".xls")):
            df = pd.read_excel(csv_path)
        else:
            df = pd.read_csv(csv_path)
    except Exception as e:
        shutil.rmtree(ds_dir, ignore_errors=True)
        raise ValueError(f"无法解析文件: {e}")

    if df.empty:
        shutil.rmtree(ds_dir, ignore_errors=True)
        raise ValueError("文件内容为空")

    # 表名：用文件名转小写下划线
    table_name = Path(safe_name).stem.lower().replace("-", "_").replace(" ", "_")
    if not table_name.isidentifier():
        table_name = "data"

    # 写入 SQLite
    db_path = ds_dir / "data.db"
    from sqlalchemy import create_engine
    engine = create_engine(f"sqlite:///{db_path}")
    df.to_sql(table_name, engine, if_exists="replace", index=False)
    engine.dispose()

    display_name = f"{safe_name}（{len(df)} 行）"

    return register(DataSource(
        id=ds_id,
        name=display_name,
        type="sqlite",
        url=f"sqlite:///{db_path}",
        builtin=False,
        created_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    ))