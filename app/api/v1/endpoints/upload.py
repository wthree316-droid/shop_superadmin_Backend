import uuid
from fastapi import APIRouter, UploadFile, File, HTTPException
from supabase import create_client, Client
from app.core.config import settings

router = APIRouter()

# 1. เชื่อมต่อ Supabase
try:
    supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
except Exception as e:
    print(f"⚠️ Supabase Connection Error: {e}")

BUCKET_NAME = "lotto_images" 

# [เพิ่ม] รายชื่อนามสกุลไฟล์ที่ปลอดภัย (Whitelist)
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "webp"}
# [เพิ่ม] ขนาดไฟล์สูงสุด (เช่น 5MB)
MAX_FILE_SIZE = 5 * 1024 * 1024 

@router.post("/", response_model=dict)
async def upload_image(file: UploadFile = File(...)):
    try:
        # 1. ตรวจสอบชนิดไฟล์จาก Header (ด่านแรก)
        if not file.content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="อนุญาตเฉพาะไฟล์รูปภาพเท่านั้น")

        # 2. ตรวจสอบนามสกุลไฟล์ (ด่านสอง - สำคัญ!)
        filename = file.filename.lower()
        if "." not in filename:
             raise HTTPException(status_code=400, detail="ชื่อไฟล์ไม่ถูกต้อง")
        
        # ดึงนามสกุลไฟล์ออกมาเช็ค (เช่น .jpg)
        file_ext = filename.rsplit(".", 1)[-1]
        
        if file_ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400, 
                detail=f"ไม่อนุญาตไฟล์นามสกุล .{file_ext} (อนุญาตเฉพาะ: {', '.join(ALLOWED_EXTENSIONS)})"
            )

        # 3. อ่านไฟล์
        file_content = await file.read()
        
        # 4. ตรวจสอบขนาดไฟล์ (ด่านสาม)
        if len(file_content) > MAX_FILE_SIZE:
            raise HTTPException(status_code=400, detail="ขนาดไฟล์ใหญ่เกินไป (สูงสุด 5MB)")
        
        # 5. [SECURITY CORE] เปลี่ยนชื่อไฟล์ใหม่เป็น UUID เสมอ
        # ไม่ว่าลูกค้าจะตั้งชื่อมาว่า "<script>alert(1)</script>.jpg" 
        # เราจะเปลี่ยนเป็น "550e8400-e29b-....jpg" ทันที
        # ทำให้โค้ดอันตรายในชื่อไฟล์ไม่มีผลใดๆ
        new_filename = f"{uuid.uuid4()}.{file_ext}"

        # 6. อัปโหลดขึ้น Supabase
        res = supabase.storage.from_(BUCKET_NAME).upload(
            path=new_filename,
            file=file_content,
            file_options={"content-type": file.content_type}
        )

        # 7. สร้าง Public URL
        public_url = f"{settings.SUPABASE_URL}/storage/v1/object/public/{BUCKET_NAME}/{new_filename}"

        return {"url": public_url}

    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"❌ Upload Error: {e}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")