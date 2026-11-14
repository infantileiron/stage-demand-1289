from fastapi import HTTPException
from src.models.scenario import Scenario
from src.utils.database import list_rows, get_row, insert_row
from src.services.dataset import detail
from src.services.linked_forecast import VENUES as LINKED_VENUES

def list_scenarios(): return list_rows(Scenario.table)
def create(body):
    data=body.model_dump()
    source=detail(data["dataset_id"])
    if data["mode"]=="linked":
        missing=[venue for venue in LINKED_VENUES
                 if len([r for r in source["rows"] if r["venue"]==venue])<70]
        if missing: raise HTTPException(422,f"联动预演要求三厅历史均不少于70天，历史不足：{'、'.join(missing)}")
    elif len([r for r in source["rows"] if r["venue"]==data["venue"]])<70:
        raise HTTPException(422,"所选厅至少需要70天观测")
    return insert_row(Scenario.table,data["name"],Scenario(**data).payload())
