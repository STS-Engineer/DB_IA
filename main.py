from fastapi import FastAPI, HTTPException, File, Form, UploadFile
from pydantic import BaseModel
from models import ActionPlan
from datetime import datetime
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
