#!/usr/bin/env bash
export VISION_PROVIDER=fixture
python -m uvicorn server:app --host 127.0.0.1 --port 8000
