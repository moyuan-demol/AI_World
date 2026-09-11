from pydantic import BaseModel, ConfigDict


class ORMModel(BaseModel):
    """Base schema able to read SQLAlchemy model attributes."""

    model_config = ConfigDict(from_attributes=True)
