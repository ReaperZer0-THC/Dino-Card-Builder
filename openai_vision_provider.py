
from __future__ import annotations

import base64
import json
import os
from typing import Any, Dict

from openai import OpenAI


class OpenAIVisionProvider:
    """
    Production multimodal provider for the THC Dino Vision Adapter.

    The API key is read from OPENAI_API_KEY on the SERVER only.
    Never put the key in the browser/client.
    """

    def __init__(self, model: str | None = None):
        self.model = model or os.getenv("OPENAI_VISION_MODEL", "gpt-5.6-luna")
        self.client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    def infer(
        self,
        image_bytes: bytes,
        mime_type: str,
        instructions: str,
        schema: Dict[str, Any],
    ) -> Dict[str, Any]:
        encoded = base64.b64encode(image_bytes).decode("ascii")
        data_url = f"data:{mime_type};base64,{encoded}"

        response = self.client.responses.create(
            model=self.model,
            input=[
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": instructions},
                        {
                            "type": "input_image",
                            "image_url": data_url,
                            "detail": "high",
                        },
                    ],
                }
            ],
            text={
                "format": {
                    "type": "json_schema",
                    "name": "thc_dino_extraction",
                    "strict": True,
                    "schema": schema,
                }
            },
        )

        if not response.output_text:
            raise RuntimeError("Vision provider returned no structured output.")

        return json.loads(response.output_text)
