"""FastAPI 接口：POST /diagnose 诊断单条 SQL。

启动：uvicorn app:app --port 9091
"""
from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from agent import diagnose_sql
from llm_diagnoser import DiagnosisResult


class DiagnoseRequest(BaseModel):
    sql: str
    allow_llm: bool = True


app = FastAPI(title="DB Doctor", description="MySQL 慢查询 AI 诊断与优化 Agent")


@app.post("/diagnose", response_model=DiagnosisResult)
def diagnose(req: DiagnoseRequest) -> DiagnosisResult:
    return diagnose_sql(req.sql, allow_llm=req.allow_llm)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
