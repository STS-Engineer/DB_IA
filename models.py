from pydantic import BaseModel, EmailStr, Field, root_validator
from typing import List , Optional , Literal , Any, Dict
from datetime import datetime, timezone , date 


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
class AuditeePrecheckIn(BaseModel):
    first_name: str
    email: EmailStr

class AuditeePrecheckOut(BaseModel):
    ok: bool
    today: str
    exists: bool
    profile_incomplete: Optional[bool] = None
    auditee: Optional[AuditeeOut] = None
    reason: Optional[str] = None
    
class AuditeeOut(BaseModel):
    id: int
    first_name: str
    email: EmailStr
    function: Optional[str] = None
    plant_name: Optional[str] = None
    dept_name: Optional[str] = None
    manager_email: Optional[EmailStr] = None
   

class AuthAuditeeOut(BaseModel):
    ok: bool
    today: str
    auditee: Optional[AuditeeOut] = None
    reason: Optional[str] = None

class AuditeeCreateIn(BaseModel):
    first_name: str = Field(..., min_length=1, max_length=120)
    email: EmailStr
    function: Optional[str] = Field(None, max_length=120)
    plant_name: Optional[str] = Field(None, max_length=120)
    dept_name: Optional[str] = Field(None, max_length=120)
    manager_email: Optional[EmailStr] = None

class AuditeeCreateOut(BaseModel):
    ok: bool
    today: str
    auditee: AuditeeOut

def today_iso():
    return datetime.now(timezone.utc).date().isoformat()

class AuditStartIn(BaseModel):
    auditee_id: int
    type: str
    questionnaire_version: Optional[str] = None
    external_id: Optional[str] = None  # uuid from client for idempotency

class QuestionIn(BaseModel):
    text: str
    category: Optional[str] = None
    mandatory: Optional[bool] = True
    source_doc: Optional[str] = None

class QuestionsBulkIn(BaseModel):
    version_tag: str
    questions: List[QuestionIn]

class AnswerIn(BaseModel):
    question_id: int
    response_text: Optional[str] = ""
    is_compliant: Optional[bool] = None
    attempt_number: int = Field(1, ge=1, le=2)
    evidence_url: Optional[str] = None

class NonConformityIn(BaseModel):
    question_id: int
    description: str
    severity: Literal["minor","major","critical"] = "major"
    status: Literal["open","in_progress","closed"] = "open"
    responsible_id: Optional[int] = None
    due_date: Optional[date] = None
    evidence_url: Optional[str] = None
    closed_at: Optional[datetime] = None
    closure_comment: Optional[str] = None

class CompleteAuditIn(BaseModel):
    score_global: Optional[float] = None

class FileUploadPayload(BaseModel):
    action_plan_id: int
    filename: str
    filetype: str
    content: str  # base64

class AuthCheckIn(BaseModel):
    name: str
    code: str

class AuthCheckOut(BaseModel):
    ok: bool
    reason: str | None = None

# -------------------------------------------------
# Models & helpers for Sales
# -------------------------------------------------
class ObjectionOut(BaseModel):
    id: int
    customer_concern: str
    example_customer_argument: str
    recommended_response: str
    category: Optional[str] = None

    class Config:
        orm_mode = True

class MatrixOut(BaseModel):
    id: int
    freeze_time_respected: bool
    demand_vs_moq: str
    inventory_vs_demand: str
    recommended_strategy: str

    class Config:
        orm_mode = True

# -------------------------------------------------
# Conversations Models
# -------------------------------------------------

class ConversationIn(BaseModel):
    user_name: str = Field(..., min_length=1, max_length=200)
    conversation: str = Field(..., min_length=1)
    date_conversation: Optional[datetime] = None
    assistant_name: Optional[str] = None

    @root_validator(pre=True)
    def _defaults_and_trim(cls, values):
        if not values.get("date_conversation"):
            values["date_conversation"] = datetime.now(timezone.utc)  # ✅ aware UTC
        if "user_name" in values and isinstance(values["user_name"], str):
            values["user_name"] = values["user_name"].strip()
        if "conversation" in values and isinstance(values["conversation"], str):
            values["conversation"] = values["conversation"].strip()
        if "assistant_name" in values and isinstance(values["assistant_name"], str):
            values["assistant_name"] = values["assistant_name"].strip() or None
        return values

class ConversationOut(BaseModel):
    id: int
    status: str = "ok"

class ConversationSummary(BaseModel):
    id: int
    user_name: str
    date_conversation: datetime
    preview: str
    assistant_name: Optional[str] = None

class ConversationDetail(BaseModel):
    id: int
    user_name: str
    date_conversation: datetime
    conversation: str
    assistant_name: Optional[str] = None

class ConversationsListOut(BaseModel):
    items: List[ConversationSummary]
    total: int

# Small helper you’ll use in the list endpoint
def build_preview(text: str, max_len: int = 140) -> str:
    t = (text or "").strip().replace("\n", " ").replace("\r", " ")
    t = " ".join(t.split())
    return (t[:max_len] + "…") if len(t) > max_len else t
