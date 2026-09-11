from fastapi.testclient import TestClient
import os
os.environ.setdefault("VISION_PROVIDER", "fixture")
from server import app

client = TestClient(app)

def main():
    h = client.get('/api/health')
    assert h.status_code == 200, h.text
    data = h.json()
    assert data['ok'] is True
    assert data['version'] == '6.0'
    root = client.get('/')
    assert root.status_code == 200
    print('SMOKE PASS', data)

if __name__ == '__main__':
    main()
