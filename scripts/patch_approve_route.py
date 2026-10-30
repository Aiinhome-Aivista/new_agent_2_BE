with open("new_agent_2_BE/api/routes/baseline.py", "r", encoding="utf-8") as f:
    code = f.read()

old_approve = """@router.post("/approve")
def approve_baseline(project_id: int, current_user: dict = Depends(require_roles(["ENGAGEMENT_MANAGER", "ADMIN"])), db: mysql.connector.connection.MySQLConnection = Depends(get_db)):
    verify_project_access(project_id, current_user, db)
    
    baseline = BaselineRepository.get_draft_baseline(db, project_id)
    if not baseline:
        raise HTTPException(status_code=404, detail="No draft baseline found")
        
    BaselineRepository.approve_baseline(db, baseline["id"], current_user["id"])
    BaselineRepository.update_project_monitoring_status(db, project_id, 'ACTIVE')
    db.commit()
    
    return {"success": True, "message": "Baseline approved. Project is now ACTIVE."}"""

new_approve = """@router.post("/approve")
def approve_baseline(
    project_id: int,
    allow_override: bool = Query(False),
    current_user: dict = Depends(require_roles(["ENGAGEMENT_MANAGER", "ADMIN"])),
    db: mysql.connector.connection.MySQLConnection = Depends(get_db)
):
    verify_project_access(project_id, current_user, db)
    
    baseline = BaselineRepository.get_draft_baseline(db, project_id)
    if not baseline:
        raise HTTPException(status_code=404, detail="No draft baseline found")
        
    try:
        BaselineRepository.approve_baseline(db, baseline["id"], current_user["id"], allow_override=allow_override)
    except ValueError as val_err:
        raise HTTPException(status_code=422, detail=str(val_err))
        
    BaselineRepository.update_project_monitoring_status(db, project_id, 'ACTIVE')
    db.commit()
    
    return {"success": True, "message": "Baseline approved. Project is now ACTIVE."}"""

code_norm = code.replace("\r\n", "\n")
old_norm = old_approve.replace("\r\n", "\n")

if old_norm in code_norm:
    code_norm = code_norm.replace(old_norm, new_approve)
    with open("new_agent_2_BE/api/routes/baseline.py", "w", encoding="utf-8") as f:
        f.write(code_norm)
    print("approve_baseline route patched successfully!")
else:
    print("Could not find old_approve in baseline.py!")

