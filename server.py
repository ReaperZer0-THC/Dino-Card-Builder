
from __future__ import annotations

from pathlib import Path
import json
import os

from fastapi import FastAPI, File, UploadFile, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from vision_adapter_v1 import FixtureVisionProvider, VisionAdapter, validate_record
from species_service import render_creature_png, region_metadata
from card_renderer import render_card_png

HERE = Path(__file__).resolve().parent
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(20 * 1024 * 1024)))

provider_name = os.getenv("VISION_PROVIDER", "fixture").strip().lower()

if provider_name == "openai":
    from openai_vision_provider import OpenAIVisionProvider
    provider = OpenAIVisionProvider()
else:
    fixtures = json.loads((HERE / "validated_fixture_extractions.json").read_text())
    provider = FixtureVisionProvider(fixtures)

adapter = VisionAdapter(provider)

app = FastAPI(title="THC Dino Card Builder", version="6.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET","POST"],
    allow_headers=["*"],
)

@app.get("/api/health")
def health():
    return {"ok":True,"provider":provider_name,"model":getattr(provider,"model",None),"version":"6.0"}

@app.post("/api/extract")
async def extract(file: UploadFile = File(...)):
    data=await file.read()
    if not data:
        return JSONResponse({"error":"Empty image."},status_code=400)
    if len(data) > MAX_UPLOAD_BYTES:
        return JSONResponse({"error":"Image is too large.","max_bytes":MAX_UPLOAD_BYTES},status_code=413)
    if not (file.content_type or "").startswith("image/"):
        return JSONResponse({"error":"Upload an image file."},status_code=415)
    try:
        return adapter.extract(data,file.content_type or "image/png")
    except Exception as exc:
        return JSONResponse({"error":"Vision extraction failed.","detail":str(exc)},status_code=500)

@app.post("/api/creature")
def creature(record: dict = Body(...)):
    validation=validate_record(record)
    # Creature preview only needs identity + colors; do not require full card readiness.
    try:
        png=render_creature_png(record)
        return Response(png,media_type="image/png")
    except Exception as exc:
        return JSONResponse({"error":"Creature rendering failed.","detail":str(exc)},status_code=400)

@app.post("/api/regions")
def regions(record: dict = Body(...)):
    try:
        return {"regions":region_metadata(record)}
    except Exception as exc:
        return JSONResponse({"error":"Region lookup failed.","detail":str(exc)},status_code=400)

@app.post("/api/card")
def card(record: dict = Body(...)):
    validation=validate_record(record)
    if not validation["card_generation_allowed"]:
        return JSONResponse({"error":"Record is not card-ready.","validation":validation},status_code=422)
    try:
        png=render_card_png(record)
        return Response(png,media_type="image/png")
    except Exception as exc:
        return JSONResponse({"error":"Card rendering failed.","detail":str(exc)},status_code=400)

app.mount("/",StaticFiles(directory=HERE/"web",html=True),name="web")
