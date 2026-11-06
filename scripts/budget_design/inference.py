"""Headless ML subprocess client shared by server and local comparison tools."""
import json
import math
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[2]

CATEGORIES = {
    "FOOD": "식비", "HOUSING_TELECOM": "주거·통신", "TRANSPORT_CAR": "교통·차량",
    "HOUSEHOLD": "생활용품", "FASHION_SHOPPING": "패션·쇼핑", "MEDICAL_HEALTH": "의료·건강",
    "BEAUTY": "뷰티·미용", "EDUCATION": "교육", "CULTURE_LEISURE": "문화·여가",
    "TRAVEL_LODGING": "여행·숙박",
}


def predict_ml(profile):
    """Keep CatBoost's pinned environment isolated; send amounts through stdin."""
    default_python = ROOT / 'local_workspace/.venv-budget-inference' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    python = Path(os.environ.get('BUDGET_INFERENCE_PYTHON', str(default_python)))
    worker = Path(__file__).with_name('predict_worker.py')
    if not python.is_file():
        raise ValueError(f"ML 실행 환경이 없습니다: {python}")
    try:
        process = subprocess.run(
            [str(python), str(worker)], input=json.dumps(profile),
            text=True, encoding='utf-8', capture_output=True, cwd=ROOT, timeout=30,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
        )
    except subprocess.TimeoutExpired:
        raise ValueError('ML 실행이 30초를 초과했습니다. 결과 없이 중단했습니다.') from None
    except OSError:
        raise ValueError('ML 실행 환경을 시작할 수 없습니다.') from None
    if process.returncode:
        raise ValueError('ML 실행에 실패했습니다. 모델 파일·체크섬·전용 환경을 확인하세요.')
    try:
        result = json.loads(process.stdout)
        if (result['model_name'] != 'catboost_shallow'
                or result['artifact_id'] != '20261026T074827Z_53769d64'
                or result['living_budget_amount'] != profile['living_budget_amount']):
            raise ValueError
        for field in ('ratios', 'draft_amounts'):
            values = result[field]
            if (not isinstance(values, dict) or set(values) != set(CATEGORIES.values())
                    or any(type(v) not in (int, float) or not math.isfinite(v) or v < 0
                           for v in values.values())):
                raise ValueError
        if not math.isclose(sum(result['ratios'].values()), 1, rel_tol=0, abs_tol=1e-12):
            raise ValueError
        if not math.isclose(sum(result['draft_amounts'].values()), profile['living_budget_amount'],
                            rel_tol=0, abs_tol=1e-6):
            raise ValueError
        for name in CATEGORIES.values():
            if not math.isclose(result['draft_amounts'][name], result['ratios'][name] * profile['living_budget_amount'],
                                rel_tol=0, abs_tol=1e-6):
                raise ValueError
        if any(type(result[key]) is not bool for key in ('used_fallback', 'negative_clipped')):
            raise ValueError
    except (ValueError, KeyError, TypeError, OverflowError):
        raise ValueError('ML 응답의 모델·카테고리·금액 합계가 맞지 않습니다.') from None
    return result
