import uuid
from fastapi import APIRouter, UploadFile, File, HTTPException, Form
from supabase import create_client, Client
from app.core.config import settings

router = APIRouter()

# เชื่อมต่อ Supabase
try:
    supabase: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
except Exception as e:
    print(f"⚠️ Supabase Connection Error: {e}")

# Config ของแต่ละ Bucket
BUCKET_CONFIG = {
    "lotto": {
        "name": "lotto_images",
        "allowed": {"jpg", "jpeg", "png", "gif", "webp"},
        "max_size": 2 * 1024 * 1024  # 2MB
    },
    "slip": {
        "name": "slips",
        "allowed": {"jpg", "jpeg", "png"},
        "max_size": 5 * 1024 * 1024  # 5MB
    }
}

@router.post("/", response_model=dict)
async def upload_image(
    file: UploadFile = File(...),
    folder: str = Form("lotto") # รับค่า folder ว่าจะลงถังไหน (default = lotto)
):
    try:
        # 1. ตรวจสอบว่า folder ที่ส่งมาถูกต้องไหม
        if folder not in BUCKET_CONFIG:
            raise HTTPException(status_code=400, detail="Invalid folder type. Use 'lotto' or 'slip'")
        
        config = BUCKET_CONFIG[folder]
        bucket_name = config["name"]
        
        # 2. ตรวจสอบชนิดไฟล์ (MIME Type)
        if not file.content_type.startswith("image/"):
            raise HTTPException(status_code=400, detail="อนุญาตเฉพาะไฟล์รูปภาพเท่านั้น")

        # 3. ตรวจสอบนามสกุลไฟล์
        filename = file.filename.lower()
        if "." not in filename:
             raise HTTPException(status_code=400, detail="ชื่อไฟล์ไม่ถูกต้อง")
        
        file_ext = filename.rsplit(".", 1)[-1]
        if file_ext not in config["allowed"]:
            raise HTTPException(
                status_code=400, 
                detail=f"ไม่อนุญาตไฟล์นามสกุล .{file_ext} สำหรับหมวดหมู่นี้"
            )

        # 4. อ่านและตรวจสอบขนาดไฟล์
        file_content = await file.read()
        if len(file_content) > config["max_size"]:
            raise HTTPException(status_code=400, detail=f"ขนาดไฟล์ใหญ่เกินไป (Max {config['max_size']/1024/1024}MB)")
        
        # 5. เปลี่ยนชื่อไฟล์เป็น UUID
        new_filename = f"{uuid.uuid4()}.{file_ext}"

        # 6. อัปโหลดขึ้น Supabase
        res = supabase.storage.from_(bucket_name).upload(
            path=new_filename,
            file=file_content,
            file_options={"content-type": file.content_type}
        )

        # 7. สร้าง Public URL
        public_url = f"{settings.SUPABASE_URL}/storage/v1/object/public/{bucket_name}/{new_filename}"

        return {"url": public_url}

    except HTTPException as he:
        raise he
    except Exception as e:
        print(f"❌ Upload Error: {e}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")