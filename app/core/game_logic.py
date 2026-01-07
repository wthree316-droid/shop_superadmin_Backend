from typing import List
from decimal import Decimal, ROUND_HALF_UP 

# ลบ import itertools ออกได้เลย ไม่ได้ใช้แล้ว

def expand_numbers(number: str, bet_type: str) -> List[str]:
    """
    เวอร์ชั่น Minimal: รับมายังไง ส่งกลับไปอย่างนั้น
    เพราะ Frontend แตกเลขและเปลี่ยน Type มาให้เสร็จแล้ว
    """
    # คืนค่าเป็น List ใส่เลขตัวเดิมตัวเดียว 
    # เพื่อให้ loop ใน play.py ทำงานต่อได้โดยไม่ต้องไปแก้โค้ดส่วนนั้น
    return [number.strip()]


def get_reward_rate(bet_type: str, rules: dict) -> Decimal:
    """
    ดึงราคาจ่ายจาก Config ตรงๆ (เพราะ Frontend ส่ง Core Type มาแล้ว)
    """
    if not rules:
        return Decimal('0.00')

    rates = rules.get("rates", {})
    if not rates:
        return Decimal('0.00')
        
    # Frontend ส่งมาเป็น 2up, 3top, run_up อยู่แล้ว ดึงค่าได้เลย
    raw_rate = rates.get(bet_type, 0)
    
    return Decimal(str(raw_rate)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)