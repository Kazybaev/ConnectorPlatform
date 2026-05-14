from __future__ import annotations

import logging
from functools import lru_cache
from io import BytesIO
from typing import Iterable

import pandas as pd

from app.models.schemas import AgentKnowledgeBase, FAQItem, Instructions

logger = logging.getLogger(__name__)

HEADER_MAP = {
    "раздел": "РАЗДЕЛ",
    "section": "РАЗДЕЛ",
    "поле": "ПОЛЕ",
    "field": "ПОЛЕ",
    "значение": "ЗНАЧЕНИЕ",
    "value": "ЗНАЧЕНИЕ",
}

CANONICAL_COLUMNS = ["РАЗДЕЛ", "ПОЛЕ", "ЗНАЧЕНИЕ"]
REQUIRED_COLUMNS = set(CANONICAL_COLUMNS)
FAQ_SECTION_NAMES = {"faq"}
INSTRUCTION_SECTION_NAMES = {"инструкции ai", "ai instructions"}
LIMITATION_SECTION_NAMES = {"ограничения", "limitations"}
CHANNEL_SECTION_NAMES = {"каналы", "channels"}
CHANNEL_FIELD_NAMES = {"канал", "каналы", "channel", "channels"}
COMPANY_FIELD_NAMES = {"название компании", "company name"}
INSTRUCTION_FIELD_MAP = {
    "роль": "role",
    "role": "role",
    "тон": "tone",
    "tone": "tone",
    "цель": "goal",
    "goal": "goal",
}


class ExcelParserError(ValueError):
    """Raised when the uploaded spreadsheet cannot be parsed safely."""


class ExcelParserService:
    """Parse Excel files with a fixed three-column business schema."""

    def parse(self, file_bytes: bytes) -> AgentKnowledgeBase:
        """Convert the first Excel sheet into the API response model."""
        dataframe = self._load_dataframe(file_bytes)
        knowledge_base = AgentKnowledgeBase(
            company="",
            faq=[],
            instructions=Instructions(),
            limitations=[],
            channels=[],
        )

        for row in dataframe.to_dict(orient="records"):
            section_raw = self._stringify(row.get("РАЗДЕЛ"))
            field_raw = self._stringify(row.get("ПОЛЕ"))
            value_raw = self._stringify(row.get("ЗНАЧЕНИЕ"))

            if not any((section_raw, field_raw, value_raw)):
                continue

            section = self._normalize_key(section_raw)
            field = self._normalize_key(field_raw)

            if section in FAQ_SECTION_NAMES and field_raw and value_raw:
                knowledge_base.faq.append(
                    FAQItem(
                        question=field_raw,
                        answer=value_raw,
                    )
                )
                continue

            if field in COMPANY_FIELD_NAMES and value_raw:
                knowledge_base.company = value_raw
                continue

            if section in INSTRUCTION_SECTION_NAMES:
                instruction_name = INSTRUCTION_FIELD_MAP.get(field)
                if instruction_name and value_raw:
                    setattr(knowledge_base.instructions, instruction_name, value_raw)
                continue

            if section in LIMITATION_SECTION_NAMES:
                limitation = value_raw or field_raw
                if limitation:
                    self._append_unique(knowledge_base.limitations, limitation)
                continue

            if section in CHANNEL_SECTION_NAMES or field in CHANNEL_FIELD_NAMES:
                raw_channels = value_raw or field_raw
                for channel in self._split_multi_value(raw_channels):
                    self._append_unique(knowledge_base.channels, channel)

        logger.info(
            "Excel parsing completed: company=%s, faq=%s, limitations=%s, channels=%s",
            knowledge_base.company or "<empty>",
            len(knowledge_base.faq),
            len(knowledge_base.limitations),
            len(knowledge_base.channels),
        )
        return knowledge_base

    def _load_dataframe(self, file_bytes: bytes) -> pd.DataFrame:
        """Read the first sheet and normalize incoming column names."""
        try:
            dataframe = pd.read_excel(BytesIO(file_bytes), sheet_name=0)
        except ImportError as exc:
            raise ExcelParserError(
                "Excel support is not fully installed. Please install the required parser packages."
            ) from exc
        except ValueError as exc:
            raise ExcelParserError("Unable to read the Excel file. Please upload a valid spreadsheet.") from exc
        except Exception as exc:
            raise ExcelParserError(
                "Unable to parse the Excel file. If this is an old .xls file, please save it as .xlsx and retry."
            ) from exc

        dataframe = dataframe.rename(columns=self._normalize_headers(list(dataframe.columns)))
        missing_columns = REQUIRED_COLUMNS.difference(set(dataframe.columns))
        if missing_columns:
            missing = ", ".join(sorted(missing_columns))
            raise ExcelParserError(f"Missing required columns: {missing}.")

        return dataframe[CANONICAL_COLUMNS]

    @staticmethod
    def _normalize_headers(columns: list[object]) -> dict[object, str]:
        """Map flexible column names to the canonical schema."""
        normalized: dict[object, str] = {}
        for column in columns:
            normalized_key = ExcelParserService._normalize_key(ExcelParserService._stringify(column))
            mapped_name = HEADER_MAP.get(normalized_key)
            if mapped_name:
                normalized[column] = mapped_name
        return normalized

    @staticmethod
    def _stringify(value: object) -> str:
        """Convert cells to clean strings and drop NaN-like values."""
        if value is None or pd.isna(value):
            return ""
        return str(value).strip()

    @staticmethod
    def _normalize_key(value: str) -> str:
        """Build a stable lowercase key for comparisons."""
        return " ".join(value.replace("\n", " ").split()).casefold()

    @staticmethod
    def _split_multi_value(value: str) -> Iterable[str]:
        """Split channels by common separators while preserving order."""
        normalized = value.replace(";", ",").replace("\n", ",")
        for item in normalized.split(","):
            clean_item = item.strip()
            if clean_item:
                yield clean_item

    @staticmethod
    def _append_unique(items: list[str], value: str) -> None:
        """Append list items without losing insertion order."""
        if value not in items:
            items.append(value)


@lru_cache
def get_excel_parser_service() -> ExcelParserService:
    """Reuse the stateless parser service."""
    return ExcelParserService()
