"""Classification schema. Category labels come from configuration."""

from __future__ import annotations

from enum import Enum
from functools import lru_cache

from pydantic import BaseModel, ConfigDict, Field

from app.config import get_settings


class ExtractedFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_email: str | None = None
    order_id: str | None = None


@lru_cache
def classification_model(categories: tuple[str, ...]) -> type[BaseModel]:
    members = {f"C{index}": value for index, value in enumerate(categories)}
    category_enum = Enum("SupportCategory", members, type=str)

    class ClassificationResult(BaseModel):
        model_config = ConfigDict(use_enum_values=True, extra="forbid")
        category: category_enum
        confidence: float = Field(ge=0, le=1)
        is_ambiguous: bool
        reason: str
        extracted: ExtractedFields | None = None

    return ClassificationResult


def get_classification_model() -> type[BaseModel]:
    return classification_model(tuple(get_settings().category_list))
