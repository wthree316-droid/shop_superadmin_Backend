# ใช้ Python 3.10 แบบ Slim (เบาและเร็ว)
FROM python:3.10-slim

# 1. ตั้งค่า Environment Variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_HOME=/app \
    PORT=8000

WORKDIR $APP_HOME

# 2. ติดตั้ง System Dependencies ที่จำเป็น (เช่น ตัว compile สำหรับ psycopg2)
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 3. ติดตั้ง Python Libraries
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 4. คัดลอกโค้ดทั้งหมด
COPY . .

# 5. คำสั่งรัน Server (ใช้ PORT จาก Environment)
# หมายเหตุ: Workers = 1 (สำหรับ Cloud Run ที่ Auto Scale)
# Threads = 8 (เพื่อรองรับ Concurrency ได้ดีขึ้น)
CMD exec gunicorn --bind :$PORT --workers 1 --worker-class uvicorn.workers.UvicornWorker --threads 8 --timeout 0 app.main:app