# ใช้ Python เวอร์ชันเดียวกับที่คุณใช้ (แนะนำ 3.9 หรือ 3.10)
FROM python:3.9-slim

# ตั้ง folder ทำงาน
WORKDIR /app

# ก๊อปปี้ไฟล์รายการ library ไปลงก่อน (เพื่อ cache จะได้ build เร็ว)
COPY requirements.txt .

# ลง library ต่างๆ
RUN pip install --no-cache-dir -r requirements.txt

# ก๊อปปี้โค้ดทั้งหมดลงไป
COPY . .

# บอก Docker ว่าเราจะใช้ Port นี้ (Cloud Run ชอบ Port 8080 แต่เราตั้งค่าได้)
# แนะนำให้แก้โค้ด Python ให้รับ PORT จาก env ได้จะดีที่สุด
# แต่ถ้าโค้ด fix 8000 ไว้ ให้ใช้คำสั่งนี้รัน:
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]