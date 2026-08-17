# -*- coding: utf-8 -*-
"""일회성 진단(3차): 실제 build_chart_only_prompt로 새 모델(openai/gpt-oss-120b,
max_tokens=3000)이 [chartAnalysis] 태그까지 정상 파싱되는지 확인. 확인 끝나면
삭제 예정."""
import os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from collect_gainers import load_env, build_chart_only_prompt, parse_chart_only_response, has_language_issue  # noqa: E402
from groq import Groq  # noqa: E402

load_env()
client = Groq(api_key=os.environ["GROQ_API_KEY"])

technicals = {
    "ma5": 5000, "ma20": 4800, "ma60": 4500, "ma120": 4200, "current": 5200,
    "w52High": 8000, "w52Low": 3000, "pctFromHigh": -35.0, "pctFromLow": 73.3,
    "volRatio": 2.1, "trend": "상승추세", "disparity": 8.3, "adx": 27.5, "cross": None,
}
prompt = build_chart_only_prompt("테스트종목", "000000", "2026-08-17", 15.5, technicals=technicals)

resp = client.chat.completions.create(
    model="openai/gpt-oss-120b", max_tokens=3000,
    messages=[{"role": "user", "content": prompt}],
)
text = resp.choices[0].message.content or ""
print("finish_reason:", resp.choices[0].finish_reason)
print("원문 길이:", len(text))
chart = parse_chart_only_response(text)
print("파싱된 chartAnalysis 길이:", len(chart))
print("언어 오염 여부:", has_language_issue(chart))
print("---내용---")
print(chart)
