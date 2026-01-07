# ใช้ Python 3.10 แบบ Slim (ขนาดเล็กและปลอดภัย)
FROM python:3.10-slim

# 1. อนุญาตให้ Log ออกทันที (สำคัญมาก ถ้าไม่มีบรรทัดนี้ คุณจะไม่เห็น Error ว่าทำไมมันพัง)
ENV PYTHONUNBUFFERED True

# ตั้ง Folder ทำงาน
ENV APP_HOME /app
WORKDIR $APP_HOME

# 2. ลง Dependencies ก่อน (เพื่อใช้ Docker Cache)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 3. ก๊อปปี้ Code ทั้งหมดเข้าไป
COPY . ./

# 4. คำสั่งรัน Server (จุดที่คนพลาดบ่อยที่สุด!)
# ต้องใช้ 'exec' เพื่อให้ Signal ส่งถึง Gunicorn
# ต้อง Bind เข้ากับ :$PORT (ไม่ใช่ localhost)
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app:app