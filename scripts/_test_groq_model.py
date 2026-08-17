# -*- coding: utf-8 -*-
"""일회성 진단: 후보 Groq 모델이 한국어로 잘 응답하는지 확인(2026-08-17,
llama-3.3-70b-versatile 단종 대체 모델 선정용). 확인 끝나면 삭제 예정."""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect_gainers import load_env  # noqa: E402
from groq import Groq  # noqa: E402

load_env()
client = Groq(api_key=os.environ["GROQ_API_KEY"])
for model in ["openai/gpt-oss-120b", "qwen/qwen3.6-27b", "groq/compound"]:
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "한국 주식시장에 대해 한 문장으로, 반드시 순수 한국어로만 답해."}],
            max_tokens=150,
        )
        print(f"=== {model} ===")
        print(r.choices[0].message.content)
    except Exception as e:
        print(f"=== {model} FAILED: {e}")
