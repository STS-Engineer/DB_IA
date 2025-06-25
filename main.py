from fastapi import FastAPI, HTTPException
from models import ActionPlan
from db import get_connection

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

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
