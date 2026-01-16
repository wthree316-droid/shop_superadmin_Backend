# app/core/game_logic.py

from typing import List, Union, Dict, Any
from decimal import Decimal, ROUND_HALF_UP 

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
    ดึงราคาจ่ายจาก Config (รองรับทั้งแบบเลขตัวเดียวและแบบ Object)
    """
    if not rules:
        return Decimal('0.00')

    rates = rules.get("rates", {})
    if not rates:
        return Decimal('0.00')
        
    # ดึงค่าดิบออกมาก่อน
    raw_data = rates.get(bet_type, 0)
    
    # เช็คว่าเป็น Dict (โครงสร้างใหม่) หรือไม่
    if isinstance(raw_data, dict):
        # ถ้าเป็น Dict ให้ดึง field 'pay'
        val = raw_data.get('pay', 0)
    else:
        # ถ้าไม่ใช่ (เป็น int/str แบบเก่า) ก็ใช้ค่าเลย
        val = raw_data
    
    # แปลงเป็น Decimal
    try:
        return Decimal(str(val)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except:
        return Decimal('0.00')