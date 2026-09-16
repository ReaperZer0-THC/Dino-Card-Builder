
from __future__ import annotations

from pathlib import Path
import json
import os
import hashlib
import logging
import uuid

from fastapi import FastAPI, File, UploadFile, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from vision_adapter_v1 import FixtureVisionProvider, VisionAdapter, validate_record
from species_service import render_creature_png, region_metadata, species_names
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

adapter = VisionAdapter(provider, valid_species_names=species_names())

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger("thc.server")

app = FastAPI(title="THC Dino Card Builder", version="6.3")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET","POST"],
    allow_headers=["*"],
)

@app.get("/api/health")
def health():
    return {"ok":True,"provider":provider_name,"model":getattr(provider,"model",None),"version":"6.3"}

@app.post("/api/extract")
async def extract(file: UploadFile = File(...)):
    data=await file.read()
    if not data:
        return JSONResponse({"error":"Empty image."},status_code=400)
    if len(data) > MAX_UPLOAD_BYTES:
        return JSONResponse({"error":"Image is too large.","max_bytes":MAX_UPLOAD_BYTES},status_code=413)
    if not (file.content_type or "").startswith("image/"):
        return JSONResponse({"error":"Upload an image file."},status_code=415)
    request_id = uuid.uuid4().hex[:12]
    image_fingerprint = hashlib.sha256(data).hexdigest()[:12]
    logger.info("VISION_DIAG %s", json.dumps({
        "request_id": request_id,
        "stage": "request_start",
        "image_sha256_12": image_fingerprint,
        "mime_type": file.content_type or "image/png",
        "bytes": len(data),
        "provider": provider_name,
        "model": getattr(provider, "model", None),
    }, separators=(",", ":"), sort_keys=True))
    try:
        result = adapter.extract(data, file.content_type or "image/png", request_id=request_id)
        logger.info("VISION_DIAG %s", json.dumps({
            "request_id": request_id,
            "stage": "request_complete",
            "status": (result.get("validation") or {}).get("status"),
            "species": (result.get("record") or {}).get("species"),
        }, separators=(",", ":"), sort_keys=True))
        return result
    except Exception as exc:
        logger.exception("VISION_DIAG request_id=%s stage=request_error", request_id)
        return JSONResponse({
            "error":"Vision extraction failed.",
            "detail":str(exc),
            "request_id": request_id,
        },status_code=500)

@app.post("/api/creature")
def creature(record: dict = Body(...)):
    validation=validate_record(record)
    # V6.3: never render an unverified/review record. Wrong artwork can make an
    # uncertain species classification look authoritative.
    if not validation["card_generation_allowed"]:
        return JSONResponse({"error":"Species/data verification required before creature rendering.","validation":validation},status_code=422)
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
