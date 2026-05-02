from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse

from case_loader import load_default_case
from pipeline import run_pipeline
from schemas import Report

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5175"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/analyze", response_model=Report)
async def analyze() -> Report:
    doc_set = load_default_case()
    return await run_pipeline(doc_set)


@app.get("/documents/{document_id}", response_class=PlainTextResponse)
async def get_document(document_id: str) -> str:
    doc_set = load_default_case()
    try:
        return doc_set.by_id(document_id).text
    except KeyError:
        raise HTTPException(status_code=404, detail=f"unknown document_id: {document_id}")
