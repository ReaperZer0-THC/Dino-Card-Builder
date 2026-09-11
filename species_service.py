
from __future__ import annotations
from pathlib import Path
from typing import Dict, Any, Optional
import io

from PIL import Image
from species_intelligence_v2 import SpeciesIntelligence

HERE = Path(__file__).resolve().parent

ALIASES = {
    "autumn drakeling": "Drakeling (Autumn)",
    "spring drakeling": "Drakeling (Spring)",
    "summer drakeling": "Drakeling (Summer)",
    "winter drakeling": "Drakeling (Winter)",
}

engine = SpeciesIntelligence(
    str(HERE / "species-images-main.zip"),
    str(HERE / "ARKStatsExtractor-0.73.1.1.zip"),
)

def species_names():
    """Canonical ASA species names available to the renderer/classifier."""
    return list(engine._canonical_names)

def canonical_species(name: str) -> str:
    return ALIASES.get((name or "").strip().lower(), name)

def record_region_ids(record: Dict[str, Any]) -> Dict[int, int]:
    result = {}
    for i in range(6):
        row = (record.get("colors") or {}).get(str(i), {})
        cid = row.get("color_id")
        if isinstance(cid, int):
            result[i] = cid
    return result

def region_metadata(record: Dict[str, Any]):
    species = canonical_species(record.get("species") or "")
    sex = record.get("sex")
    ids = record_region_ids(record)
    return engine.region_record(species, ids, sex)

def render_creature(record: Dict[str, Any]) -> Image.Image:
    species = canonical_species(record.get("species") or "")
    if not species:
        raise ValueError("Species is required.")
    return engine.render(species, record_region_ids(record), record.get("sex"))

def render_creature_png(record: Dict[str, Any]) -> bytes:
    im = render_creature(record)
    buf = io.BytesIO()
    im.save(buf, "PNG")
    return buf.getvalue()
