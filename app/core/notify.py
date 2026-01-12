# app/core/notify.py
import requests
import json

def send_line_message(channel_token: str, target_id: str, message: str, image_url: str = None):
    """
    ส่งข้อความผ่าน LINE Messaging API (Push Message)
    """
    if not channel_token or not target_id:
        print("Missing LINE credentials")
        return

    url = 'https://api.line.me/v2/bot/message/push'
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {channel_token}'
    }

    # สร้าง Payload ข้อความ
    messages_payload = []

    # 1. ข้อความ Text
    messages_payload.append({
        "type": "text",
        "text": message
    })

    # 2. รูปภาพ (ถ้ามี และต้องเป็น https:// เท่านั้น LINE ถึงจะดึงได้)
    if image_url and image_url.startswith("http"):
        messages_payload.append({
            "type": "image",
            "originalContentUrl": image_url,
            "previewImageUrl": image_url
        })

    payload = {
        "to": target_id,
        "messages": messages_payload
    }
    
    try:
        response = requests.post(url, headers=headers, data=json.dumps(payload))
        if response.status_code != 200:
            print(f"LINE API Error: {response.text}")
        else:
            print("LINE Message sent successfully")
    except Exception as e:
        print(f"Failed to send LINE Message: {e}")