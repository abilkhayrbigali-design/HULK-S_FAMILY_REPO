"""Strict input contracts for persisted conversations and checklist snapshots."""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StringConstraints,
    model_validator,
)

from app.schemas.urls import valid_http_url

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Number = Annotated[StrictInt | StrictFloat, Field(ge=0, allow_inf_nan=False)]
Language = Literal["RU", "KZ", "EN"]
Channel = Literal["agent chat", "voice call"]


class Schema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScaleStep(Schema):
    value: Number
    description: Text


class Criterion(Schema):
    id: Text
    name: Text
    instruction: Text
    type: Literal["binary", "scale", "flag"]
    weight: Number
    allow_na: StrictBool = False
    na_rule: Text | None = None
    scale: list[ScaleStep] | None = None
    section: str | None = None

    @model_validator(mode="after")
    def validate_definition(self):
        if self.allow_na != (self.na_rule is not None):
            raise ValueError("N/A requires a rule; disabled N/A requires null")
        if self.type == "flag":
            if self.weight != 0 or self.allow_na:
                raise ValueError("Flags require zero weight and no N/A")
        elif self.weight <= 0:
            raise ValueError("Scored criteria require positive weight")
        if self.type == "scale":
            values = [step.value for step in self.scale or []]
            if len(values) < 2 or len(set(values)) != len(values) or max(values) <= 0:
                raise ValueError("Scale requires distinct nonnegative steps and positive maximum")
        elif self.scale is not None:
            raise ValueError("Only scale criteria may define steps")
        return self


class CriteriaInput(Schema):
    criteria: Annotated[list[Criterion], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_criteria(self):
        ids = [c.id for c in self.criteria]
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate criterion IDs")
        if not any(c.type != "flag" for c in self.criteria):
            raise ValueError("At least one scored criterion is required")
        return self


class ChecklistCreate(CriteriaInput):
    name: Text


class ChecklistUpdate(CriteriaInput):
    expected_version_number: Annotated[StrictInt, Field(ge=1)]


class OrganizerMessage(Schema):
    timestamp: str
    speaker: Literal["client", "operator", "bot", "system"]
    # Keep original text and timestamps byte-for-byte, including whitespace.
    text: Annotated[str, Field(min_length=1)]


class OrganizerRecord(Schema):
    conversation_id: Text
    channel: Channel
    language: Language
    transcript: Annotated[list[OrganizerMessage], Field(min_length=1)]
    audio_url: str | None = None

    @model_validator(mode="after")
    def validate_audio(self):
        if self.channel == "agent chat" and self.audio_url is not None:
            raise ValueError("Chat audio_url must be null")
        if self.audio_url is not None and not valid_http_url(self.audio_url):
            raise ValueError("Invalid audio URL")
        return self


class ImportRequest(Schema):
    records: Annotated[list[OrganizerRecord], Field(min_length=1)]

    @model_validator(mode="after")
    def unique_records(self):
        ids = [record.conversation_id for record in self.records]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate conversation IDs in payload")
        return self


class DatasetImport(Schema):
    group_id: Text
