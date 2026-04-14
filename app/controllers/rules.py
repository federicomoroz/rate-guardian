from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.rule import Rule
from app.models.repositories.rule_repository import RuleRepository
from app.views.schemas.rule import RuleCreate, RuleResponse

router = APIRouter(prefix="/rules", tags=["Rules"])


@router.post("", response_model=RuleResponse, status_code=201, summary="Create a rate-limit rule")
def create_rule(body: RuleCreate, db: Session = Depends(get_db)):
    rule = Rule(
        name=body.name,
        path_pattern=body.path_pattern,
        limit=body.limit,
        window_seconds=body.window_seconds,
        key_type=body.key_type,
    )
    return RuleRepository.create(db, rule)


@router.get("", response_model=list[RuleResponse], summary="List all rules")
def list_rules(db: Session = Depends(get_db)):
    return RuleRepository.get_all(db)


@router.patch("/{rule_id}/toggle", response_model=RuleResponse, summary="Enable or disable a rule")
def toggle_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = RuleRepository.get_by_id(db, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found.")
    return RuleRepository.set_active(db, rule, not rule.active)


@router.delete("/{rule_id}", status_code=204, summary="Delete a rule")
def delete_rule(rule_id: int, db: Session = Depends(get_db)):
    rule = RuleRepository.get_by_id(db, rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail=f"Rule {rule_id} not found.")
    RuleRepository.delete(db, rule)
