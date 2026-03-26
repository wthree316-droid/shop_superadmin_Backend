import sys
import os
from datetime import datetime
from sqlalchemy import text

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.db.session import SessionLocal
from app.models.lotto import LottoType
from app.core import lotto_cache

def parse_time(t_str):
    try:
        return datetime.strptime(t_str, "%H:%M").time()
    except ValueError:
        return None

FLAG_BASE_URL = "https://flagcdn.com/w160"

# รายการแม่แบบ (No Category)
DEFAULT_TEMPLATES = [
    # ==========================================
    # 1. หมวดหวยไทย (THAI)
    # ==========================================
    { "flag": "th", "name": "รัฐบาลไทย", "code": "THAI_GOV", "close": "15:20", "result": "15:30", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    { "flag": "th", "name": "รัฐบาลไทย 70", "code": "THAI_GOV_70", "close": "15:20", "result": "15:30", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    { "flag": "th", "name": "ออมสิน", "code": "GSB", "close": "10:00", "result": "11:00", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    { "flag": "th", "name": "ธกส", "code": "BAAC", "close": "10:20", "result": "11:00", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},

    # ==========================================
    # 2. หมวดหวยลาว (LAOS)
    # ==========================================
    {"flag": "la", "name": "ลาวประตูชัย", "code": "LAO_PRATU", "close": "05:40", "result": "05:45", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "la", "name": "ลาวสันติภาพ", "code": "LAO_SANTI", "close": "06:40", "result": "06:45", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "la", "name": "ประชาชนลาว", "code": "LAO_PRACHA", "close": "07:40", "result": "07:45", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "la", "name": "ลาว Extra", "code": "LAO_EXTRA", "close": "08:25", "result": "08:30", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "la", "name": "ลาว TV", "code": "LAO_TV", "close": "10:25", "result": "10:30", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "la", "name": "ลาว HD", "code": "LAO_HD", "close": "13:40", "result": "13:45", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "la", "name": "ลาวสตาร์", "code": "LAO_STAR", "close": "15:40", "result": "15:45", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "la", "name": "หุ้นลาว VIP", "code": "LAO_STOCK_VIP", "close": "15:50", "result": "16:00", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "la", "name": "ลาวพัฒนา", "code": "LAO_DEV", "close": "20:20", "result": "20:25", "days": ["MON", "WED", "FRI"]},
    {"flag": "la", "name": "หวยลาวสามัคคี", "code": "LAO_SAMAK", "close": "20:20", "result": "20:30", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "la", "name": "ลาวพัฒนา 70", "code": "LAO_DEV_70", "close": "20:20", "result": "20:25", "days": ["MON", "WED", "FRI"]},
    {"flag": "la", "name": "ลาวอาเซียน", "code": "LAO_ASEAN", "close": "20:55", "result": "21:00", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "la", "name": "ลาวสามัคคี VIP", "code": "LAO_SAMAK_VIP", "close": "21:25", "result": "21:30", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "la", "name": "ลาว VIP", "code": "LAO_VIP", "close": "21:25", "result": "21:30", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "la", "name": "ลาวSTAR VIP", "code": "LAO_STAR_VIP", "close": "21:45", "result": "22:00", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "la", "name": "ลาว กาชาด", "code": "LAO_KACHAD", "close": "23:25", "result": "23:30", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},

    # ==========================================
    # 3. หมวดหวยฮานอย (HANOI)
    # ==========================================
    {"flag": "vn", "name": "ฮานอยอาเซียน", "code": "HANOI_ASEAN", "close": "09:10", "result": "09:30", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "vn", "name": "เวียดนาม VIP เช้า", "code": "VIET_M_VIP", "close": "09:30", "result": "09:40", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "vn", "name": "ฮานอย HD", "code": "HANOI_HD", "close": "11:10", "result": "11:30", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "vn", "name": "ฮานอย สตาร์", "code": "HANOI_STAR", "close": "12:10", "result": "12:30", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "vn", "name": "เวียดนาม VIP บ่าย", "code": "VIET_A_VIP", "close": "14:00", "result": "14:10", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "vn", "name": "ฮานอย TV", "code": "HANOI_TV", "close": "14:10", "result": "14:30", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "vn", "name": "ฮานอย กาชาด", "code": "HANOI_KACHAD", "close": "16:10", "result": "16:30", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "vn", "name": "ฮานอยเฉพาะกิจ", "code": "HANOI_SPEC", "close": "16:10", "result": "16:30", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "vn", "name": "เวียดนาม VIP เย็น", "code": "VIET_E_VIP", "close": "16:35", "result": "16:45", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "vn", "name": "ฮานอยสามัคคี", "code": "HANOI_SAMAK", "close": "17:10", "result": "17:30", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "vn", "name": "ฮานอยพิเศษ", "code": "HANOI_SPL", "close": "17:10", "result": "17:30", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "vn", "name": "ฮานอยปกติ", "code": "HANOI_NORM", "close": "18:10", "result": "18:30", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "vn", "name": "ฮานอยตรุษจีน", "code": "HANOI_CNY", "close": "18:10", "result": "18:30", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "vn", "name": "ฮานอยพัฒนา", "code": "HANOI_PATT", "close": "19:10", "result": "19:30", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "vn", "name": "ฮานอย VIP", "code": "HANOI_VIP", "close": "19:10", "result": "19:30", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "vn", "name": "ฮานอย 4D", "code": "HANOI_4D", "close": "20:35", "result": "21:00", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "vn", "name": "ฮานอย EXTRA", "code": "HANOI_EXTRA", "close": "22:10", "result": "22:30", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "vn", "name": "ฮานอยดึก", "code": "HANOI_LATE", "close": "22:10", "result": "22:30", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},

    # ==========================================
    # 4. หมวดหวยหุ้น (STOCKS)
    # ==========================================
    {"flag": "us", "name": "ดาวโจนส์ USA", "code": "DOW_USA", "close": "00:10", "result": "00:30", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "us", "name": "ดาวโจนส์", "code": "DOWJONES", "close": "02:00", "result": "03:10", "days": ["MON", "TUE", "WED", "THU", "FRI", "SUN"]},
    {"flag": "jp", "name": "นิเคอิ เช้า", "code": "NIKKEI_M", "close": "09:25", "result": "09:30", "days": ["MON", "TUE", "WED", "THU", "FRI"]},
    {"flag": "cn", "name": "จีน เช้า", "code": "CHINA_M", "close": "10:20", "result": "10:30", "days": ["MON", "TUE", "WED", "THU", "FRI"]},
    {"flag": "hk", "name": "ฮั่งเส็ง เช้า", "code": "HANGSENG_M", "close": "10:55", "result": "11:05", "days": ["MON", "TUE", "WED", "THU", "FRI"]},
    {"flag": "tw", "name": "ไต้หวัน", "code": "TAIWAN", "close": "12:10", "result": "12:35", "days": ["MON", "TUE", "WED", "THU", "FRI"]},
    {"flag": "kr", "name": "เกาหลี", "code": "KOREA", "close": "12:45", "result": "13:40", "days": ["MON", "TUE", "WED", "THU", "FRI"]},
    {"flag": "jp", "name": "นิเคอิ บ่าย", "code": "NIKKEI_A", "close": "12:55", "result": "13:30", "days": ["MON", "TUE", "WED", "THU", "FRI"]},
    {"flag": "cn", "name": "จีน บ่าย", "code": "CHINA_A", "close": "13:45", "result": "14:00", "days": ["MON", "TUE", "WED", "THU", "FRI"]},
    {"flag": "hk", "name": "ฮั่งเส็ง บ่าย", "code": "HANGSENG_A", "close": "14:55", "result": "15:09", "days": ["MON", "TUE", "WED", "THU", "FRI"]},
    {"flag": "th", "name": "ไทยเย็น", "code": "THAI_EVENING", "close": "16:05", "result": "16:24", "days": ["MON", "TUE", "WED", "THU", "FRI"]},
    {"flag": "sg", "name": "สิงคโปร์", "code": "SINGAPORE", "close": "16:10", "result": "16:20", "days": ["MON", "TUE", "WED", "THU", "FRI"]},
    {"flag": "in", "name": "อินเดีย", "code": "INDIA", "close": "16:50", "result": "17:00", "days": ["MON", "TUE", "WED", "THU", "FRI"]},
    {"flag": "eg", "name": "อิยิปต์", "code": "EGYPT", "close": "17:50", "result": "18:45", "days": ["MON", "TUE", "WED", "THU", "FRI"]},
    {"flag": "my", "name": "มาเลเซีย", "code": "MALAYSIA", "close": "18:10", "result": "18:30", "days": ["TUE", "WED", "SAT", "SUN"]},
    {"flag": "gb", "name": "อังกฤษ", "code": "ENGLAND", "close": "22:15", "result": "22:40", "days": ["MON", "TUE", "WED", "THU", "FRI"]},
    {"flag": "de", "name": "เยอรมัน", "code": "GERMANY", "close": "22:15", "result": "22:50", "days": ["MON", "TUE", "WED", "THU", "FRI"]},
    {"flag": "ru", "name": "รัสเซีย", "code": "RUSSIA", "close": "22:25", "result": "22:55", "days": ["MON", "TUE", "WED", "THU", "FRI"]},
    {"flag": "eu", "name": "ยูโร", "code": "EURO", "close": "23:05", "result": "23:30", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},

    # ==========================================
    # 5. หมวดหวยหุ้น VIP (STOCKSVIP)
    # ==========================================
    {"flag": "us", "name": "ดาวโจนส์ VIP", "code": "DOW_VIP", "close": "00:10", "result": "00:30", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "us", "name": "ดาวโจนส์ STAR", "code": "DOW_STAR", "close": "01:05", "result": "01:30", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "us", "name": "ดาวโจนส์ Mid Night", "code": "DOW_MID", "close": "02:35", "result": "03:00", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "us", "name": "ดาวโจนส์ Extra", "code": "DOW_EXTRA", "close": "03:35", "result": "03:40", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "us", "name": "ดาวโจนส์ TV", "code": "DOW_TV", "close": "04:35", "result": "04:40", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "jp", "name": "นิเคอิเช้า VIP", "code": "NIKKEI_M_VIP", "close": "09:00", "result": "09:05", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "cn", "name": "จีนเช้า VIP", "code": "CHINA_M_VIP", "close": "10:00", "result": "10:05", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "hk", "name": "ฮั่งเส็งเช้า VIP", "code": "HANGSENG_M_VIP", "close": "10:30", "result": "10:35", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "tw", "name": "ไต้หวัน VIP", "code": "TAIWAN_VIP", "close": "11:30", "result": "11:35", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "kr", "name": "เกาหลี VIP", "code": "KOREA_VIP", "close": "12:30", "result": "12:40", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "jp", "name": "นิเคอิบ่าย VIP", "code": "NIKKEI_A_VIP", "close": "13:20", "result": "13:25", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "cn", "name": "จีนบ่าย VIP", "code": "CHINA_A_VIP", "close": "14:20", "result": "14:25", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "hk", "name": "ฮั่งเส็งบ่าย VIP", "code": "HANGSENG_A_VIP", "close": "15:20", "result": "15:25", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "sg", "name": "สิงคโปร์ VIP", "code": "SINGAPORE_VIP", "close": "17:00", "result": "17:05", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "in", "name": "อินเดีย VIP", "code": "INDIA_VIP", "close": "17:35", "result": "17:40", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "gb", "name": "อังกฤษ VIP", "code": "ENGLAND_VIP", "close": "21:45", "result": "21:50", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "de", "name": "เยอรมัน VIP", "code": "GERMANY_VIP", "close": "22:45", "result": "22:50", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "ru", "name": "รัสเซีย VIP", "code": "RUSSIA_VIP", "close": "23:45", "result": "23:50", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},

    # ==========================================
    # 6. หมวดอื่นๆ (OTHERS) - แม่โขง
    # ==========================================
    {"flag": "un", "name": "แม่โขงทูเดย์", "code": "MK_TODAY", "close": "11:10", "result": "11:20", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "un", "name": "แม่โขง HD", "code": "MK_HD", "close": "13:30", "result": "13:40", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "un", "name": "แม่โขงเมก้า", "code": "MK_MEGA", "close": "14:40", "result": "14:50", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "un", "name": "แม่โขงสตาร์", "code": "MK_STAR", "close": "15:30", "result": "15:40", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "un", "name": "แม่โขงพลัส", "code": "MK_PLUS", "close": "16:35", "result": "16:45", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "un", "name": "แม่โขงพิเศษ", "code": "MK_SPEC", "close": "17:20", "result": "17:30", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "un", "name": "แม่โขงปกติ", "code": "MK_NORM", "close": "18:20", "result": "18:30", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "un", "name": "แม่โขง VIP", "code": "MK_VIP", "close": "19:40", "result": "19:50", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "un", "name": "แม่โขงพัฒนา", "code": "MK_PATT", "close": "21:10", "result": "21:20", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "un", "name": "แม่โขงโกลด์", "code": "MK_GOLD", "close": "22:40", "result": "22:50", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
    {"flag": "un", "name": "แม่โขงไนท์", "code": "MK_NIGHT", "close": "23:40", "result": "23:50", "days": ["SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"]},
]

def seed_lottos():
    db = SessionLocal()
    try:
        print("\n" + "="*30)
        print("🚀 Restoring Templates (No Category Mode)...")
        
        added = 0
        updated = 0
        
        for item in DEFAULT_TEMPLATES:
            img_url = f"{FLAG_BASE_URL}/{item['flag']}.png"
            
            # ค้นหาด้วย code และ shop_id is NULL
            exists = db.query(LottoType).filter(
                LottoType.code == item["code"],
                LottoType.shop_id == None 
            ).first()
            
            if not exists:
                new_tmpl = LottoType(
                    name=item["name"],
                    code=item["code"],
                    category=None,   # ✅ ปล่อยว่าง
                    is_template=True, 
                    shop_id=None,
                    open_days=item["days"],
                    open_time=parse_time("00:00"),
                    close_time=parse_time(item["close"]),
                    result_time=parse_time(item["result"]),
                    img_url=img_url,
                    rate_profile_id=None    
                )
                db.add(new_tmpl)
                print(f"✅ Created: {item['name']}")
                added += 1
            else:
                exists.is_template = True
                exists.category = None # ✅ ล้างหมวดหมู่ทิ้ง
                exists.shop_id = None
                print(f"🔄 Updated: {item['name']}")
                updated += 1
        
        db.commit()
        lotto_cache.invalidate_lotto_cache()
        print(f"\n🎉 Done! Created: {added}, Updated: {updated}")

    except Exception as e:
        print(f"❌ Error: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    seed_lottos()