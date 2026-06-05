from typing import Literal

from pydantic import BaseModel, Field


REASONS_MAX_CHARACTERS = 1000

OUTLOOK_SCHEMA = {
    "name": "crypto_outlook",
    "description": "Return the final structured market outlook.",
    "input_schema": {
        "type": "object",
        "properties": {
            "interpretation": {
                "type": "string",
                "enum": ["Bullish", "Bearish", "Neutral"],
                "description": "Market outlook direction",
            },
            "confidence": {
                "type": "string",
                "enum": ["Low", "Medium", "High"],
                "description": "Conviction in the direction",
            },
            "reasons": {
                "type": "string",
                "description": (
                    "Concise rationale citing the strongest factors, "
                    f"under {REASONS_MAX_CHARACTERS} characters."
                ),
            },
        },
        "required": ["interpretation", "confidence", "reasons"],
    },
}


class AIOutlook(BaseModel):
    """Validated structure for an AI market outlook."""

    interpretation: Literal["Bullish", "Bearish", "Neutral"]
    confidence: Literal["Low", "Medium", "High"]
    reasons: str = Field(min_length=1, description="Non-empty rationale for the outlook")
