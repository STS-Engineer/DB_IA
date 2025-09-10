from fastapi import FastAPI, HTTPException, File, Form, UploadFile
from pydantic import BaseModel, EmailStr , Field            
from models import (
    ActionPlan,
    AuditeeCreateIn,
    AuditeeCreateOut,
    AuthAuditeeOut,
    today_iso,
    AuditStartIn,          
    QuestionsBulkIn,       
    AnswerIn,              
    NonConformityIn,       
    CompleteAuditIn,       
)
from datetime import datetime , date
from db import get_connection
from fastapi.responses import StreamingResponse
import base64
import io
import mimetypes
import json
import requests

app = FastAPI()

# ----------------------
# Helper: Safe Base64 decode with padding fix
# ----------------------
def safe_b64decode(content: str) -> bytes:
    content = content.strip().replace("\n", "").replace("\r", "")
    missing_padding = len(content) % 4
    if missing_padding:
        content += "=" * (4 - missing_padding)
    return base64.b64decode(content)

# ----------------------
# 1. Route : Création plan d'action
# ----------------------

@app.post("/action-plan")
def store_action_plan(plan: ActionPlan):
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO action_plans (title, owner, deadline)
            VALUES (%s, %s, %s)
            RETURNING id
        """, (plan.title, plan.owner, plan.deadline))
        plan_id = cur.fetchone()[0]

        for step in plan.steps:
            cur.execute("""
                INSERT INTO action_steps (action_plan_id, description, due_date)
                VALUES (%s, %s, %s)
            """, (plan_id, step.description, step.due_date))

        conn.commit()
        return {"status": "success", "action_plan_id": plan_id}

    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to store action plan: {str(e)}")

    finally:
        if conn:
            conn.close()


# ----------------------
# 2. Upload via formulaire (multipart) – pour Postman
# ----------------------

@app.post("/upload-action-file")
async def upload_action_file(
    action_plan_id: int = Form(...),
    file: UploadFile = File(...)
):
    conn = None
    try:
        file_data = await file.read()

        conn = get_connection()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO action_files (action_plan_id, filename, filetype, content, uploaded_at)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            action_plan_id,
            file.filename,
            file.content_type,
            file_data,
            datetime.utcnow()
        ))

        conn.commit()
        return {"status": "file stored", "filename": file.filename}

    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

    finally:
        if conn:
            conn.close()


# ----------------------
# 3. Upload via JSON base64 (depuis ChatGPT)
# ----------------------

class FileUploadPayload(BaseModel):
    action_plan_id: int
    filename: str
    filetype: str
    content: str  # base64

@app.post("/upload-generated-file")
def upload_generated_file(data: FileUploadPayload):
    conn = None
    try:
        file_data = safe_b64decode(data.content)

        if len(file_data) > 10 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="File too large")

        conn = get_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO action_files (action_plan_id, filename, filetype, content, uploaded_at)
            VALUES (%s, %s, %s, %s, %s)
        """, (
            data.action_plan_id,
            data.filename,
            data.filetype,
            file_data,
            datetime.utcnow()
        ))

        conn.commit()
        return {"status": "stored", "filename": data.filename}

    except Exception as e:
        if conn:
            conn.rollback()
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

    finally:
        if conn:
            conn.close()


# ----------------------
# 4. Téléchargement d'un fichier enregistré (depuis la DB)
# ----------------------

@app.get("/download-file-by-id/{file_id}")
def download_file_by_id(file_id: int):
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT filename, filetype, content
            FROM action_files
            WHERE id = %s
        """, (file_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()

        if not row:
            raise HTTPException(status_code=404, detail="Fichier non trouvé")

        filename, filetype, content = row
        return StreamingResponse(
            io.BytesIO(content),
            media_type=filetype or "application/octet-stream",
            headers={"Content-Disposition": f"attachment; filename={filename}"}
        )

    except Exception as e:
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=f"Téléchargement échoué : {str(e)}")

# ----------------------
# 5. 🧺 Debug: Get base64-encoded content from DB and detect type
# ----------------------

@app.get("/debug-file/{file_id}")
def debug_file_base64(file_id: int):
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT filename, filetype, content
            FROM action_files
            WHERE id = %s
        """, (file_id,))
        row = cur.fetchone()
        cur.close()
        conn.close()

        if not row:
            raise HTTPException(status_code=404, detail="Fichier non trouvé")

        filename, filetype, binary_content = row
        encoded_content = base64.b64encode(binary_content).decode("utf-8")

        guessed_type, _ = mimetypes.guess_type(filename)

        return {
            "filename": filename,
            "stored_mimetype": filetype,
            "guessed_mimetype": guessed_type,
            "base64_content": encoded_content
        }

    except Exception as e:
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=f"Erreur debug : {str(e)}")


# ----------------------
# 6. ✨ Upload file to Monday.com file column
# ----------------------

@app.post("/upload-file-to-monday")
async def upload_file_to_monday(
    monday_token: str = Form(...),
    item_id: int = Form(...),
    column_id: str = Form(...),
    file: UploadFile = File(...)
):
    try:
        file_bytes = await file.read()

        query = """
        mutation ($file: File!, $itemId: Int!, $columnId: String!) {
          add_file_to_column (file: $file, item_id: $itemId, column_id: $columnId) {
            id
          }
        }
        """

        operations = {
            "query": query,
            "variables": {
                "file": None,
                "itemId": item_id,
                "columnId": column_id
            }
        }

        files_map = {
            "0": ["variables.file"]
        }

        multipart_data = {
            'operations': (None, json.dumps(operations), 'application/json'),
            'map': (None, json.dumps(files_map), 'application/json'),
            '0': (file.filename, file_bytes, file.content_type)
        }

        response = requests.post(
            "https://api.monday.com/v2/file",
            files=multipart_data,
            headers={"Authorization": monday_token}
        )

        if response.status_code != 200:
            raise HTTPException(status_code=500, detail=f"Monday API error: {response.text}")

        return {"status": "uploaded to Monday", "monday_response": response.json()}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload to Monday failed: {str(e)}")
# ----------------------
# 7. Auth simple: /auth/check (lecture DB name+code)
# ----------------------

class AuthCheckIn(BaseModel):
    name: str
    code: str

class AuthCheckOut(BaseModel):
    ok: bool
    reason: str | None = None  # message générique

@app.post("/auth/check", response_model=AuthCheckOut)
def auth_check(payload: AuthCheckIn):
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()

        # On ne révèle pas si le name existe : réponse générique
        GENERIC_FAIL = {"ok": False, "reason": "Invalid name or code"}

        # Lecture stricte par name
        cur.execute(
            """
            SELECT code, is_active, expires_at
            FROM access_codes
            WHERE name = %s
            LIMIT 1
            """,
            (payload.name,)
        )
        row = cur.fetchone()
        cur.close()
        conn.close()
        conn = None

        if not row:
            return GENERIC_FAIL

        db_code, is_active, expires_at = row

        # Vérifs état / expiration
        if not is_active:
            return {"ok": False, "reason": "Access disabled"}

        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        if expires_at is not None and expires_at <= now:
            return {"ok": False, "reason": "Code expired"}

        # Comparaison en clair (POC)
        if payload.code != db_code:
            return GENERIC_FAIL

        # OK
        return {"ok": True}

    except Exception as e:
        if conn:
            conn.close()
        # On garde 200 pour simplicité côté GPT, mais on peut aussi lever 500
        return {"ok": False, "reason": f"Server error"}
# ----------------------
# 8) GET /auditees/check  (auth by first_name + email)
# ----------------------
@app.get("/auditees/check", response_model=AuthAuditeeOut)
def auditee_check(first_name: str, email: EmailStr):
    """
    - If an auditee exists for this email (case-insensitive): ok=true and return profile
    - If not found: ok=false (assistant will ask for missing fields and call POST /auditees)
    - Also returns 'today' (UTC) so the assistant uses it as the audit date
    """
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT id, first_name, email, "function",
                   plant_id, plant_name, dept_id, dept_name , manager_email
            FROM auditees
            WHERE lower(email) = lower(%s)
            LIMIT 1
        """, (email,))
        row = cur.fetchone()

        if not row:
            cur.close(); conn.close()
            return {"ok": False, "today": today_iso(), "reason": "Not found"}

        (aid, db_first_name, db_email, db_function,
         plant_id, plant_name, dept_id, dept_name, manager_email) = row

        # Cheap sync of preferred first_name if different
        incoming_first = first_name.strip()
        if incoming_first and incoming_first != db_first_name:
            cur.execute("""
                UPDATE auditees
                SET first_name = %s
                WHERE id = %s
            """, (incoming_first, aid))
            conn.commit()
            db_first_name = incoming_first

        cur.close(); conn.close()

        return {
            "ok": True,
            "today": today_iso(),
            "auditee": {
                "id": aid,
                "first_name": db_first_name,
                "email": db_email,
                "function": db_function,
                "plant_id": plant_id,
                "plant_name": plant_name,
                "dept_id": dept_id,
                "dept_name": dept_name,
                "manager_email": manager_email
            }
        }

    except Exception as e:
        if conn:
            conn.close()
        # keep 200 with reason so your assistant handles uniformly
        return {"ok": False, "today": today_iso(), "reason": f"Server error: {e}"}


# ----------------------
# 9) POST /auditees  (create or update full profile)
# ----------------------
@app.post("/auditees", response_model=AuditeeCreateOut, status_code=200)
def create_or_update_auditee(payload: AuditeeCreateIn):
    """
    Upsert rule:
      - If email exists -> update provided fields
      - If not -> insert new row
    Returns the full profile + today's date for the audit.
    """
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()

        # Exists?
        cur.execute("""
            SELECT id FROM auditees
            WHERE lower(email) = lower(%s)
            LIMIT 1
        """, (payload.email,))
        hit = cur.fetchone()

        if hit:
            aid = hit[0]
            cur.execute("""
                UPDATE auditees
                SET first_name = COALESCE(%s, first_name),
                    "function" = COALESCE(%s, "function"),
                    plant_id = COALESCE(%s, plant_id),
                    plant_name = COALESCE(%s, plant_name),
                    dept_id = COALESCE(%s, dept_id),
                    dept_name = COALESCE(%s, dept_name),
                    manager_email = COALESCE(%s, manager_email)
                WHERE id = %s
                RETURNING id, first_name, email, "function",
                          plant_id, plant_name, dept_id, dept_name, manager_email
            """, (
                payload.first_name.strip(),
                (payload.function.strip() if payload.function else None),
                payload.plant_id, payload.plant_name,
                payload.dept_id, payload.dept_name,
                _none_if_blank(getattr(payload, "manager_email", None)),
                aid
            ))
            row = cur.fetchone()
        else:
            cur.execute("""
                INSERT INTO auditees (first_name, email, "function",
                                      plant_id, plant_name, dept_id, dept_name, manager_email)
                VALUES (%s, %s, %s, %s, %s, %s, %s , %s)
                RETURNING id, first_name, email, "function",
                          plant_id, plant_name, dept_id, dept_name, manager_email
            """, (
                payload.first_name.strip(),
                payload.email.strip(),
                (payload.function.strip() if payload.function else None),
                payload.plant_id, payload.plant_name,
                payload.dept_id, payload.dept_name,
                _none_if_blank(getattr(payload, "manager_email", None))
            ))
            row = cur.fetchone()

        conn.commit()
        cur.close(); conn.close()

        (aid, first_name, email, function,
         plant_id, plant_name, dept_id, dept_name) = row

        return {
            "ok": True,
            "today": today_iso(),
            "auditee": {
                "id": aid, "first_name": first_name, "email": email,
                "function": function,
                "plant_id": plant_id, "plant_name": plant_name,
                "dept_id": dept_id, "dept_name": dept_name,
                "manager_email": manager_email
            }
        }

    except Exception as e:
        if conn:
            conn.rollback()
            conn.close()
        raise HTTPException(status_code=500, detail=f"Failed to upsert auditee: {e}")

@app.post("/audits/start")
def audit_start(payload: AuditStartIn):
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO audits (auditee_id, type, questionnaire_version, external_id, status, started_at)
            VALUES (%s, %s, %s, %s, 'in_progress', now())
            RETURNING id, auditee_id, type, status, started_at, questionnaire_version, score_global
        """, (
            payload.auditee_id, payload.type, payload.questionnaire_version, payload.external_id
        ))

        row = cur.fetchone()
        conn.commit()
        cur.close(); conn.close(); conn = None

        (aid, auditee_id, atype, status, started_at, qv, score) = row
        return {
            "id": aid,
            "auditee_id": auditee_id,
            "type": atype,
            "status": status,
            "started_at": started_at,
            "questionnaire_version": qv,
            "score_global": float(score) if score is not None else None
        }

    except Exception as e:
        if conn:
            conn.rollback(); conn.close()
        raise HTTPException(status_code=500, detail=f"Failed to start audit: {e}")

@app.post("/questions/bulk")
def questions_bulk_upsert(payload: QuestionsBulkIn):
    """
    For each question in order, if (version_tag, text) exists => return existing question_id,
    else insert and return the new question_id. Response order matches input order.
    """
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        out_items = []

        for idx, q in enumerate(payload.questions):
            # 1) try to find existing
            cur.execute("""
                SELECT question_id FROM questions
                 WHERE version_tag = %s AND text = %s
                 LIMIT 1
            """, (payload.version_tag, q.text))
            hit = cur.fetchone()

            if hit:
                qid = hit[0]
            else:
                # 2) insert
                cur.execute("""
                    INSERT INTO questions (text, category, mandatory, source_doc, version_tag, created_at)
                    VALUES (%s, %s, %s, %s, %s, now())
                    RETURNING question_id
                """, (q.text, q.category, q.mandatory, q.source_doc, payload.version_tag))
                qid = cur.fetchone()[0]

            out_items.append({"index": idx, "question_id": qid})

        conn.commit()
        cur.close(); conn.close(); conn = None
        return {"ok": True, "version_tag": payload.version_tag, "items": out_items}

    except Exception as e:
        if conn:
            conn.rollback(); conn.close()
        raise HTTPException(status_code=500, detail=f"Failed to upsert questions: {e}")

@app.post("/audits/{audit_id}/answers")
def save_answer(audit_id: int, payload: AnswerIn):
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()

        # try update (unique key: audit_id, question_id, attempt_number)
        cur.execute("""
            UPDATE answers
               SET response_text = %s,
                   is_compliant  = %s,
                   evidence_url  = %s
             WHERE audit_id = %s AND question_id = %s AND attempt_number = %s
         RETURNING answer_id
        """, (
            payload.response_text, payload.is_compliant, payload.evidence_url,
            audit_id, payload.question_id, payload.attempt_number
        ))
        row = cur.fetchone()

        if not row:
            cur.execute("""
                INSERT INTO answers (audit_id, question_id, response_text, is_compliant, attempt_number, evidence_url, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, now())
                RETURNING answer_id
            """, (
                audit_id, payload.question_id, payload.response_text,
                payload.is_compliant, payload.attempt_number, payload.evidence_url
            ))
            row = cur.fetchone()

        conn.commit()
        answer_id = row[0]
        cur.close(); conn.close(); conn = None
        return {"ok": True, "answer_id": answer_id}

    except Exception as e:
        if conn:
            conn.rollback(); conn.close()
        raise HTTPException(status_code=500, detail=f"Failed to save answer: {e}")

@app.post("/audits/{audit_id}/nonconformities")
def save_nc(audit_id: int, payload: NonConformityIn):
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO non_conformities (
                audit_id, question_id, description, severity, status,
                responsible_id, due_date, evidence_url, closed_at, closure_comment, detected_at
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, now())
            RETURNING nc_id
        """, (
            audit_id, payload.question_id, payload.description, payload.severity, payload.status,
            payload.responsible_id, payload.due_date, payload.evidence_url, payload.closed_at, payload.closure_comment
        ))
        nc_id = cur.fetchone()[0]
        conn.commit()
        cur.close(); conn.close(); conn = None
        return {"ok": True, "nc_id": nc_id}

    except Exception as e:
        if conn:
            conn.rollback(); conn.close()
        raise HTTPException(status_code=500, detail=f"Failed to save non-conformity: {e}")
@app.post("/audits/{audit_id}/complete")
def complete_audit(audit_id: int, payload: CompleteAuditIn):
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()

        score_value = payload.score_global

        if score_value is None:
            # compute % compliant questions: any attempt true per question
            cur.execute("""
                WITH per_q AS (
                  SELECT question_id, bool_or(is_compliant) AS compliant
                    FROM answers
                   WHERE audit_id = %s
                GROUP BY question_id
                )
                SELECT
                  COALESCE(SUM(CASE WHEN compliant THEN 1 ELSE 0 END),0)::float,
                  COALESCE(COUNT(*),0)::float
                FROM per_q
            """, (audit_id,))
            srow = cur.fetchone()
            numer, denom = (srow or (0.0, 0.0))
            score_value = round((numer / denom) * 100.0, 2) if denom > 0 else 0.0

        cur.execute("""
            UPDATE audits
               SET status = 'completed',
                   ended_at = now(),
                   score_global = %s
             WHERE id = %s
         RETURNING id, status, ended_at, score_global
        """, (score_value, audit_id))
        row = cur.fetchone()
        conn.commit()

        if not row:
            cur.close(); conn.close()
            raise HTTPException(status_code=404, detail="Audit not found")

        (aid, status, ended_at, score_global) = row
        cur.close(); conn.close(); conn = None
        return {
            "id": aid,
            "status": status,
            "ended_at": ended_at,
            "score_global": float(score_global) if score_global is not None else None
        }

    except Exception as e:
        if conn:
            conn.rollback(); conn.close()
        raise HTTPException(status_code=500, detail=f"Failed to complete audit: {e}")

