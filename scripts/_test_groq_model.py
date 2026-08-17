# -*- coding: utf-8 -*-
"""일회성 진단(2차): max_tokens을 늘려서 gpt-oss-120b가 실제로 응답하는지
재확인. 확인 끝나면 삭제 예정."""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect_gainers import load_env  # noqa: E402
from groq import Groq  # noqa: E402

load_env()
client = Groq(api_key=os.environ["GROQ_API_KEY"])
for model in ["openai/gpt-oss-120b", "openai/gpt-oss-20b"]:
    try:
        r = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "한국 주식시장에 대해 한 문장으로, 반드시 순수 한국어로만 답해."}],
            max_tokens=2000,
        )
        print(f"=== {model} (finish_reason={r.choices[0].finish_reason}) ===")
        print(repr(r.choices[0].message.content))
        if hasattr(r.choices[0].message, "reasoning"):
            print("reasoning:", repr(getattr(r.choices[0].message, "reasoning", None))[:200])
    except Exception as e:
        print(f"=== {model} FAILED: {e}")
