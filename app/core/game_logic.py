from typing import List, Union, Dict, Any
from decimal import Decimal, ROUND_HALF_UP 

def expand_numbers(number: str, bet_type: str) -> List[str]:
    return [number.strip()]

def get_reward_rate(bet_type: str, rules: dict) -> Decimal:
    if not rules: return Decimal('0.00')
    rates = rules.get("rates", {})
    if not rates: return Decimal('0.00')
    raw_data = rates.get(bet_type, 0)
    if isinstance(raw_data, dict):
        val = raw_data.get('pay', 0)
    else:
        val = raw_data
    try:
        return Decimal(str(val)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except:
        return Decimal('0.00')

# ✅ ย้าย Logic ตรวจรางวัลมาไว้ที่นี่ (Centralized Logic)
def check_is_win_precise(bet_type: str, number: str, top_3: str, bottom_2: str) -> bool:
    if not number or not top_3 or not bottom_2: return False
    
    try:
        # 1. กลุ่ม 3 ตัว
        if bet_type == '3top':      
            return number == top_3
        elif bet_type == '3tod':    
            # โต๊ด: เรียงตัวเลขแล้วเทียบกัน (เช่น 123 == 321)
            return sorted(list(number)) == sorted(list(top_3))
            
        # 2. กลุ่ม 2 ตัว
        elif bet_type == '2up':     
            return number == top_3[-2:] # 2 ตัวท้ายของรางวัลที่ 1
        elif bet_type == '2down':   
            return number == bottom_2
        
        # 3. กลุ่มเลขวิ่ง (Run)
        elif bet_type == 'run_up':  
            # วิ่งบน: เลขที่แทง ปรากฏใน 3 ตัวบน
            return number in top_3
            
        elif bet_type == 'run_down': 
            # วิ่งล่าง: เลขที่แทง ปรากฏใน 2 ตัวล่าง
            return number in bottom_2
            
        return False
    except:
        return False