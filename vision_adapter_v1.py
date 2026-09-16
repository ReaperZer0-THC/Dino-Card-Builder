
"""
THC Dino Vision Adapter V1

Purpose
-------
Converts an ARK: Survival Ascended screenshot interpretation into the locked
THC Dino Record schema.

This module deliberately separates:
1) visual inference (provider-specific)
2) normalization
3) species resolution
4) validation

That means the same Dino Card Builder can use any future vision provider
without changing the card, merge, or validation layers.
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol, Iterable
import json
import re
import logging

logger = logging.getLogger("thc.vision")

CARD_STATS = ("health", "stamina", "oxygen", "food", "weight", "melee")
REGIONS = tuple(range(6))

VISION_INSTRUCTIONS = """
You are interpreting a single ARK: Survival Ascended Xbox screenshot for a
breeding-card generator.

LOCKED RULES
- Ignore tribe and owner text entirely.
- Determine screenshot layout: "hud" or "cryopod".
- Species identification is a required visual-classification task whenever a creature body or creature icon is visible.
- First read the displayed creature-name/title line. Do NOT use tribe, owner, player, imprinter, or nearby-world text as displayed_name.
- Then independently identify the species from the creature body shape and/or creature portrait/icon.
- Compare the name-derived species with the visual species. If they conflict, the VISUAL species wins.
- A custom creature name is not a species. A custom name must never prevent visual species identification.
- If the name/title is ambiguous or appears renamed, still classify visual_species from morphology/icon.
- Use null for displayed_name rather than copying tribe/owner text.
- Example of the rule: a renamed creature can have a custom title while its body/icon clearly identifies Rock Drake; return visual_species="Rock Drake" and keep the custom title only as displayed_name.
- Sex must be "female" or "male" if visibly known, otherwise null.
- Preserve every explicit zero. Zero is a real observed value.
- Never replace unreadable/missing information with zero.
- Normal HUD card stats are exactly:
  health, stamina, oxygen, food, weight, melee.
- Exclude Torpor and Movement from breeding stats.
- For normal HUD stat triplets:
  first bracket = wild
  second bracket = mutations
  third bracket = added
- The final relevant HUD stat line is Oxygen, not Movement.
- Keep Regions 0 through 5, even when a region is not visually expressed.
- Ignore aggregate maternal/paternal mutation counters.
- Cryopod tooltips display current/max values rather than Wild/Mut/Added.
  For cryopod screenshots, do not infer breeding-point triplets from current
  values. Return null for unavailable breeding points.
- If a stat is explicitly not applicable (for example Maeguana Melee),
  set applicable=false and leave wild/mutations/added null.
- Do not guess.

Return only data matching the requested schema.
"""

EXTRACTION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "panel_type", "displayed_name", "visual_species", "sex",
        "shown_level", "stats", "colors", "cryopod_display_values",
        "confidence_notes"
    ],
    "properties": {
        "panel_type": {"type": ["string", "null"], "enum": ["hud", "cryopod", None]},
        "displayed_name": {"type": ["string", "null"]},
        "visual_species": {"type": ["string", "null"]},
        "sex": {"type": ["string", "null"], "enum": ["female", "male", None]},
        "shown_level": {"type": ["integer", "null"]},
        "stats": {
            "type": "object",
            "additionalProperties": False,
            "required": list(CARD_STATS),
            "properties": {
                stat: {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["wild", "mutations", "added", "applicable"],
                    "properties": {
                        "wild": {"type": ["integer", "null"]},
                        "mutations": {"type": ["integer", "null"]},
                        "added": {"type": ["integer", "null"]},
                        "applicable": {"type": "boolean"},
                    },
                } for stat in CARD_STATS
            },
        },
        "colors": {
            "type": "object",
            "additionalProperties": False,
            "required": [str(i) for i in REGIONS],
            "properties": {
                str(i): {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["color_id"],
                    "properties": {
                        "color_id": {"type": ["integer", "null"]}
                    },
                } for i in REGIONS
            },
        },
        "cryopod_display_values": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "health", "stamina", "weight", "melee", "oxygen",
                "food", "movement", "imprint", "torpor"
            ],
            "properties": {
                "health": {"type": ["string", "null"]},
                "stamina": {"type": ["string", "null"]},
                "weight": {"type": ["string", "null"]},
                "melee": {"type": ["string", "null"]},
                "oxygen": {"type": ["string", "null"]},
                "food": {"type": ["string", "null"]},
                "movement": {"type": ["string", "null"]},
                "imprint": {"type": ["string", "null"]},
                "torpor": {"type": ["string", "null"]},
            },
        },
        "confidence_notes": {
            "type": "array",
            "items": {"type": "string"}
        },
    },
}



# V6.3 separates HUD/data extraction from species classification.  The data
# schema has no species fields, and the species schema has no breeding fields.
# This makes it impossible for a species retry to overwrite stats/colors.
DATA_EXTRACTION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["panel_type", "sex", "shown_level", "stats", "colors",
                 "cryopod_display_values", "confidence_notes"],
    "properties": {
        "panel_type": {"type": ["string", "null"], "enum": ["hud", "cryopod", None]},
        "sex": {"type": ["string", "null"], "enum": ["female", "male", None]},
        "shown_level": {"type": ["integer", "null"]},
        "stats": EXTRACTION_SCHEMA["properties"]["stats"],
        "colors": EXTRACTION_SCHEMA["properties"]["colors"],
        "cryopod_display_values": EXTRACTION_SCHEMA["properties"]["cryopod_display_values"],
        "confidence_notes": EXTRACTION_SCHEMA["properties"]["confidence_notes"],
    },
}

DATA_EXTRACTION_INSTRUCTIONS = """
Read breeding DATA ONLY from this ARK: Survival Ascended Xbox screenshot.
Do not identify or guess the creature species and do not use creature morphology.

LOCKED RULES
- Ignore tribe/owner/player/imprinter text.
- Determine panel_type: hud or cryopod.
- Read sex and shown creature level when visible.
- HUD breeding stats are exactly health, stamina, oxygen, food, weight, melee.
- Preserve every explicit zero. Missing/unreadable is null, never zero.
- For HUD triplets: first=wild, second=mutations, third=added.
- Exclude Torpor and Movement from breeding stats.
- Keep all six Color IDs, Regions 0-5.
- Ignore maternal/paternal aggregate mutation counters.
- Cryopod current/max values are NOT breeding points; leave breeding triplets null.
- Do not guess values merely to make the level arithmetic match.
Return only the requested schema.
"""

SPECIES_CANDIDATE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["displayed_name", "candidates", "notes"],
    "properties": {
        "displayed_name": {"type": ["string", "null"]},
        "candidates": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["species", "confidence", "evidence"],
                "properties": {
                    "species": {"type": ["string", "null"]},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "evidence": {"type": "string"},
                },
            },
        },
        "notes": {"type": "array", "items": {"type": "string"}},
    },
}

SPECIES_CLASSIFICATION_INSTRUCTIONS = """
Identify the biological ARK: Survival Ascended SPECIES ONLY.
Do not read or return breeding stats, colors, sex, or level.

Rules:
- Read displayed_name only from the creature title/name line.
- Ignore tribe, owner, player, imprinter, and nearby world text completely.
- Renamed creatures are common; a custom displayed name is not a species.
- Classify from the creature body morphology and/or the creature portrait/icon.
- Return exactly three ranked candidate species, best match first.
- Use the supplied known ASA species list as a closed-set reference when appropriate.
- Evidence must describe visible morphology/icon cues, not hidden assumptions.
- Do not inflate confidence. If visual evidence is ambiguous, say medium/low.
"""


class VisionProvider(Protocol):
    def infer(self, image_bytes: bytes, mime_type: str, instructions: str,
              schema: Dict[str, Any]) -> Dict[str, Any]:
        """Return raw structured extraction matching EXTRACTION_SCHEMA."""


def _species_key(value: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def choose_species(displayed_name: Optional[str],
                   visual_species: Optional[str],
                   valid_species_names: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """
    Locked species rule:
    displayed name -> visual verification -> species.
    Visual wins on conflict.
    """
    dn = (displayed_name or "").strip()
    vs = (visual_species or "").strip()

    valid_map = {}
    if valid_species_names:
        valid_map = {_species_key(x): x for x in valid_species_names}

    dn_key = _species_key(dn)
    vs_key = _species_key(vs)

    name_species = valid_map.get(dn_key) if valid_map else None
    visual_canonical = valid_map.get(vs_key, vs) if vs else None

    if vs:
        if name_species and _species_key(name_species) == _species_key(visual_canonical):
            return {
                "species": visual_canonical,
                "source": "name+visual",
                "confidence": "high",
            }
        if name_species and _species_key(name_species) != _species_key(visual_canonical):
            return {
                "species": visual_canonical,
                "source": "visual_override",
                "confidence": "high",
                "note": f"Displayed name '{dn}' conflicts with visual species '{visual_canonical}'; visual wins.",
            }
        return {
            "species": visual_canonical,
            "source": "visual_override",
            "confidence": "high",
            "note": f"Displayed name '{dn}' treated as custom/non-species name; visual identifies species.",
        }

    if name_species:
        return {
            "species": name_species,
            "source": "name_only",
            "confidence": "medium",
        }

    return {
        "species": None,
        "source": "unresolved",
        "confidence": "low",
        "note": "Species could not be confirmed from displayed name or creature visual.",
    }


def normalize_extraction(raw: Dict[str, Any],
                         valid_species_names: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Normalize provider output into the locked Dino Record schema."""
    raw = raw or {}

    resolution = choose_species(
        raw.get("displayed_name"),
        raw.get("visual_species"),
        valid_species_names,
    )

    panel_type = raw.get("panel_type")
    stats_in = raw.get("stats") or {}
    colors_in = raw.get("colors") or {}

    stats = {}
    for stat in CARD_STATS:
        row = stats_in.get(stat) or {}
        applicable = row.get("applicable")
        if applicable is None:
            applicable = True

        if applicable is False:
            stats[stat] = {
                "wild": None,
                "mutations": None,
                "added": None,
                "applicable": False,
                "confidence": "explicit_na",
            }
            continue

        # Cryopod screenshots cannot provide breeding-point triplets safely.
        if panel_type == "cryopod":
            stats[stat] = {
                "wild": None,
                "mutations": None,
                "added": None,
                "applicable": True,
                "confidence": "unavailable_from_cryopod",
            }
            continue

        stats[stat] = {
            "wild": row.get("wild"),
            "mutations": row.get("mutations"),
            "added": row.get("added"),
            "applicable": True,
            "confidence": "vision",
        }

    colors = {}
    for region in REGIONS:
        row = colors_in.get(str(region), colors_in.get(region)) or {}
        colors[str(region)] = {"color_id": row.get("color_id")}

    review = []
    if resolution.get("note"):
        # Informational species notes are only review-blocking if unresolved.
        if resolution["species"] is None:
            review.append({
                "field": "species",
                "reason": "unresolved",
                "message": resolution["note"],
            })

    if panel_type == "cryopod":
        review.append({
            "field": "stats",
            "reason": "breeding_points_not_displayed",
            "message": "Cryopod screenshot does not display Wild/Mut/Added breeding-point triplets.",
        })

    record = {
        "panel_type": panel_type,
        "displayed_name": raw.get("displayed_name"),
        "species": resolution["species"],
        "species_source": resolution["source"],
        "species_confidence": resolution["confidence"],
        "sex": raw.get("sex"),
        "shown_level": raw.get("shown_level"),
        "stats": stats,
        "colors": colors,
        "cryopod_display_values": raw.get("cryopod_display_values") or {},
        "review": review,
        "vision_notes": list(raw.get("confidence_notes") or []),
    }
    return record


def genetic_level(record: Dict[str, Any]) -> Optional[int]:
    total = 1
    for stat in CARD_STATS:
        row = (record.get("stats") or {}).get(stat, {})
        if row.get("applicable") is False:
            continue
        wild = row.get("wild")
        mut = row.get("mutations")
        if not isinstance(wild, int) or not isinstance(mut, int):
            return None
        total += wild + mut
    return total


def validate_record(record: Dict[str, Any]) -> Dict[str, Any]:
    issues = []

    if record.get("panel_type") not in ("hud", "cryopod"):
        issues.append({
            "field": "panel_type",
            "reason": "unknown_layout",
            "message": "Screenshot layout is not recognized.",
        })

    if not record.get("species"):
        issues.append({
            "field": "species",
            "reason": "unresolved",
            "message": "Species needs visual/name review.",
        })

    if record.get("sex") not in ("female", "male"):
        issues.append({
            "field": "sex",
            "reason": "unresolved",
            "message": "Sex needs review.",
        })

    for stat in CARD_STATS:
        row = (record.get("stats") or {}).get(stat)
        if not isinstance(row, dict):
            issues.append({
                "field": f"stats.{stat}",
                "reason": "missing",
                "message": "Statline missing.",
            })
            continue
        if row.get("applicable") is False:
            continue
        for key in ("wild", "mutations", "added"):
            if row.get(key) is None:
                issues.append({
                    "field": f"stats.{stat}.{key}",
                    "reason": "unknown",
                    "message": "Unknown is not zero; review required.",
                })

    for region in REGIONS:
        row = (record.get("colors") or {}).get(str(region))
        if not isinstance(row, dict) or row.get("color_id") is None:
            issues.append({
                "field": f"colors.{region}",
                "reason": "unknown",
                "message": "Color region must be preserved; review missing ID.",
            })

    calc = genetic_level(record)
    shown = record.get("shown_level")
    if calc is not None and isinstance(shown, int) and calc != shown:
        issues.append({
            "field": "genetic_level",
            "reason": "mismatch",
            "message": f"Calculated {calc}, screenshot shows {shown}.",
        })

    # Avoid duplicate review entries by field/reason.
    seen = {(i["field"], i["reason"]) for i in issues}
    for item in record.get("review") or []:
        key = (item.get("field"), item.get("reason"))
        if key not in seen:
            issues.append(item)
            seen.add(key)

    return {
        "status": "PASS" if not issues else "REVIEW",
        "card_generation_allowed": not issues,
        "calculated_genetic_level": calc,
        "issues": issues,
    }


def _diagnostic_snapshot(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Return a log-safe subset of a raw extraction."""
    raw = raw or {}
    stats = {}
    for stat in CARD_STATS:
        row = (raw.get("stats") or {}).get(stat) or {}
        stats[stat] = {
            "wild": row.get("wild"),
            "mutations": row.get("mutations"),
            "added": row.get("added"),
            "applicable": row.get("applicable"),
        }
    colors = {}
    for region in REGIONS:
        row = (raw.get("colors") or {}).get(str(region), {}) or {}
        colors[str(region)] = row.get("color_id")
    return {
        "panel_type": raw.get("panel_type"),
        "displayed_name": raw.get("displayed_name"),
        "visual_species": raw.get("visual_species"),
        "sex": raw.get("sex"),
        "shown_level": raw.get("shown_level"),
        "stats": stats,
        "colors": colors,
        "confidence_notes": list(raw.get("confidence_notes") or []),
    }


def _breeding_data_changed(before: Dict[str, Any], after: Dict[str, Any]) -> bool:
    """True only if a species retry changed non-species breeding data."""
    keys = ("panel_type", "sex", "shown_level", "stats", "colors")
    return any(before.get(k) != after.get(k) for k in keys)


@dataclass
class VisionAdapter:
    provider: VisionProvider
    valid_species_names: Optional[Iterable[str]] = None

    def _species_context(self) -> str:
        names = list(self.valid_species_names or [])
        if not names:
            return ""
        return "\n\nKNOWN ASA SPECIES (closed-set reference):\n" + " | ".join(names)

    def _canonical_species(self, value: Optional[str]) -> Optional[str]:
        if not value:
            return None
        names = list(self.valid_species_names or [])
        if not names:
            return value.strip()
        m = {_species_key(x): x for x in names}
        return m.get(_species_key(value))

    @staticmethod
    def _raw_level(raw: Dict[str, Any]) -> Optional[int]:
        if (raw or {}).get("panel_type") != "hud":
            return None
        total = 1
        for stat in CARD_STATS:
            row = ((raw or {}).get("stats") or {}).get(stat) or {}
            if row.get("applicable") is False:
                continue
            if not isinstance(row.get("wild"), int) or not isinstance(row.get("mutations"), int):
                return None
            total += row["wild"] + row["mutations"]
        return total

    @staticmethod
    def _data_snapshot(raw: Dict[str, Any]) -> Dict[str, Any]:
        snap = _diagnostic_snapshot({**(raw or {}), "displayed_name": None, "visual_species": None})
        snap.pop("displayed_name", None)
        snap.pop("visual_species", None)
        return snap

    def _run_data(self, image_bytes: bytes, mime_type: str) -> Dict[str, Any]:
        return self.provider.infer(
            image_bytes=image_bytes,
            mime_type=mime_type,
            instructions=DATA_EXTRACTION_INSTRUCTIONS,
            schema=DATA_EXTRACTION_SCHEMA,
        )

    def _run_species(self, image_bytes: bytes, mime_type: str) -> Dict[str, Any]:
        return self.provider.infer(
            image_bytes=image_bytes,
            mime_type=mime_type,
            instructions=SPECIES_CLASSIFICATION_INSTRUCTIONS + self._species_context(),
            schema=SPECIES_CANDIDATE_SCHEMA,
        )

    def extract(self, image_bytes: bytes, mime_type: str = "image/png", request_id: Optional[str] = None) -> Dict[str, Any]:
        rid = request_id or "untracked"

        # PASS A: breeding data only. Species is not present in this schema.
        data1 = self._run_data(image_bytes, mime_type)
        calc1 = self._raw_level(data1)
        shown1 = data1.get("shown_level")
        logger.info("VISION_DIAG %s", json.dumps({
            "request_id": rid, "stage": "data_pass1",
            **self._data_snapshot(data1), "calculated_genetic_level": calc1,
        }, separators=(",", ":"), sort_keys=True))

        selected_data = data1
        data_retry = None
        data_retry_reason = None
        # Genetic level is an objective checksum for HUD screenshots. Retry the
        # data pass when missing fields or a mismatch proves the first read is bad.
        if data1.get("panel_type") == "hud" and isinstance(shown1, int) and calc1 != shown1:
            data_retry_reason = "genetic_level_mismatch_or_incomplete"
            data_retry = self._run_data(image_bytes, mime_type)
            calc2 = self._raw_level(data_retry)
            shown2 = data_retry.get("shown_level")
            logger.info("VISION_DIAG %s", json.dumps({
                "request_id": rid, "stage": "data_retry",
                **self._data_snapshot(data_retry), "calculated_genetic_level": calc2,
                "retry_reason": data_retry_reason,
            }, separators=(",", ":"), sort_keys=True))
            # Prefer a pass that independently satisfies the displayed-level checksum.
            if isinstance(shown2, int) and calc2 == shown2:
                selected_data = data_retry
            logger.info("VISION_DIAG %s", json.dumps({
                "request_id": rid, "stage": "data_selection",
                "selected": "retry" if selected_data is data_retry else "pass1",
                "pass1_level": calc1, "retry_level": calc2,
                "shown_level": (selected_data or {}).get("shown_level"),
            }, separators=(",", ":"), sort_keys=True))

        # PASS B: two isolated species classifications. These schemas contain no
        # breeding fields, so species work cannot mutate the selected data pass.
        sp1 = self._run_species(image_bytes, mime_type)
        sp2 = self._run_species(image_bytes, mime_type)

        def top(sp):
            arr = (sp or {}).get("candidates") or []
            return self._canonical_species((arr[0] or {}).get("species")) if arr else None

        top1, top2 = top(sp1), top(sp2)
        logger.info("VISION_DIAG %s", json.dumps({
            "request_id": rid, "stage": "species_pass1",
            "displayed_name": sp1.get("displayed_name"), "candidates": sp1.get("candidates"),
        }, separators=(",", ":"), sort_keys=True))
        logger.info("VISION_DIAG %s", json.dumps({
            "request_id": rid, "stage": "species_pass2",
            "displayed_name": sp2.get("displayed_name"), "candidates": sp2.get("candidates"),
        }, separators=(",", ":"), sort_keys=True))

        species = top1 if top1 and top1 == top2 else None
        displayed_name = sp1.get("displayed_name") or sp2.get("displayed_name")
        consensus = bool(species)
        logger.info("VISION_DIAG %s", json.dumps({
            "request_id": rid, "stage": "species_consensus",
            "top1": top1, "top2": top2, "accepted_species": species,
            "consensus": consensus,
        }, separators=(",", ":"), sort_keys=True))

        # Recombine only after both independent jobs finish.
        combined = json.loads(json.dumps(selected_data))
        combined["displayed_name"] = displayed_name
        combined["visual_species"] = species
        record = normalize_extraction(combined, self.valid_species_names)
        if species:
            record["species"] = species
            record["species_source"] = "visual_consensus"
            record["species_confidence"] = "verified"
        else:
            record["species"] = None
            record["species_source"] = "visual_disagreement"
            record["species_confidence"] = "review"
            record.setdefault("review", []).append({
                "field": "species",
                "reason": "classifier_disagreement",
                "message": "Species classifiers disagreed; verify the species before rendering.",
            })
        record["species_candidates"] = {
            "pass1": sp1.get("candidates") or [],
            "pass2": sp2.get("candidates") or [],
        }
        record["vision_notes"] = list(selected_data.get("confidence_notes") or [])

        validation = validate_record(record)
        logger.info("VISION_DIAG %s", json.dumps({
            "request_id": rid, "stage": "validation",
            "status": validation.get("status"),
            "calculated_genetic_level": validation.get("calculated_genetic_level"),
            "shown_level": record.get("shown_level"), "species": record.get("species"),
            "species_source": record.get("species_source"),
            "issue_fields": [i.get("field") for i in validation.get("issues", [])],
        }, separators=(",", ":"), sort_keys=True))

        result = {
            "request_id": rid,
            "record": record,
            "validation": validation,
            "diagnostics": {
                "data_retry_used": selected_data is data_retry,
                "data_retry_reason": data_retry_reason,
                "species_consensus": consensus,
                "species_top": [top1, top2],
            },
        }
        return result


class FixtureVisionProvider:
    """
    Development provider.

    It proves the adapter/server/UI contract using already-validated screenshots.
    It intentionally refuses to invent values for an unknown image.
    """

    def __init__(self, fixtures_by_sha256: Dict[str, Dict[str, Any]]):
        self.fixtures_by_sha256 = fixtures_by_sha256

    def infer(self, image_bytes: bytes, mime_type: str, instructions: str,
              schema: Dict[str, Any]) -> Dict[str, Any]:
        import hashlib
        digest = hashlib.sha256(image_bytes).hexdigest()
        fixture = self.fixtures_by_sha256.get(digest)

        # Species-only contract. Development fixtures deterministically expose
        # their validated visual species as the top candidate.
        if "candidates" in (schema.get("properties") or {}):
            species = (fixture or {}).get("visual_species") if fixture else None
            displayed = (fixture or {}).get("displayed_name") if fixture else None
            return {
                "displayed_name": displayed,
                "candidates": [
                    {"species": species, "confidence": "high" if species else "low", "evidence": "validated development fixture" if species else "unknown image"},
                    {"species": None, "confidence": "low", "evidence": "no second fixture candidate"},
                    {"species": None, "confidence": "low", "evidence": "no third fixture candidate"},
                ],
                "notes": ["Development fixture species classification."],
            }

        if not fixture:
            return {
                "panel_type": None, "sex": None, "shown_level": None,
                "stats": {stat: {"wild": None, "mutations": None, "added": None, "applicable": True} for stat in CARD_STATS},
                "colors": {str(i): {"color_id": None} for i in REGIONS},
                "cryopod_display_values": {k: None for k in ("health","stamina","weight","melee","oxygen","food","movement","imprint","torpor")},
                "confidence_notes": ["Unknown image: no development fixture matched. No values guessed."],
            }

        # Data-only contract: deliberately strip species/name fields.
        f = json.loads(json.dumps(fixture))
        return {
            "panel_type": f.get("panel_type"),
            "sex": f.get("sex"),
            "shown_level": f.get("shown_level"),
            "stats": f.get("stats") or {},
            "colors": f.get("colors") or {},
            "cryopod_display_values": f.get("cryopod_display_values") or {k: None for k in ("health","stamina","weight","melee","oxygen","food","movement","imprint","torpor")},
            "confidence_notes": f.get("confidence_notes") or [],
        }

