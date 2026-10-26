from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ColumnRule(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: Literal['text', 'email', 'integer', 'number', 'date'] = 'text'
    required: bool = False
    lowercase: bool = False
    minimum: float | None = Field(default=None, allow_inf_nan=False)
    maximum: float | None = Field(default=None, allow_inf_nan=False)

    @model_validator(mode='after')
    def check_bounds(self):
        if self.minimum is not None and self.maximum is not None:
            if self.minimum > self.maximum:
                raise ValueError('minimum must not exceed maximum')
        if self.kind not in ('integer', 'number') and (self.minimum is not None or self.maximum is not None):
            raise ValueError('bounds are only supported for numeric columns')
        return self


class Rules(BaseModel):
    columns: list[ColumnRule] = Field(default_factory=list, max_length=100)
    trim_whitespace: bool = True
    deduplicate_by: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def unique_names(self):
        names = [rule.name for rule in self.columns]
        if len(names) != len(set(names)):
            raise ValueError('each column can only have one rule')
        if len(self.deduplicate_by) != len(set(self.deduplicate_by)):
            raise ValueError('deduplicate_by contains repeated columns')
        return self
