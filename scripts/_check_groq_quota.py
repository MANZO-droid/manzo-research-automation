# -*- coding: utf-8 -*-
"""일회성 진단: Groq 할당량이 지금 회복됐는지 최소 호출로 확인. 확인 끝나면 삭제."""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect_gainers import load_env  # noqa: E402
from groq import Groq, RateLimitError  # noqa: E402

load_env()
client = Groq(api_key=os.environ["GROQ_API_KEY"])
try:
    r = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[{"role": "user", "content": "1+1은? 숫자만 답해."}],
        max_tokens=200,
    )
    print("정상 응답:", repr(r.choices[0].message.content))
except RateLimitError as e:
    print("429 RATE LIMIT:", e)
except Exception as e:
    print("기타 오류:", type(e).__name__, e)
