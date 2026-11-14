from pydantic import BaseModel, Field
from typing import Literal

class PlanCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    run_id: int = Field(gt=0)
    venue: Literal["大剧场", "黑匣子", "实验厅"] | None = None
