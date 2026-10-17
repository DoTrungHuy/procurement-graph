import os
from pathlib import Path
import pytest

TEST_ROOT = Path(__file__).resolve().parents[1]/'.test-work'
TEST_ROOT.mkdir(parents=True,exist_ok=True)
os.environ['DATABASE_PATH'] = str(TEST_ROOT/'test.db')
os.environ['UPLOAD_DIR'] = str(TEST_ROOT/'uploads')
os.environ['DEEPSEEK_API_KEY'] = ''
os.environ['DEMO_SEED'] = 'false'
os.environ['REFERENCE_SEED'] = 'false'
os.environ['NEO4J_URI'] = ''
os.environ['APP_API_TOKEN'] = ''
os.environ['PARSE_CACHE_DIR'] = ''
os.environ['MODEL_RESPONSE_CACHE_DIR'] = ''
os.environ['MODEL_DOCUMENT_CACHE_DIR'] = ''
os.environ['EXTRACT_BLANK_TEMPLATES'] = 'false'
os.environ['EXTRACT_STANDARD_TABLES'] = 'false'
os.environ['EXTRACT_BATCH_ATTACHMENTS'] = 'false'
os.environ['MODEL_SETTINGS_PATH'] = str(TEST_ROOT/'unused-model-settings.local.json')
# Live credentials are absent; model tests use an explicit MockTransport.
os.environ['MODEL_NETWORK_ENABLED'] = 'true'

from app.database import connect, init_db
from app.models import BidExtract, ItemExtract, ProjectExtract

@pytest.fixture(autouse=True)
def clean_database():
    init_db()
    with connect() as c:
        for table in ('job_documents','jobs','items','bids','projects','notices'):
            c.execute(f'DELETE FROM {table}')
    yield

@pytest.fixture
def notice():
    def make(uid='t20260101_10001',lot='未分包',amount=300,**kwargs):
        return ProjectExtract(notice_uuid=uid,project_name='采购项目',purchaser='采购单位A',lot_name=lot,awarded_amount=amount,
            bids=[BidExtract(bidder='供应商甲',is_winner=True,bid_amount=amount),
                  BidExtract(bidder='供应商乙',review_status='通过',bid_amount=amount+10 if amount is not None else None)],
            items=[ItemExtract(product_name='服务器',brand='品牌X',model='M1',quantity=2,unit_price=930,total_price=1860)],**kwargs)
    return make

@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app,client=('127.0.0.1',50000)) as c:
        yield c
