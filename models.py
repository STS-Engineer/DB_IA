from pydantic import BaseModel, EmailStr, Field
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
    plant_id: Optional[str] = None
    plant_name: Optional[str] = None
    dept_id: Optional[str] = None
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
    plant_id: Optional[str] = Field(None, max_length=40)
    plant_name: Optional[str] = Field(None, max_length=120)
    dept_id: Optional[str] = Field(None, max_length=40)
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
# ----------------------
# Models for Conversation get and post       
# ----------------------
class ConversationIn(BaseModel):
    # Which assistant (user picked from the main menu)
    assistant_id: int = Field(..., ge=1, le=6)

    # Prompt identity (optional metadata you send from the client)
    prompt_name: Optional[str] = None          # e.g., "Write mail.docx"
    prompt_version: Optional[str] = None       # e.g., "v2025-10-06" or "sha256:..."
    prompt_notes: Optional[str] = None

    # Session identity (read-only; do not ask user)
    ui_lang: Optional[str] = None              # EN/FR/AR...
    person_name: Optional[str] = None          # preserve exact spelling
    person_email: Optional[EmailStr] = None
    session_id: Optional[str] = None

    # Core payloads
    conversation: Dict[str, Any] = Field(..., description="Turns/checkpoints/validations JSON")
    summary: Optional[Dict[str, Any]] = None   # key takeaways in ui_lang
    outputs: Optional[Dict[str, Any]] = None   # generated artifacts (email draft, etc.)

    # Status & health
    status: Optional[str] = Field("active", pattern="^(active|completed|aborted|error)$")
    error_count: Optional[int] = 0
    last_error: Optional[str] = None

    # Runtime
    completed_at: Optional[datetime] = None

    # Generic metadata
    meta: Optional[Dict[str, Any]] = Field(default_factory=dict)


class ConversationOut(BaseModel):
    id: str
    assistant_id: int
    assistant_name: str

    prompt_name: Optional[str]
    prompt_version: Optional[str]
    prompt_notes: Optional[str]

    ui_lang: Optional[str]
    person_name: Optional[str]
    person_email: Optional[str]
    session_id: Optional[str]

    started_at: datetime
    completed_at: Optional[datetime]

    conversation: Dict[str, Any]
    summary: Optional[Dict[str, Any]]
    outputs: Optional[Dict[str, Any]]

    status: str
    error_count: int
    last_error: Optional[str]

    meta: Dict[str, Any]
