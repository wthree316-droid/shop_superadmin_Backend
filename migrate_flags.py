import sys
import os
import requests
import mimetypes
from sqlalchemy import text

# Setup Path ให้มองเห็น app
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.db.session import SessionLocal
from app.models.lotto import LottoType
from app.core.config import settings
from supabase import create_client

# --- ตั้งค่า ---
BUCKET_NAME = "lotto_images"
TARGET_FOLDER = "flags"  # จะเก็บใน folder flags/
TEMP_DIR = "temp_flags"  # โฟลเดอร์พักไฟล์ชั่วคราว

# Mapping: รหัสธง (flagcdn) -> คำค้นหาในรหัสหวย (Code Prefix)
# ระบบจะโหลดรูปจาก flagcdn มาอัปขึ้น supabase แล้วเอา URL ไปแปะให้หวยที่มีรหัสขึ้นต้นตามนี้
FLAG_MAPPING = {
    "th": ["THAI", "GSB", "BAAC"],           # ไทย, ออมสิน, ธกส
    "la": ["LAO"],                           # หวยลาวทุกชนิด
    "vn": ["HANOI", "VIET"],                 # ฮานอยทุกชนิด
    "us": ["DOW", "DOWJONES"],               # หุ้นดาวโจนส์
    "jp": ["NIKKEI"],                        # หุ้นนิเคอิ
    "cn": ["CHINA"],                         # หุ้นจีน
    "hk": ["HANGSENG"],                      # หุ้นฮั่งเส็ง
    "tw": ["TAIWAN"],                        # ไต้หวัน
    "kr": ["KOREA"],                         # เกาหลี
    "sg": ["SINGAPORE"],                     # สิงคโปร์
    "in": ["INDIA"],                         # อินเดีย
    "ru": ["RUSSIA"],                        # รัสเซีย
    "de": ["GERMANY"],                       # เยอรมัน
    "gb": ["ENGLAND"],                       # อังกฤษ
    "eg": ["EGYPT"],                         # อียิปต์
    "my": ["MALAYSIA"],                      # มาเลเซีย
    "eu": ["EURO"],                          # ยูโร
    "un": ["MK", "OTHER"]                    # แม่โขง (ใช้ธง UN หรือรูปโลก)
}

def migrate_images():
    print("🚀 Starting Flag Migration System...")
    
    # 1. เชื่อมต่อ Supabase
    try:
        supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        print("✅ Supabase Connected")
    except Exception as e:
        print(f"❌ Supabase Connection Error: {e}")
        return

    # 2. เชื่อมต่อ Database
    db = SessionLocal()
    
    # สร้างโฟลเดอร์ชั่วคราวถ้ายังไม่มี
    if not os.path.exists(TEMP_DIR):
        os.makedirs(TEMP_DIR)

    total_updated = 0
    
    try:
        # วนลูปตามรายการธงที่เราตั้งค่าไว้
        for flag_code, prefixes in FLAG_MAPPING.items():
            print(f"\n🔵 Processing Flag: [{flag_code}] for {prefixes}")
            
            # --- A. ดาวน์โหลดรูปจาก FlagCDN ---
            source_url = f"https://flagcdn.com/w320/{flag_code}.png"
            local_filename = f"{flag_code}.png"
            local_path = os.path.join(TEMP_DIR, local_filename)
            
            try:
                # โหลดไฟล์
                response = requests.get(source_url, timeout=10)
                if response.status_code == 200:
                    with open(local_path, 'wb') as f:
                        f.write(response.content)
                else:
                    print(f"   ⚠️ Download failed for {flag_code} (Status: {response.status_code})")
                    continue
            except Exception as e:
                print(f"   ⚠️ Download error: {e}")
                continue

            # --- B. อัปโหลดขึ้น Supabase ---
            supabase_path = f"{TARGET_FOLDER}/{local_filename}"
            mime_type = "image/png"
            
            try:
                with open(local_path, 'rb') as f:
                    file_content = f.read()
                    
                    # อัปโหลด (upsert=true คือทับของเดิมถ้ามีชื่อซ้ำ)
                    supabase.storage.from_(BUCKET_NAME).upload(
                        path=supabase_path,
                        file=file_content,
                        file_options={"content-type": mime_type, "upsert": "true"}
                    )
                
                # ขอ Public URL
                public_url_res = supabase.storage.from_(BUCKET_NAME).get_public_url(supabase_path)
                
                # เช็ค format ของ library supabase ว่าคืนค่าแบบไหน (String หรือ Dict)
                final_url = public_url_res if isinstance(public_url_res, str) else public_url_res.get('publicUrl')
                
                print(f"   ✅ Uploaded: {final_url}")

                # --- C. อัปเดต Database ---
                # หาหวยที่มีรหัส (Code) ขึ้นต้นด้วย prefix ในกลุ่มนี้
                for prefix in prefixes:
                    # ใช้ ILIKE เพื่อหาแบบ Case Insensitive (เช่น Code ขึ้นต้นด้วย 'THAI%')
                    lottos = db.query(LottoType).filter(LottoType.code.ilike(f"{prefix}%")).all()
                    
                    for lotto in lottos:
                        lotto.img_url = final_url
                        total_updated += 1
                        # print(f"      -> Linked to: {lotto.name} ({lotto.code})")

            except Exception as e:
                print(f"   ❌ Upload/Update Error for {flag_code}: {e}")

        # บันทึกลงฐานข้อมูลทีเดียว
        db.commit()
        print("\n" + "="*40)
        print(f"🎉 Migration Completed! Updated {total_updated} lottos.")
        print("="*40)

    except Exception as e:
        db.rollback()
        print(f"❌ Critical Error: {e}")
    finally:
        db.close()
        # ลบโฟลเดอร์ชั่วคราวทิ้ง
        import shutil
        if os.path.exists(TEMP_DIR):
            shutil.rmtree(TEMP_DIR)

if __name__ == "__main__":
    migrate_images()