# 라우터 · 소비 해석 실제 LLM 평가 기록

집계 · 설정 · 해시(`<회차>.json`)와 재채점에 필요한 모델 응답 원문(`<회차>-responses.jsonl`, 단위 ID + 역할별 응답)만 둔다.
평가 문장은 합성 사례라 개인정보가 없다. API 키 · 로컬 경로 · 요청 본문 · 장부 파일은 넣지 않는다. 상세 해설은 `backend/chat/router_core/MEMO.md` 15~18번.

- `2026-10-29_b160_run28` — 28차. 사례 B-2026-10-29.1, 라우터 지시문 19c9f8c9 · 소비 a7547d3d. 장부 138/160.
- `2026-10-29_b160_run30` — 30차. 사례 B-2026-10-29.2, 라우터 f894b169 · 소비 302b9cd8. 장부 141/160 (점검 단위 b05 가 본 실행에 다시 들어가 +2, 채점은 한 번만).

재채점 (API 호출 없음, 결과는 저장소 밖에):

```bash
cd scripts/router_eval
python stage_b.py --cases-file <사례 파일> --split all --scenarios --cases <58단위 ID 쉼표 목록> \
  --replay ../../reports/router_eval/<회차>-responses.jsonl --output-dir <저장소 밖 폴더>
```

사례 · 지시문 원문은 `scripts/router_eval/frozen/`. 재채점 결과는 그 회차의 코드(요약 JSON `pre_run.files` 해시)와 같을 때만 같다 —
28차 응답을 지금 코드로 재생하면 s6#5 응답이 없어 그 단위는 채점되지 않는다(MEMO 16번).
