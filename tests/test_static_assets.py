"""Exercise real ASGI static routes without startup, workers or production data."""
import os
os.environ['TICKET_MAIL_WORKER'] = '0'
os.environ['TICKET_FEISHU_NOTIFY_WORKER'] = '0'
import re
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
import app

class StaticAssetsTests(unittest.TestCase):
    def test_every_local_asset_referenced_by_entry_pages_is_served(self):
        root = Path(app.__file__).parent
        assets = set()
        for page in ('index.html', 'login.html'):
            assets.update(re.findall(r'(?:src|href)=[\"\x27](/static/[^\"\x27]+)', (root/'static'/page).read_text()))
        self.assertTrue(any('feishu.js' in x for x in assets))
        self.assertTrue(any('mobile.js' in x for x in assets))
        client = TestClient(app.app)  # no context manager: deliberately no lifespan
        try:
            for url in sorted(assets):
                with self.subTest(url=url):
                    response = client.get(url)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.content, (root/url.split('?')[0].lstrip('/')).read_bytes())
            self.assertEqual(client.get('/static/unlisted-secret.txt').status_code, 404)
        finally:
            client.close()
