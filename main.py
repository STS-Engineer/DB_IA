from fastapi import FastAPI, HTTPException, File, Form UploadFile, Header
from models import ActionPlan
from datetime import datetime
from db import get_connection
import shutil


app = FastAPI()

@app.post("/action-plan")
def store_action_plan(plan: ActionPlan):
    try:
        conn = get_connection()
        cur = conn.cursor()

        # Insert into action_plans table
        cur.execute("""
            INSERT INTO action_plans (title, owner, deadline)
            VALUES (%s, %s, %s)
            RETURNING id
        """, (plan.title, plan.owner, plan.deadline))
        plan_id = cur.fetchone()[0]

        # Insert steps
        for step in plan.steps:
            cur.execute("""
                INSERT INTO action_steps (action_plan_id, description, due_date)
                VALUES (%s, %s, %s)
            """, (plan_id, step.description, step.due_date))

        conn.commit()
        cur.close()
        conn.close()
        return {"status": "success", "action_plan_id": plan_id}

@app.post("/upload-action-file")
async def upload_action_file(
    action_plan_id: int = Form(...),
    file: UploadFile = File(...),
    x_chatgpt_token: str = Header(None)
):
    if x_chatgpt_token != ALLOWED_ASSISTANT_TOKEN:
        raise HTTPException(status_code=403, detail="Unauthorized sender")

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
        cur.close()
        conn.close()

        return {"status": "file stored", "filename": file.filename}


    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
