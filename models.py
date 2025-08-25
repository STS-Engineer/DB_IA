from pydantic import BaseModel
from typing import List

class ActionStep(BaseModel):
    description: str
    due_date: str  # use ISO format (e.g., 2025-07-01)

class ActionPlan(BaseModel):
    title: str
    owner: str
    deadline: str
    steps: List[ActionStep]

# ----------------------
# Models & helpers for Auditee auth/create
# ----------------------
class AuditeeOut(BaseModel):
    id: int
    first_name: str
    email: EmailStr
    function: Optional[str] = None
    plant_id: Optional[str] = None
    plant_name: Optional[str] = None
    dept_id: Optional[str] = None
    dept_name: Optional[str] = None

class AuthAuditeeOut(BaseModel):
    ok: bool
    today: str
    auditee: Optional[AuditeeOut] = None
    reason: Optional[str] = None

class AuditeeCreateIn(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=120)
    email: EmailStr
    function: Optional[str] = Field(None, max_length=120)
    plant_id: Optional[str] = Field(None, max_length=40)
    plant_name: Optional[str] = Field(None, max_length=120)
    dept_id: Optional[str] = Field(None, max_length=40)
    dept_name: Optional[str] = Field(None, max_length=120)

class AuditeeCreateOut(BaseModel):
    ok: bool
    today: str
    auditee: AuditeeOut

def today_iso():
    from datetime import timezone
    return datetime.now(timezone.utc).date().isoformat()

