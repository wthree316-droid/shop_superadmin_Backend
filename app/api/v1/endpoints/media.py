import uuid
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from app.api import deps
from app.models.user import User, UserRole
from app.core.config import settings
from supabase import create_client

router = APIRouter()

# --- 1. ดึงรายชื่อรูป (GET) ---
@router.get("/flags")
def get_flag_library(
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.admin, UserRole.superadmin]:
        raise HTTPException(status_code=403, detail="Not authorized")

    try:
        supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        files = supabase.storage.from_("lotto_images").list("flags", {"limit": 100, "offset": 0, "sortBy": {"column": "created_at", "order": "desc"}})
        
        flags = []
        for file in files:
            if file['name'].startswith('.'): continue
            
            path = f"flags/{file['name']}"
            public_url_res = supabase.storage.from_("lotto_images").get_public_url(path)
            final_url = public_url_res if isinstance(public_url_res, str) else public_url_res.get('publicUrl')

            flags.append({
                "name": file['name'],
                "url": final_url
            })
            
        return flags
    except Exception as e:
        print(f"Media Error: {e}")
        return []

# --- 2. อัปโหลดรูปเพิ่ม (POST) ---
@router.post("/flags")
async def upload_flag(
    file: UploadFile = File(...),
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.admin, UserRole.superadmin]:
        raise HTTPException(status_code=403, detail="Permission denied")
        
    try:
        # ตรวจนามสกุล
        filename = file.filename.lower()
        if not filename.endswith(('.png', '.jpg', '.jpeg', '.webp', '.gif')):
             raise HTTPException(status_code=400, detail="รองรับเฉพาะไฟล์รูปภาพ (png, jpg, webp, gif)")
        
        # ตั้งชื่อไฟล์ใหม่กันซ้ำ (เช่น th_uuid.png) หรือใช้ชื่อเดิมก็ได้
        # ในที่นี้ใช้ชื่อเดิมนำหน้า ตามด้วย UUID สั้นๆ เพื่อให้จำง่ายแต่ไม่ซ้ำ
        safe_name = f"{uuid.uuid4().hex[:6]}_{filename.replace(' ', '_')}"
        path = f"flags/{safe_name}"
        
        file_content = await file.read()
        
        supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        supabase.storage.from_("lotto_images").upload(
            path=path,
            file=file_content,
            file_options={"content-type": file.content_type}
        )
        
        return {"message": "Upload success"}
    except Exception as e:
        print(f"Upload Error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# --- 3. ลบรูป (DELETE) ---
@router.delete("/flags")
def delete_flag(
    name: str, # รับชื่อไฟล์ เช่น "th.png"
    current_user: User = Depends(deps.get_current_active_user)
):
    if current_user.role not in [UserRole.admin, UserRole.superadmin]:
        raise HTTPException(status_code=403, detail="Permission denied")
        
    try:
        supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        path = f"flags/{name}"
        supabase.storage.from_("lotto_images").remove([path])
        return {"message": "Deleted successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))