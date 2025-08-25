--- START OF FILE: tests/test_api.py ---  
from fastapi.testclient import TestClient  
from capitalguard.interfaces.api.main import app  
from capitalguard.config import settings  
import pytest  
  
# ملاحظة: لإجراء اختبار تكاملي حقيقي، ستحتاج إلى إعداد قاعدة بيانات منفصلة للاختبارات.  
# هذا المثال يركز على اختبار مسار الطلب والاستجابة.  
  
client = TestClient(app)  
  
# يمكن تعيين مفتاح API للاختبارات إذا كان مطلوبًا  
API_KEY = "test_api_key"  
settings.API_KEY = API_KEY  # Override settings for testing  
HEADERS = {"X-API-Key": API_KEY}  
  
def test_health():  
    response = client.get("/health")  
    assert response.status_code == 200  
    assert response.json() == {"status": "ok"}  
  
def test_api_key_protection():  
    response = client.get("/recommendations", headers={"X-API-Key": "wrong_key"})  
    assert response.status_code == 401 # Unauthorized  
  
    response = client.get("/recommendations") # No key  
    assert response.status_code == 401  
  
def test_create_and_close_recommendation_flow():  
    """  
    يختبر سيناريو كامل لإنشاء ثم إغلاق توصية عبر API.  
    """  
    # 1. إنشاء توصية جديدة  
    create_payload = {  
        "asset": "ETHUSDT",  
        "side": "SHORT",  
        "entry": 3000,  
        "stop_loss": 3100,  
        "targets": [2900, 2800]  
    }  
    create_response = client.post("/recommendations", json=create_payload, headers=HEADERS)  
      
    assert create_response.status_code == 200  
    created_rec = create_response.json()  
    assert created_rec["status"] == "OPEN"  
    assert created_rec["asset"] == "ETHUSDT"  
    rec_id = created_rec["id"]  
  
    # 2. إغلاق التوصية التي تم إنشاؤها  
    close_payload = {"exit_price": 2950.0}  
    close_response = client.post(f"/recommendations/{rec_id}/close", json=close_payload, headers=HEADERS)  
  
    assert close_response.status_code == 200  
    closed_rec = close_response.json()  
    assert closed_rec["status"] == "CLOSED"  
    assert closed_rec["id"] == rec_id  
  
    # 3. (اختياري) التحقق من أن التوصية تظهر كمغلقة عند طلبها مرة أخرى  
    get_response = client.get("/recommendations", headers=HEADERS)  
    all_recs = get_response.json()  
    found = False  
    for rec in all_recs:  
        if rec['id'] == rec_id:  
            assert rec['status'] == 'CLOSED'  
            found = True  
            break  
    assert found, "The closed recommendation was not found in the list."  
--- END OF FILE ---