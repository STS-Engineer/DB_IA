from fastapi import FastAPI, HTTPException, File, Form, UploadFile , Query
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
    ObjectionOut,
    MatrixOut,
    AuditeePrecheckIn ,
    AuditeePrecheckOut ,
    FileUploadPayload ,
    AuthCheckIn,
    AuthCheckOut

)
from datetime import datetime , date
from db import get_connection , get_connection_sales
from fastapi.responses import StreamingResponse
import base64
import io
import mimetypes
import json
import requests
import psycopg2.extras
import uuid

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
       
# ------------------------------------------------------------------------------------------------
# 11) GET /auditees/precheck  (auth by first_name + email)
# ------------------------------------------------------------------------------------------------
@app.post("/auditees/precheck", response_model=AuditeePrecheckOut, status_code=200)
def auditee_precheck(payload: AuditeePrecheckIn):
    """
    Step A: Profile Pre-Check.
    - Input: first_name + email
    - If auditee exists:
        * Return profile.
        * Flag profile_incomplete if plant/dept are missing.
    - If not exists:
        * exists=false → client should collect full profile and call /auditees.
    """
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT id, first_name, email, "function",
                   plant_name, dept_name, manager_email
            FROM auditees
            WHERE lower(email) = lower(%s)
            LIMIT 1
        """, (payload.email,))
        row = cur.fetchone()

        if not row:
            cur.close(); conn.close()
            return {
                "ok": True,
                "today": today_iso(),
                "exists": False,
                "reason": "No profile was found for this email."
            }

        (
            aid, db_first_name, db_email, db_function,
            plant_name, dept_name, manager_email
        ) = row

        incoming_first = payload.first_name.strip()
        if incoming_first and incoming_first != db_first_name:
            cur.execute("""
                UPDATE auditees
                SET first_name = %s
                WHERE id = %s
            """, (incoming_first, aid))
            conn.commit()
            db_first_name = incoming_first

        cur.close(); conn.close()

        # Profile completeness check
        profile_incomplete = not (first_name and email)

        return {
            "ok": True,
            "today": today_iso(),
            "exists": True,
            "profile_incomplete": profile_incomplete,
            "auditee": {
                "id": aid,
                "first_name": db_first_name,
                "email": db_email,
                "function": db_function,
                "plant_name": plant_name,
                "dept_name": dept_name,
                "manager_email": manager_email,
            },
        }

    except Exception as e:
        if conn:
            conn.close()
        return {
            "ok": False,
            "today": today_iso(),
            "exists": False,
            "reason": f"Server error: {e}"
        }
# ------------------------------------------------------------------------------------------------
# 8) GET /auditees/check  (auth by first_name + email)
# ------------------------------------------------------------------------------------------------
@app.get("/auditees/check", response_model=AuthAuditeeOut)
def auditee_check(first_name: str, email: EmailStr, code: str):
    """
    Auth: first_name (case-insensitive) + email (case-insensitive) + code (exact)
    - If match: ok=true and return profile
    - If not found or first_name mismatch: ok=false with reason
    - Always returns 'today' (UTC) for assistant to use as audit date
    """
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()

        # 1) Find by email + code
        cur.execute("""
            SELECT id, first_name, email, "function",
                   plant_name, dept_name, manager_email, code
            FROM auditees
            WHERE lower(email) = lower(%s)
              AND code = %s
            LIMIT 1
        """, (email, code))
        row = cur.fetchone()

        if not row:
            cur.close(); conn.close()
            return {
                "ok": False,
                "today": today_iso(),
                "reason": "Not found for provided email+code"
            }

        (aid, db_first_name, db_email, db_function,
        plant_name, dept_name, manager_email, db_code) = row

        # 2) Verify first_name (case-insensitive)
        incoming_first = (first_name or "").strip()
        if not incoming_first or incoming_first.casefold() != (db_first_name or "").strip().casefold():
            cur.close(); conn.close()
            return {
                "ok": False,
                "today": today_iso(),
                "reason": "First name does not match this email+code"
            }

        # 3) Optional: cheap sync display of first_name (e.g., capitalization/spacing)
        if incoming_first != db_first_name:
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
                "plant_name": plant_name,
                "dept_name": dept_name,
                "manager_email": manager_email,
                "code": db_code,
            }
        }

    except Exception as e:
        if conn:
            conn.close()
        # Keep 200 so the assistant handles uniformly
        return {"ok": False, "today": today_iso(), "reason": f"Server error: {e}"}


# ----------------------
# 9) POST /auditees  (create or update full profile)
# ----------------------
@app.post("/auditees", response_model=AuditeeCreateOut, status_code=200)
def create_or_update_auditee(payload: AuditeeCreateIn):
    """
    Upsert rule:
      - If email exists -> update provided fields (non-null keep existing via COALESCE)
      - If not -> insert new row
    Returns the full profile + today's date for the audit.
    """
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()

        # Normalize inputs (preserve first_name spelling, just trim spaces)
        email_val = payload.email.strip()
        first_name_val = payload.first_name.strip()
        function_val = payload.function.strip() if payload.function else None
        plant_name_val = payload.plant_name.strip() if payload.plant_name else None
        dept_name_val = payload.dept_name.strip() if payload.dept_name else None
        manager_email_val = payload.manager_email.strip() if payload.manager_email else None

        # Exists?
        cur.execute(
            """
            SELECT id FROM auditees
            WHERE lower(email) = lower(%s)
            LIMIT 1
            """,
            (email_val,),
        )
        hit = cur.fetchone()

        if hit:
            aid = hit[0]
            cur.execute(
                """
                UPDATE auditees
                SET first_name    = COALESCE(%s, first_name),
                    "function"    = COALESCE(%s, "function"),
                    plant_name    = COALESCE(%s, plant_name),
                    dept_name     = COALESCE(%s, dept_name),
                    manager_email = COALESCE(%s, manager_email)
                WHERE id = %s
                RETURNING id, first_name, email, "function",
                          plant_name, dept_name, manager_email
                """,
                (
                    first_name_val,
                    function_val,
                    plant_name_val,
                    dept_name_val,
                    manager_email_val, 
                    aid,               
                ),
            )
            row = cur.fetchone()
        else:
            cur.execute(
                """
                INSERT INTO auditees (
                    first_name, email, "function",
                    plant_name, dept_name, manager_email
                )
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id, first_name, email, "function",
                          plant_name,  dept_name, manager_email
                """,
                (
                    first_name_val,
                    email_val,
                    function_val,
                    plant_name_val,
                    dept_name_val,
                    manager_email_val,  # <-- was missing
                ),
            )
            row = cur.fetchone()

        conn.commit()
        cur.close()
        conn.close()

        (
            aid,
            first_name,
            email,
            function,
            plant_name,
            dept_name,
            manager_email,  # <-- include this in unpack
        ) = row

        return {
            "ok": True,
            "today": today_iso(),
            "auditee": {
                "id": aid,
                "first_name": first_name,
                "email": email,
                "function": function,
                "plant_name": plant_name,
                "dept_name": dept_name,
                "manager_email": manager_email,
            },
        }

    except Exception:
        if conn:
            conn.rollback()
            conn.close()
        raise HTTPException(status_code=500, detail="Failed to upsert auditee.")

@app.get("/audits/{audit_id}/answers")
def get_answers(audit_id: int):
    """
    Get all answers for a given audit_id, linked with the auditee.
    """
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()

        # join answers with audits to fetch auditee info
        cur.execute("""
            SELECT a.answer_id,
                   a.audit_id,
                   q.text AS question_text,
                   a.response_text,
                   a.is_compliant,
                   a.attempt_number,
                   a.evidence_url,
                   a.created_at,
                   au.auditee_id,
                   au.auditee_name
            FROM answers a
            JOIN audits au ON a.audit_id = au.audit_id
            JOIN questions q ON a.question_id = q.id
            WHERE a.audit_id = %s
            ORDER BY q.id, a.attempt_number
        """, (audit_id,))
        rows = cur.fetchall()
        cur.close(); conn.close(); conn = None

        answers = []
        for r in rows:
            answers.append({
                "answer_id": r[0],
                "audit_id": r[1],
                "question_text": r[2],
                "response_text": r[3],
                "is_compliant": r[4],
                "attempt_number": r[5],
                "evidence_url": r[6],
                "created_at": r[7].isoformat() if r[7] else None,
                "auditee_id": r[8],
                "auditee_name": r[9],
            })

        return {"ok": True, "audit_id": audit_id, "count": len(answers), "answers": answers}

    except Exception as e:
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=f"Failed to fetch answers: {e}")


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

# ----------------------
# 10) GET /objections                                      
# ----------------------

@app.get("/objections", response_model=list[ObjectionOut])
def get_objections(
    category: str | None = Query(None, description="Filter by category (e.g. 'Lead Time', 'MOQ')"),
    q: str | None = Query(None, description="Full-text search in concern/argument/response"),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    conn = None
    try:
        conn = get_connection_sales()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        sql = """
            SELECT id, customer_concern, example_customer_argument, recommended_response, category
            FROM customer_objection_handling
            WHERE 1=1
        """
        params: list = []

        if category:
            sql += " AND category = %s"
            params.append(category)

        if q:
            like = f"%{q}%"
            sql += """
                AND (
                    customer_concern ILIKE %s OR
                    example_customer_argument ILIKE %s OR
                    recommended_response ILIKE %s
                )
            """
            params.extend([like, like, like])

        sql += " ORDER BY id LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close(); conn.close()
        return rows

    except Exception as e:
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=f"Failed to fetch objections: {e}")
# ----------------------
# 9) GET / matrix                                      
# ----------------------

@app.get("/matrix", response_model=list[MatrixOut])
def get_matrix(
    freeze_time_respected: bool | None = Query(None, description="true or false"),
    demand_vs_moq: str | None = Query(None, description="e.g. '> MOQ' or '< MOQ'"),
    inventory_vs_demand: str | None = Query(None, description="e.g. 'No stock', 'Exact match'"),
    limit: int = Query(200, ge=1, le=2000),
    offset: int = Query(0, ge=0),
):
    conn = None
    try:
        conn = get_connection_sales()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        sql = """
            SELECT id, freeze_time_respected, demand_vs_moq, inventory_vs_demand, recommended_strategy
            FROM customer_handling_matrix
            WHERE 1=1
        """
        params: list = []

        if freeze_time_respected is not None:
            sql += " AND freeze_time_respected = %s"
            params.append(freeze_time_respected)

        if demand_vs_moq:
            sql += " AND demand_vs_moq = %s"
            params.append(demand_vs_moq)

        if inventory_vs_demand:
            sql += " AND inventory_vs_demand = %s"
            params.append(inventory_vs_demand)

        sql += " ORDER BY id LIMIT %s OFFSET %s"
        params.extend([limit, offset])

        cur.execute(sql, params)
        rows = cur.fetchall()
        cur.close(); conn.close()
        return rows

    except Exception as e:
        if conn:
            conn.close()
        raise HTTPException(status_code=500, detail=f"Failed to fetch matrix: {e}")


