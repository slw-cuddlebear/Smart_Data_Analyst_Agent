"""FastAPI 服务：把 Agent 暴露为 HTTP 接口"""
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.agent.graph import run
from app.agent.memory import get_store
from app.config import BASE_DIR, settings
from app.db.engine import dispose_engine
from app.db.registry import (
    get_default_id,
    import_csv,
    list_datasources,
    unregister,
)

WEB_DIR = BASE_DIR / "app" / "web"
INDEX_FILE = WEB_DIR / "index.html"

MAX_DATA_ROWS = 100

def _safe_cell(v):
    """把 numpy 类型和 NaN 转成 JSON 可序列化的原生类型"""
    if v is None:
        return None
    if isinstance(v, float) and (v != v):  # NaN
        return None
    if hasattr(v, "item"):                  # numpy 标量
        try:
            return v.item()
        except Exception:
            return str(v)
    return v


def _serialize_python_result(value):
    """把 python_result 序列化成前端可渲染的结构

    返回的 dict 有一个 type 字段：
    - "table":  DataFrame / Series 转成表格
    - "text":   标量、字符串等
    - "json":   dict / list
    """
    if value is None:
        return None

    # DataFrame：有 to_dict 且有多列
    if hasattr(value, "to_dict") and hasattr(value, "columns"):
        try:
            cols = [str(c) for c in value.columns]
            rows = []
            for record in value.to_dict(orient="records"):
                rows.append({str(k): _safe_cell(v) for k, v in record.items()})
            return {"type": "table", "columns": cols, "rows": rows}
        except Exception:
            return {"type": "text", "value": str(value)}

    # Series：有 to_dict 且有 index 但没 columns
    if hasattr(value, "to_dict") and hasattr(value, "index"):
        try:
            rows = [{"index": str(k), "value": _safe_cell(v)}
                    for k, v in value.to_dict().items()]
            return {"type": "table", "columns": ["index", "value"], "rows": rows}
        except Exception:
            return {"type": "text", "value": str(value)}

    # dict / list：原样返回，让前端 JSON 展示
    if isinstance(value, (dict, list)):
        return {"type": "json", "value": value}

    # 其他：当字符串
    return {"type": "text", "value": str(value)}

app = FastAPI(title="智数析言 API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态挂载：图表
app.mount("/outputs", StaticFiles(directory=str(settings.output_dir)), name="outputs")

# 静态挂载：前端 vendor 资源
if WEB_DIR.exists():
    app.mount("/web", StaticFiles(directory=str(WEB_DIR)), name="web")


# ---------- 数据模型 ----------

class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1)
    session_id: str = Field(default="default")
    datasource_id: str = Field(default="")


class ChatResponse(BaseModel):
    question: str
    plan: list[str]
    steps: list[str]
    sql: str                # 实际执行的 SQL（可能被补上 LIMIT 兜底）
    raw_sql: str            # LLM 原始生成的 SQL，供前端展示
    data: list[dict]
    data_count: int
    data_truncated: bool
    python_result: dict | None
    chart_type: str | None
    chart_path: str | None
    chart_url: str | None
    conclusion: str
    error: str | None
    datasource_id: str


class DataSourceInfo(BaseModel):
    id: str
    name: str
    type: str
    builtin: bool
    created_at: str


# ---------- 通用接口 ----------

@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "model": settings.deepseek_model,
        "default_datasource": get_default_id(),
    }


@app.get("/datasources", response_model=list[DataSourceInfo])
def list_ds() -> list[DataSourceInfo]:
    return [
        DataSourceInfo(
            id=ds.id, name=ds.name, type=ds.type,
            builtin=ds.builtin, created_at=ds.created_at,
        )
        for ds in list_datasources()
    ]


@app.post("/datasources/upload", response_model=DataSourceInfo)
async def upload_ds(file: UploadFile = File(...)) -> DataSourceInfo:
    if not file.filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in {".csv", ".xlsx", ".xls"}:
        raise HTTPException(status_code=400, detail="仅支持 CSV / Excel 文件")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="文件内容为空")

    try:
        ds = import_csv(file.filename, content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"导入失败: {e}")

    return DataSourceInfo(
        id=ds.id, name=ds.name, type=ds.type,
        builtin=ds.builtin, created_at=ds.created_at,
    )


@app.delete("/datasources/{ds_id}")
def delete_ds(ds_id: str) -> dict:
    try:
        unregister(ds_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail=str(e))

    # 释放连接池
    dispose_engine(ds_id)
    return {"id": ds_id, "deleted": True}


# ---------- 会话历史 ----------

@app.get("/sessions/{session_id}/history")
def get_history(session_id: str) -> dict:
    return {"session_id": session_id, "history": get_store().get(session_id)}


@app.delete("/sessions/{session_id}")
def clear_session(session_id: str) -> dict:
    get_store().clear(session_id)
    return {"session_id": session_id, "cleared": True}


# ---------- 对话 ----------

@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")

    ds_id = req.datasource_id.strip() or get_default_id()

    try:
        state = run(
            question,
            session_id=req.session_id,
            datasource_id=ds_id,
            verbose=False,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent 执行失败: {type(e).__name__}: {e}")

    chart_path = state.get("chart_path") or ""
    chart_url = ""
    if chart_path and Path(chart_path).exists():
        chart_url = f"/outputs/{Path(chart_path).name}"

    full_data = state.get("data") or []
    data_count = len(full_data)
    data_truncated = data_count > MAX_DATA_ROWS
    data = full_data[:MAX_DATA_ROWS]

    error = (
        state.get("sql_error")
        or state.get("python_error")
        or state.get("chart_error")
        or ""
    )

    python_result = _serialize_python_result(state.get("python_result"))

    return ChatResponse(
        question=question,
        plan=state.get("plan", []),
        steps=state.get("steps", []),
        sql=state.get("sql", ""),
        raw_sql=state.get("raw_sql") or state.get("sql", ""),
        data=data,
        data_count=data_count,
        data_truncated=data_truncated,
        python_result=python_result,
        chart_type=(state.get("chart_type") or None) if chart_url else None,
        chart_path=chart_path or None,
        chart_url=chart_url or None,
        conclusion=state.get("conclusion", ""),
        error=error or None,
        datasource_id=ds_id,
    )


@app.get("/")
def index() -> FileResponse:
    if not INDEX_FILE.exists():
        raise HTTPException(status_code=404, detail="前端文件未找到: app/web/index.html")
    return FileResponse(str(INDEX_FILE))