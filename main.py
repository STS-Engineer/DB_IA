from fastapi import FastAPI, HTTPException, File, Form, UploadFile
from pydantic import BaseModel
from models import ActionPlan
from datetime import datetime
from db import get_connection
from fastapi.responses import StreamingResponse
import base64
import io
import mimetypes

app = FastAPI()

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
        file_data = base64.b64decode(data.content)

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
# 5. 🧪 Debug: Get base64-encoded content from DB and detect type
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
