# THC Dino Card Builder V6 — Render Ready

A full-stack Dino Card Builder for ARK: Survival Ascended.

## Pipeline

**Screenshot → Vision → Dino Record → Validation → Species Intelligence → ASA color render → THC Dino Card PNG**

## Card rules

- Species is checked from the displayed name and verified against the creature visual/icon; the visual wins on conflicts.
- Tribe/owner text is ignored.
- Stats: Health, Stamina, Oxygen, Food, Weight, Melee.
- Each stat retains Wild / Mut / Added.
- Explicit zeroes remain visible.
- Missing/unreadable values remain unknown; they are never silently converted to zero.
- Torpor and Movement are excluded.
- `Genetic Lvl = 1 + Σ(Wild + Mut)` across applicable card stats.
- All six color regions are retained, including non-displayed genetic regions.
- Cryopod-only screenshots are partial imports when breeding point triplets are not shown.

## API

- `GET /api/health`
- `POST /api/extract`
- `POST /api/creature`
- `POST /api/regions`
- `POST /api/card`

## Vision provider

Development:

```bash
VISION_PROVIDER=fixture uvicorn server:app --host 127.0.0.1 --port 8000
```

Production:

```text
VISION_PROVIDER=openai
OPENAI_VISION_MODEL=gpt-5.6-luna
OPENAI_API_KEY=<server secret>
```

The API key is used only by the server-side provider. It is never embedded in `web/index.html`.

## Render

`render.yaml` is included for one-repository deployment. See `DEPLOYMENT.md`.

## Assets

- `species-images-main.zip` supplies ASA base artwork and color masks.
- `ARKStatsExtractor-0.73.1.1.zip` supplies ASB species/color metadata.

## Known limitation

The bundled ASB color definition table does not currently resolve every newer ASA Color ID. Unknown IDs are retained rather than discarded or falsified.
