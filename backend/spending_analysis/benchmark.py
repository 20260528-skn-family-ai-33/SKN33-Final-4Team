"""Local model reference, aligned monthly comparisons, and conditional saving room."""
from functools import lru_cache
import json
import logging
from math import isfinite
import os
from pathlib import Path
import subprocess

from django.core.exceptions import ObjectDoesNotExist
from django.db import DatabaseError

from .answer import NAMES, won
from . import reasoning

ROOT = Path(__file__).resolve().parents[2]
log = logging.getLogger(__name__)
# 모델 C 추론에 꼭 있어야 하는 파일 (Git에 없음 — 담당자에게 받아 SPENDING_REFERENCE_ROOT 아래에 둔다)
ARTIFACTS = ('models/original/C/model.cbm', 'models/original/C/metadata.json', 'reports/tables/observed_metrics.json')


class NotInstalled(ValueError):
    """추론용 Python 이나 모델 파일이 이 서버에 없다 — ML 비교 기능이 꺼진 상태로 본다"""


def installed(python, root):
    """추론 환경 · 모델 파일이 모두 있는지. 없으면 하위 프로세스를 띄우지 않는다(요청마다 30초 대기 방지)"""
    missing = [name for name in ARTIFACTS if not (Path(root) / name).is_file()]
    if not Path(python).is_file():
        missing.insert(0, 'python')
    if missing:
        _warn_once(tuple(missing))
    return not missing


@lru_cache(maxsize=4)
def _warn_once(missing):
    log.warning('ML 월 소비 비교를 건너뜀: 추론 환경 또는 모델 파일 없음 (%s). BUDGET_INFERENCE_PYTHON · SPENDING_REFERENCE_ROOT 확인', ', '.join(missing))
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


def _model_input(user, profile, today):
    """모델 C 입력 (나이 · 성별 · 소득 · 차량 · 고정비). 대상 범위 밖이거나 정보가 없으면 None"""
    birth = profile.birth_date
    age = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day)) if birth else None
    income = profile.monthly_take_home_income
    sex = {'male': 1, 'female': 2}.get(profile.gender)
    if age is None or not 20 <= age <= 34 or sex is None or profile.has_car is None or not income:
        return None
    from finance.models import RecurringExpense
    from django.db.models import Sum
    fixed = RecurringExpense.objects.filter(user=user, status='confirmed', category_id__in=('HOUSING_TELECOM', 'ETC')).aggregate(amount=Sum('amount'))['amount'] or 0
    return dict(age=age, sex=sex, net_income=income, vehicle_count=int(profile.has_car), fixed_expense_amount=fixed)


def _paths():
    python = os.getenv('BUDGET_INFERENCE_PYTHON', str(ROOT / 'local_workspace/.venv-budget-inference' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')))
    root = os.getenv('SPENDING_REFERENCE_ROOT', str(ROOT / 'local_workspace/models/living_expense_prediction'))
    return python, root


def reference(user, summary, today):
    """소비 유형 보조 근거용 모델 C 기준 (유형 판정에는 쓰지 않는다).
    → {'amounts': {카테고리 코드: 원}, 'mae': {코드: 원}, 'version', 'label', 'actual': {코드: 원}} 또는 {'reason': ...}"""
    observations = summary.get('reference_month')
    if not observations:
        return {'reason': 'no_full_month'}
    try:
        model_profile = _model_input(user, user.financial_profile, today)
        if model_profile is None:
            return {'reason': 'out_of_range'}
        python, root = _paths()
        if not installed(python, root):
            return {'reason': 'not_installed'}
        prediction = _predict(json.dumps(model_profile, sort_keys=True), python, root)
    except (AttributeError, ObjectDoesNotExist, DatabaseError, ValueError):
        return {'reason': 'failed'}
    codes = {code: key for code, key in MODEL_CODES.items() if code not in ('ETC', 'HOUSING_TELECOM')}
    return {'amounts': {code: round(prediction['amounts'][key] / 1000) * 1000 for code, key in codes.items()},
            'mae': {code: prediction['category_mae'][key] for code, key in codes.items()},
            'version': prediction['model'], 'label': observations['label'],
            'actual': dict(observations['categories']), 'unclassified': observations.get('unclassified', 0)}


def for_user(user, summary, today):
    if summary.get('basis', {}).get('filters') or summary.get('basis', {}).get('categories'):
        return [], []
    savings_lines, savings_notes = [], []
    try:
        profile = user.financial_profile
        income = profile.monthly_take_home_income
        savings_lines, savings_notes = saving_outlook(summary, income, profile.savings_floor)
        if not summary.get('reference_month'):
            return savings_lines, [*savings_notes, '한 달 전체가 확인된 내역이 없어 ML 월 소비 기준과의 비교는 보류했어요.']
        model_profile = _model_input(user, profile, today)
        if model_profile is None:
            return savings_lines, [*savings_notes, 'ML 비교에 필요한 나이·성별·월 소득·차량 정보가 부족하거나 모델의 대상 범위와 달라 비교를 보류했어요.']
        fixed = model_profile['fixed_expense_amount']
        python, root = _paths()
        if not installed(python, root):
            raise NotInstalled('reference_not_installed')
        prediction = _predict(json.dumps(model_profile, sort_keys=True), python, root)
        return compare(summary, prediction, dict(income=income, fixed=fixed, savings_goal=profile.savings_floor))
    except NotInstalled:  # 이 서버에 ML 비교가 설치되지 않음 — 매 답변마다 '못 불러왔다'고 쓰지 않고 비교만 뺀다
        return savings_lines, savings_notes
    except (AttributeError, ObjectDoesNotExist, DatabaseError, ValueError):
        return savings_lines, [*savings_notes, 'ML 비교 자료를 불러오지 못했어요. 거래에서 확인한 소비 패턴과 추이는 그대로 분석했어요.']
