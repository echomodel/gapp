"""Tiny FastAPI service used as an eval fixture for the deploy skill."""
import os

from fastapi import FastAPI

app = FastAPI()

DATA_DIR = os.environ.get("SAMPLE_DATA_DIR", "./data")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/notes")
def notes():
    os.makedirs(DATA_DIR, exist_ok=True)
    return {"files": sorted(os.listdir(DATA_DIR))}
