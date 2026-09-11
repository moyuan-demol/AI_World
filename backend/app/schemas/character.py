from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from app.schemas.common import ORMModel


class CharacterBase(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    role: str = Field(default="", max_length=128)
    personality: str = ""
    expertise: str = ""
    speaking_style: str = Field(default="", max_length=255)
    system_prompt: str = ""


class CharacterCreate(CharacterBase):
    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("角色名称不能为空")
        return value


class CharacterUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    role: str | None = None
    personality: str | None = None
    expertise: str | None = None
    speaking_style: str | None = None
    system_prompt: str | None = None


class CharacterOut(ORMModel):
    id: int
    name: str
    role: str
    personality: str
    expertise: str
    speaking_style: str
    system_prompt: str
    created_time: datetime | None = None
