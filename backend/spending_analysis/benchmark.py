"""Local model reference, aligned monthly comparisons, and conditional saving room."""
from functools import lru_cache
import json
from math import isfinite
import os
from pathlib import Path
import subprocess

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError

from .answer import NAMES, won
from . import reasoning

ROOT = Path(__file__).resolve().parents[2]
MODEL_CODES = dict(zip(('FOOD', 'HOUSING_TELECOM', 'TRANSPORT_CAR', 'HOUSEHOLD', 'FASHION_SHOPPING',
                       'MEDICAL_HEALTH', 'BEAUTY', 'EDUCATION', 'CULTURE_LEISURE', 'TRAVEL_LODGING', 'ETC'),
                      ('food', 'housing_communication', 'transport_vehicle', 'household_goods', 'fashion_shopping',
                       'medical_health', 'beauty_care', 'education', 'culture_leisure', 'travel_accommodation', 'other')))


@lru_cache(maxsize=32)
def _predict(profile_json, python, root):
    try:
        process = subprocess.run([python, str(ROOT / 'scripts/living_expense_prediction/predict_reference.py'), root],
            input=profile_json, text=True, encoding='utf-8', capture_output=True, timeout=30,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if process.returncode:
            raise ValueError('reference_inference_failed')
        result = json.loads(process.stdout)
        for field in ('amounts', 'category_mae'):
            if set(result[field]) != set(MODEL_CODES.values()) or any(type(v) not in (int, float) or not isfinite(v) or v < 0 for v in result[field].values()):
                raise ValueError('reference_output_invalid')
        if result['model'] != 'v13_model_c' or type(result['samples']) is not int or result['samples'] <= 0 or type(result['total_mae']) not in (int, float) or not isfinite(result['total_mae']) or result['total_mae'] < 0:
            raise ValueError('reference_validation_invalid')
        return result
    except (OSError, subprocess.SubprocessError, KeyError, TypeError, ValueError):
        raise ValueError('reference_unavailable') from None


def compare(summary, prediction, profile):
    """Never compare a month-to-date sum to a model's full-month prediction."""
    observations = summary.get('reference_month')
    if not observations:
        return [], ['한 달 전체가 확인된 내역이 없어 ML 월 소비 기준과의 비교는 보류했어요.']
    lines, notes = [], []
    expected = prediction['amounts']
    excessive = []
    for code, actual in observations['categories'].items():
        if code not in MODEL_CODES or code in ('ETC', 'HOUSING_TELECOM'):
            continue
        if actual <= 0:
            continue
        key = MODEL_CODES[code]
        reference = round(expected[key] / 1000) * 1000
        delta = actual - reference
        if observations.get('unclassified', 0) and delta < 0:
            continue
        if abs(delta) > prediction['category_mae'][key] and reasoning.material(delta, [], reference)['meaningful']:
            excessive.append((abs(delta), code, actual, reference, delta))
    for _, code, actual, reference, delta in sorted(excessive, reverse=True)[:2]:
        lines.append(f"{observations['label']} {NAMES[code]} 지출은 {won(actual)}으로, 비슷한 조건의 ML 월 소비 기준 약 {won(reference)}보다 {won(abs(delta))} {'높아요' if delta > 0 else '낮아요'}.")
    # An old month's excess only supports an action if the same verified pattern remains now.
    from .lifestyle import suggestions
    for _, code, _, _, delta in sorted(excessive, reverse=True):
        if delta <= 0:
            continue
        candidates = [{**f, 'reference_high': True} for f in summary.get('candidates', []) if f.get('category') == code
                      and f.get('comparable') and f.get('regular_delta', 0) >= 0]
        action = suggestions(candidates)
        if action:
            notes.append('ML 기준 초과만으로 과소비를 단정하지 않았어요. 행동 제안은 지금도 확인된 반복 소비에 한정했어요.')
            summary['reference_actions'] = action
            break
    if not lines:
        notes.append(f"{observations['label']} 소비를 모델과 비교했지만, 모델의 평균 예측 오차까지 고려해 크게 벗어났다고 설명할 카테고리는 없어요.")
    notes.append('ML 기준은 나이·성별·소득·차량·고정비 조건으로 예측한 참고 금액이에요. 실제 비슷한 사람들의 평균이나 권장 소비 한도는 아니에요.')
    notes.append(f"모델 C의 별도 평가 {prediction['samples']:,}건에서 총액 평균 절대오차는 {won(round(prediction['total_mae']))}이에요. 카테고리별 평균 오차보다 작은 차이는 강조하지 않았어요. 오차는 개인별 신뢰구간이 아니에요.")
    notes.append('ML 자료는 1인 청년 근로자 가구 기준이며 차량 보유는 1대로 가정했어요. 등록 주거·통신 및 기타 고정지출을 모델 고정비의 대용값으로 사용해요. 보험·대출이자와 통계 업종 묶음의 차이 때문에 정확한 동일 조건 비교는 아니에요.')
    if observations.get('unclassified', 0):
        notes.append('비교 월에도 미분류 금액이 있어 분류된 금액만 비교했어요. 적게 쓴다는 판단은 하지 않았어요.')
    savings_lines, savings_notes = saving_outlook(summary, profile['income'], profile.get('savings_goal'))
    lines.extend(savings_lines)
    notes.extend(savings_notes)
    total = summary.get('total_forecast', {})
    if total.get('state') == 'ready':
        room = profile['income'] - total['month_end']
        # Same income and fixed costs; only variable consumption differs. Not observed peer savings.
        predicted_variable = sum(expected.values())
        reference_room = profile['income'] - profile['fixed'] - round(predicted_variable)
        difference = room - reference_room
        if abs(difference) > prediction['total_mae']:
            lines.append(f"같은 소득·고정지출에서 모델의 소비 수준을 적용한 경우보다 남는 돈은 약 {won(abs(difference))} {'많은' if difference > 0 else '적은'} 전망이에요.")
    notes.append('다른 사람의 실제 저축액 자료는 없어 저축액 우열은 판단하지 않았어요.')
    return lines, notes


def saving_outlook(summary, income, goal):
    total = summary.get('total_forecast', {})
    if not income or total.get('state') != 'ready':
        return [], ['저축 여력은 등록 월 소득과 검증된 총지출 전망이 함께 있을 때 계산해요.']
    room = income - total['month_end']
    lines = [f"등록한 월 소득에서 예상 총지출을 빼면 약 {won(room)}이 남아요." if room >= 0 else
             f"예상 총지출이 등록한 월 소득보다 약 {won(-room)} 많아요."]
    if goal is not None and room < goal:
        lines.append(f"설정한 월 저축 목표 {won(goal)}을 남기려면 예상 지출을 약 {won(goal - room)} 줄여야 해요.")
    return lines, ['남는 돈은 실제 저축액이 아니며 대출 원금·이체·미등록 예정 지출에 따라 달라져요.']


def for_user(user, summary, today):
    if summary.get('basis', {}).get('filters') or summary.get('basis', {}).get('categories'):
        return [], []
    savings_lines, savings_notes = [], []
    try:
        profile = user.financial_profile
        birth = profile.birth_date
        age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day)) if birth else None
        income = profile.monthly_take_home_income
        savings_lines, savings_notes = saving_outlook(summary, income, profile.savings_floor)
        if not summary.get('reference_month'):
            return savings_lines, [*savings_notes, '한 달 전체가 확인된 내역이 없어 ML 월 소비 기준과의 비교는 보류했어요.']
        sex = {'male': 1, 'female': 2}.get(profile.gender)
        if age is None or not 20 <= age <= 34 or sex is None or profile.has_car is None or not income:
            return savings_lines, [*savings_notes, 'ML 비교에 필요한 나이·성별·월 소득·차량 정보가 부족하거나 모델의 대상 범위와 달라 비교를 보류했어요.']
        from finance.models import RecurringExpense
        from django.db.models import Sum
        fixed = RecurringExpense.objects.filter(user=user, status='confirmed', category_id__in=('HOUSING_TELECOM', 'ETC')).aggregate(amount=Sum('amount'))['amount'] or 0
        model_profile = dict(age=age, sex=sex, net_income=income, vehicle_count=int(profile.has_car), fixed_expense_amount=fixed)
        python = os.getenv('BUDGET_INFERENCE_PYTHON', str(ROOT / 'local_workspace/.venv-budget-inference' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')))
        root = os.getenv('SPENDING_REFERENCE_ROOT', str(ROOT / 'local_workspace/models/living_expense_prediction'))
        prediction = _predict(json.dumps(model_profile, sort_keys=True), python, root)
        return compare(summary, prediction, dict(income=income, fixed=fixed, savings_goal=profile.savings_floor))
    except (AttributeError, ObjectDoesNotExist, DatabaseError, ValueError):
        return savings_lines, [*savings_notes, 'ML 비교 자료를 불러오지 못했어요. 거래에서 확인한 소비 패턴과 추이는 그대로 분석했어요.']
