# backend/init_system.py
import sys
import os
from dotenv import load_dotenv

# 🌟 1. บังคับโหลด .env ก่อน เพื่อให้เชื่อมต่อ Database ตัวจริงได้ถูกต้อง
load_dotenv() 

# เพิ่ม path เพื่อให้ Python มองเห็นโฟลเดอร์ app
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.db.session import SessionLocal
from app.models.user import User, UserRole
from app.core.security import get_password_hash

# นำเข้าฟังก์ชันจากไฟล์ seed ของคุณ
try:
    from seed_lottos import DEFAULT_TEMPLATES, parse_time
    from app.models.lotto import LottoType
    from app.core import lotto_cache
    CAN_SEED_LOTTOS = True
except ImportError:
    CAN_SEED_LOTTOS = False

def setup_superadmin(db):
    print("⏳ [1/2] Checking Superadmin account...")
    existing_admin = db.query(User).filter(User.username == "superadmin").first()
    
    password_text = "Admin@1234!"
    
    if existing_admin:
        # 🌟 ถ้ามีอยู่แล้ว ให้ "บังคับรีเซ็ตรหัสผ่าน" ทับไปเลย จะได้ล็อกอินเข้าแน่นอน
        existing_admin.password_hash = get_password_hash(password_text)
        existing_admin.role = UserRole.superadmin
        db.commit()
        print(f"✅ Superadmin exists! Password has been FORCE RESET to: {password_text}")
    else:
        print("🔨 Creating the first superadmin account...")
        new_superadmin = User(
            username="superadmin",
            password_hash=get_password_hash(password_text),
            full_name="System Owner",
            role=UserRole.superadmin,
            shop_id=None,
            credit_balance=0,
            is_active=True
        )
        db.add(new_superadmin)
        db.commit()
        print(f"🎉 Successfully created first Superadmin! (User: superadmin, Pass: {password_text})")

def setup_default_lottos(db):
    if not CAN_SEED_LOTTOS:
        return
        
    print("\n⏳ [2/2] Seeding Default Lotto Templates...")
    added, updated = 0, 0
    for item in DEFAULT_TEMPLATES:
        exists = db.query(LottoType).filter(LottoType.code == item["code"], LottoType.shop_id == None).first()
        if not exists:
            new_tmpl = LottoType(
                name=item["name"], code=item["code"], category=None, is_template=True, shop_id=None,
                open_days=item["days"], open_time=parse_time("00:00"), close_time=parse_time(item["close"]),
                result_time=parse_time(item["result"]), img_url="https://flagcdn.com/w160/" + item["flag"] + ".png"
            )
            db.add(new_tmpl)
            added += 1
        else:
            updated += 1
            
    db.commit()
    try:
        lotto_cache.invalidate_lotto_cache()
    except:
        pass
    print(f"🎉 Lotto templates ready! (Added: {added}, Checked/Updated: {updated})")

if __name__ == "__main__":
    print("🚀 Starting System Initialization...")
    db = SessionLocal()
    try:
        setup_superadmin(db)
        setup_default_lottos(db)
        print("\n✨ ALL DONE! The system is ready to use. ✨")
    except Exception as e:
        print(f"❌ Error during initialization: {e}")
        db.rollback()
    finally:
        db.close()