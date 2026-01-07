# backend/app/core/reward_calculator.py
from decimal import Decimal

class RewardCalculator:
    def __init__(self, top_3: str, bottom_2: str):
        self.top_3 = top_3          # เช่น "567"
        self.top_2 = top_3[-2:]     # ตัดเอา 2 ตัวท้ายบน -> "67"
        self.bottom_2 = bottom_2    # เช่น "89"
    
    def check_is_win(self, bet_number: str, bet_type: str) -> bool:
        """
        Input: เลขที่ลูกค้าซื้อ (bet_number), ประเภท (bet_type - Core Types Only)
        Output: True = ถูกรางวัล
        """
        
        # 1. กลุ่ม 2 ตัวบน
        if bet_type == "2up": 
            return bet_number == self.top_2
            
        # 2. กลุ่ม 2 ตัวล่าง
        elif bet_type == "2down":
            return bet_number == self.bottom_2

        # 3. กลุ่ม 3 ตัวบน
        elif bet_type == "3top":
            return bet_number == self.top_3
            
        # 4. กลุ่ม 3 ตัวโต๊ด
        elif bet_type == "3tod":
            # เรียงเลขแล้วเทียบ
            return sorted(bet_number) == sorted(self.top_3)

        # 5. เลขวิ่งบน
        elif bet_type == "run_up":
            return bet_number in self.top_3
            
        # 6. เลขวิ่งล่าง
        elif bet_type == "run_down":
            return bet_number in self.bottom_2

        return False