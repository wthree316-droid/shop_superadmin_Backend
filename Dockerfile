# ใช้ Python 3.10 แบบ Slim (ขนาดเล็กและปลอดภัย)
FROM python:3.10-slim

# 1. อนุญาตให้ Log ออกทันที
ENV PYTHONUNBUFFERED True

# ตั้ง Folder ทำงาน
ENV APP_HOME /app
WORKDIR $APP_HOME

# 2. ลง Dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 3. ก๊อปปี้ Code ทั้งหมด
COPY . ./

# 4. คำสั่งรัน Server
# [แก้ตรงนี้] เปลี่ยน app:app เป็น app.main:app
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app.main:app