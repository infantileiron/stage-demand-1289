from fastapi import HTTPException
from src.models.plan import Plan
from src.constants.enums import ReviewStatus,RunStatus
from src.utils.database import list_rows,get_row,insert_row,update_row

def list_plans(): return list_rows(Plan.table)

def _linked_days(result, venue):
    match = next((vf for vf in result.get("venue_forecasts", []) if vf["venue"] == venue), None)
    if match is None:
        raise HTTPException(422, "联动预演只包含大剧场、黑匣子、实验厅，请指定其中一厅")
    return match["forecast"]

def create(body):
    run=get_row("runs",body.run_id)
    if run["status"]!=RunStatus.COMPLETED.value: raise HTTPException(409,"仅已完成预测可生成排班建议")
    result=run["result"]
    scenario=get_row("scenarios",run["scenario_id"])
    if result.get("mode")=="linked":
        venue=body.venue or scenario.get("venue")
        if not venue: raise HTTPException(422,"联动预演生成排班建议时需指定场馆")
        days=_linked_days(result,venue)
    else:
        venue=scenario["venue"]
        days=result["forecast"]
    return insert_row(Plan.table,body.name,Plan(name=body.name,run_id=run["id"],venue=venue,days=days,status=ReviewStatus.DRAFT.value,approved_by=None).payload())

def approve(ident,user):
    plan=get_row(Plan.table,ident)
    if plan["status"]==ReviewStatus.APPROVED.value: return plan
    return update_row(Plan.table,ident,{"status":ReviewStatus.APPROVED.value,"approved_by":user["email"]})
