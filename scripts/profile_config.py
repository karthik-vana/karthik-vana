"""Content for the terminal info card. Edit this file, not the SVG generator.

Every value is taken from the CV; nothing here is invented. Roles that have ended
(The Skill Union, Innomatics, the GIM fellowship) are deliberately not described as
current. Keep each value under ~44 characters so it fits the card width; the
generator raises an error instead of clipping text silently.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CardRow:
    label: str  # "" makes this a continuation line of the row above
    value: str


@dataclass(frozen=True)
class CardConfig:
    title: str  # window title bar
    handle: str  # shown as the neofetch header
    command: str  # the command typed after the first prompt
    rows: tuple[CardRow, ...]
    statement: str  # short career-focused line under the palette


CARD = CardConfig(
    title="karthik@github: ~",
    handle="karthik@github",
    command="whoami",
    rows=(
        CardRow("Name", "Karthik Vana"),
        CardRow("Role", "AI/ML Engineer"),  # CV summary: "AI/ML engineer"
        CardRow("Focus", "GenAI | RAG | NLP | CV"),  # Generative AI & NLP, Computer Vision
        CardRow("Education", "M.Tech CSE (AI/ML), 2025-27"),  # CV: Sep 2025 - Jun 2027, pursuing
        CardRow("Research", "Big Data Analytics | SRIP-2026"),  # CV: GIM fellowship, Apr-Jun 2026
        CardRow("", "Blockchain-based A2A authentication"),  # CV: research topic of the fellowship
        CardRow("Stack", "Python | TensorFlow | LangChain"),
        CardRow("Deploy", "Flask | Docker | AWS | GCP"),
        CardRow("Projects", "ShopMind AI"),
        CardRow("", "FoodVision AI"),
        CardRow("", "Telecom Retention System"),
        CardRow("", "Credit Card Risk Prediction"),
        CardRow("Certs", "Oracle OCI Gen AI Pro | AWS ML"),
        CardRow("Languages", "English | Telugu | Hindi"),
        CardRow("Status", "Building useful AI"),
    ),
    statement="From data prep to deployment: AI that ships.",
)
