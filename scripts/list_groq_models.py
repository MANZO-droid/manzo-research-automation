# -*- coding: utf-8 -*-
"""진단용: Groq에서 지금 실제로 쓸 수 있는 모델 목록을 출력한다.
2026-08-17, llama-3.3-70b-versatile이 404(model_not_found)로 단종된 걸
발견해서 대체 모델을 찾기 위해 추가."""
import os, sys
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect_gainers import load_env  # noqa: E402

load_env()
key = os.environ["GROQ_API_KEY"]
r = requests.get("https://api.groq.com/openai/v1/models", headers={"Authorization": f"Bearer {key}"}, timeout=15)
r.raise_for_status()
for m in r.json().get("data", []):
    print(m["id"])
