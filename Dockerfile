# ใช้ Python 3.10 แบบ Slim
FROM python:3.10-slim

# 1. ตั้งค่า Environment
ENV PYTHONUNBUFFERED True
ENV APP_HOME /app
WORKDIR $APP_HOME

# 2. ติดตั้ง Dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 3. คัดลอกโค้ดทั้งหมด
COPY . ./

# 4. [แก้ไขจุดสำคัญ] เพิ่ม -k uvicorn.workers.UvicornWorker
# นี่คือการบอกให้ Gunicorn ใช้ Worker ที่รองรับระบบ Async ของ FastAPI
CMD exec gunicorn --bind :$PORT --workers 1 -k uvicorn.workers.UvicornWorker --threads 8 --timeout 0 app.main:app