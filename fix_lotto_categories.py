import sys
import os
import uuid
from sqlalchemy import text

# ตั้งค่า Path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.db.session import SessionLocal
from app.models.lotto import LottoType, LottoCategory
from app.core import lotto_cache

# Config มาตรฐาน (ตัดยี่กีออกแล้ว)
CATEGORY_STD = {
    "THAI":      {"label": "หวยรัฐบาลไทย", "color": "#EF4444", "idx": 1},
    "HANOI":     {"label": "หวยฮานอย",     "color": "#F59E0B", "idx": 2},
    "LAOS":      {"label": "หวยลาว",       "color": "#10B981", "idx": 3},
    "STOCKS":    {"label": "หวยหุ้น",       "color": "#EC4899", "idx": 4},
    "STOCKSVIP": {"label": "หวยหุ้นVIP",    "color": "#8B5CF6", "idx": 5},
    "DOW":       {"label": "หวยดาวโจนส์",   "color": "#F43F5E", "idx": 6},
    "OTHERS":    {"label": "หวยอื่นๆ",      "color": "#3B82F6", "idx": 99},
}

def get_target_key(code):
    """วิเคราะห์รหัสหวย ว่าควรอยู่หมวดไหน"""
    code = code.upper()
    if code == 'THAI_EVENING': return 'STOCKS'
    if code.startswith('THAI') or code.startswith('GSB') or code.startswith('BAAC'): return 'THAI'
    if code.startswith('HANOI') or code.startswith('VIET'): return 'HANOI'
    if code.startswith('LAO'): return 'LAOS'
    if code.startswith('DOW'): return 'DOW'
    
    if 'VIP' in code and not any(x in code for x in ['HANOI', 'LAO', 'VIET', 'DOW']): return 'STOCKSVIP'
    
    stock_prefixes = ['NIKKEI', 'CHINA', 'HANGSENG', 'TAIWAN', 'KOREA', 'SINGAPORE', 'INDIA', 'EGYPT', 'RUSSIA', 'GERMANY', 'ENGLAND', 'MALAYSIA']
    if any(code.startswith(p) for p in stock_prefixes): return 'STOCKS'
    
    return 'OTHERS'

def fix_categories():
    print("🔧 Starting Shop-Aware Category Fix (Skipping Templates)...")
    db = SessionLocal()
    
    try:
        # ✅ ดึงเฉพาะหวยร้านค้า (ข้าม Template)
        shop_lottos = db.query(LottoType).filter(
            LottoType.is_template == False
        ).all()
        
        updated_count = 0
        shop_cat_cache = {}

        for lotto in shop_lottos:
            # ถ้า shop_id เป็น None แต่หลุดมาว่าเป็น is_template=False (ซึ่งไม่ควรเกิด) ก็ข้ามไป
            if lotto.shop_id is None:
                continue

            target_key = get_target_key(lotto.code)
            
            if target_key not in CATEGORY_STD:
                target_key = 'OTHERS'

            target_info = CATEGORY_STD[target_key]
            shop_id = lotto.shop_id
            
            # --- ตรวจสอบหมวดหมู่ของร้านนั้นๆ ---
            if shop_id not in shop_cat_cache:
                shop_cat_cache[shop_id] = {}
            
            if target_key not in shop_cat_cache[shop_id]:
                cat = db.query(LottoCategory).filter(
                    LottoCategory.label == target_info['label'],
                    LottoCategory.shop_id == shop_id
                ).first()
                
                if not cat:
                    print(f"   ✨ Creating '{target_info['label']}' for Shop {shop_id}")
                    cat = LottoCategory(
                        label=target_info['label'],
                        color=target_info['color'],
                        shop_id=shop_id,
                        order_index=target_info['idx']
                    )
                    db.add(cat)
                    db.commit()
                    db.refresh(cat)
                
                shop_cat_cache[shop_id][target_key] = str(cat.id)
            
            correct_cat_id = shop_cat_cache[shop_id][target_key]
            
            if lotto.category != correct_cat_id:
                lotto.category = correct_cat_id
                updated_count += 1

        db.commit()
        lotto_cache.invalidate_lotto_cache()
        
        print("\n" + "="*40)
        print(f"🎉 DONE! Fixed {updated_count} shop lottos.")
        print(f"ℹ️  Templates were left untouched (Category = NULL).")
        print("="*40)

    except Exception as e:
        print(f"❌ Error: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    fix_categories()