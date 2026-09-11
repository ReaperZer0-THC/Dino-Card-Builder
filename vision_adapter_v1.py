
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


SPECIES_RETRY_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["displayed_name", "visual_species", "reason"],
    "properties": {
        "displayed_name": {"type": ["string", "null"]},
        "visual_species": {"type": ["string", "null"]},
        "reason": {"type": "string"},
    },
}

SPECIES_RETRY_INSTRUCTIONS = """
Re-examine this ARK: Survival Ascended screenshot for SPECIES IDENTIFICATION ONLY.

Do not extract stats or colors. Focus on the creature body silhouette, head, limbs,
wing/feather/scale structure, tail, and any creature portrait/icon in the HUD.

Rules:
- Ignore tribe, owner, player, imprinter, and world text completely.
- displayed_name is only the creature name/title line. If that line cannot be
  distinguished from tribe/owner text, return displayed_name=null.
- visual_species must be the biological ARK species identified from the creature
  body or creature icon, not a custom creature name.
- Renamed creatures are common. A custom name does not make species unknown.
- If text and visual conflict, visual wins.
- Choose from the supplied known ASA species list when a confident visual match exists.
- Return null only when the creature/icon truly cannot be visually classified.
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
        return "\n\nKNOWN ASA SPECIES (use as a closed-set reference when visually appropriate):\n" + " | ".join(names)

    def extract(self, image_bytes: bytes, mime_type: str = "image/png", request_id: Optional[str] = None) -> Dict[str, Any]:
        """Run extraction while emitting safe, structured diagnostics.

        Diagnostic logs intentionally exclude image bytes, API credentials, and
        cryopod free-text values. They include only the fields needed to diagnose
        extraction/species-resolution behavior.
        """
        rid = request_id or "untracked"
        species_context = self._species_context()
        raw = self.provider.infer(
            image_bytes=image_bytes,
            mime_type=mime_type,
            instructions=VISION_INSTRUCTIONS + species_context,
            schema=EXTRACTION_SCHEMA,
        )

        pass1_snapshot = _diagnostic_snapshot(raw)
        logger.info("VISION_DIAG %s", json.dumps({
            "request_id": rid,
            "stage": "pass1_raw",
            **pass1_snapshot,
        }, separators=(",", ":"), sort_keys=True))

        record = normalize_extraction(raw, self.valid_species_names)
        logger.info("VISION_DIAG %s", json.dumps({
            "request_id": rid,
            "stage": "pass1_resolution",
            "species": record.get("species"),
            "species_source": record.get("species_source"),
            "species_confidence": record.get("species_confidence"),
        }, separators=(",", ":"), sort_keys=True))

        # If the general extraction could not resolve species, perform one focused
        # visual-classification retry. This retry is species-only: it is not given
        # a schema capable of changing stats, colors, sex, or level.
        species_retry = None
        if record.get("species") is None:
            species_retry = self.provider.infer(
                image_bytes=image_bytes,
                mime_type=mime_type,
                instructions=SPECIES_RETRY_INSTRUCTIONS + species_context,
                schema=SPECIES_RETRY_SCHEMA,
            )
            logger.info("VISION_DIAG %s", json.dumps({
                "request_id": rid,
                "stage": "species_retry_raw",
                "displayed_name": (species_retry or {}).get("displayed_name"),
                "visual_species": (species_retry or {}).get("visual_species"),
                "reason": (species_retry or {}).get("reason"),
            }, separators=(",", ":"), sort_keys=True))

            retry_visual = (species_retry or {}).get("visual_species")
            retry_displayed = (species_retry or {}).get("displayed_name")
            if retry_visual:
                # Only these two identity fields may be updated by the retry.
                raw["visual_species"] = retry_visual
                if retry_displayed:
                    raw["displayed_name"] = retry_displayed
                raw.setdefault("confidence_notes", []).append(
                    "Species resolved by focused visual retry: " + str((species_retry or {}).get("reason") or "visual classification")
                )
                record = normalize_extraction(raw, self.valid_species_names)

            # Prove in the log that the focused retry did not alter breeding data.
            pass2_snapshot = _diagnostic_snapshot(raw)
            logger.info("VISION_DIAG %s", json.dumps({
                "request_id": rid,
                "stage": "species_retry_merge",
                "species": record.get("species"),
                "species_source": record.get("species_source"),
                "breeding_data_changed": _breeding_data_changed(pass1_snapshot, pass2_snapshot),
            }, separators=(",", ":"), sort_keys=True))

        validation = validate_record(record)
        logger.info("VISION_DIAG %s", json.dumps({
            "request_id": rid,
            "stage": "validation",
            "status": validation.get("status"),
            "calculated_genetic_level": validation.get("calculated_genetic_level"),
            "shown_level": record.get("shown_level"),
            "species": record.get("species"),
            "issue_fields": [i.get("field") for i in validation.get("issues", [])],
        }, separators=(",", ":"), sort_keys=True))

        result = {
            "request_id": rid,
            "record": record,
            "validation": validation,
            "raw_extraction": raw,
        }
        if species_retry is not None:
            result["species_retry"] = species_retry
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
        if digest not in self.fixtures_by_sha256:
            return {
                "panel_type": None,
                "displayed_name": None,
                "visual_species": None,
                "sex": None,
                "shown_level": None,
                "stats": {
                    stat: {
                        "wild": None, "mutations": None, "added": None,
                        "applicable": True
                    } for stat in CARD_STATS
                },
                "colors": {str(i): {"color_id": None} for i in REGIONS},
                "cryopod_display_values": {},
                "confidence_notes": [
                    "Unknown image: no development fixture matched. No values guessed."
                ],
            }
        return json.loads(json.dumps(self.fixtures_by_sha256[digest]))
