# THC Dino Card Builder — Render Deployment

This repository is ready to deploy as a single Render **Web Service**. The browser UI and FastAPI backend are served by the same service.

## Recommended: Render Blueprint

1. Push this folder to the root of your GitHub repository.
2. In Render, create a new **Blueprint** from that repository.
3. Render will read `render.yaml` and create the `thc-dino-card-builder` web service.
4. When prompted for `OPENAI_API_KEY`, enter the API key as a secret. Do not place the key in GitHub or in the browser.
5. Deploy.
6. Open the generated `*.onrender.com` address.
7. Verify `/api/health` returns `ok: true`, `provider: openai`, and the configured model.

The Blueprint already sets:
- `VISION_PROVIDER=openai`
- `OPENAI_VISION_MODEL=gpt-5.6-luna`
- health check path `/api/health`
- Uvicorn binding to Render's `$PORT`
- automatic deploys from commits

## Manual Render setup

If you do not use the Blueprint:

- Service type: **Web Service**
- Runtime: **Python 3**
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn server:app --host 0.0.0.0 --port $PORT`
- Health check: `/api/health`

Environment variables:
- `VISION_PROVIDER=openai`
- `OPENAI_VISION_MODEL=gpt-5.6-luna`
- `OPENAI_API_KEY=<secret>`

## Local fixture test

Install dependencies:

```bash
pip install -r requirements.txt
```

Run without making OpenAI API calls:

```bash
VISION_PROVIDER=fixture uvicorn server:app --host 127.0.0.1 --port 8000
```

Windows PowerShell:

```powershell
$env:VISION_PROVIDER="fixture"
python -m uvicorn server:app --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`.

## Production data flow

`Xbox screenshot → /api/extract → structured Dino Record → validation → species/color renderer → /api/card → PNG`

The uploaded screenshot is processed for extraction; the current app does not require a database or persistent screenshot storage.

## Runtime assets

The repository includes:
- `species-images-main.zip`
- `ARKStatsExtractor-0.73.1.1.zip`

These are read directly at runtime by Species Intelligence. No startup extraction step or persistent disk is required.

## Production verification checklist

After deployment:

1. Open `/api/health` and confirm `provider` is `openai`.
2. Upload one known HUD screenshot.
3. Confirm the extracted Dino Record before generating the card.
4. Confirm explicit zero values remain visible.
5. Confirm unknown fields remain unknown rather than becoming zero.
6. Confirm `Genetic Lvl` matches the Wild + Mut calculation.
7. Generate the creature preview and final card.
8. Test a cryopod screenshot and confirm incomplete breeding points block card generation.

## Known data issue

The current ASB `values.json` bundled in the source has a limited `colorDefinitions` list. Newer/high ASA Color IDs (for example ID 202 in the Pyromane test) can still be preserved as IDs but may render with `Unknown` name/fallback tint until the authoritative extended ASA color table is added.
