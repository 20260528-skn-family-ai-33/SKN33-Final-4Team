"""Price evidence review only; no collection, file access or budget mutation."""
from datetime import date
import math
import re

CATEGORIES = frozenset((
    'FOOD', 'HOUSING_TELECOM', 'TRANSPORT_CAR', 'HOUSEHOLD', 'FASHION_SHOPPING',
    'MEDICAL_HEALTH', 'BEAUTY', 'EDUCATION', 'CULTURE_LEISURE', 'TRAVEL_LODGING'))
NON_MONTHLY = {'HOUSING_TELECOM', 'TRAVEL_LODGING'}


def review_allocations(refs, region_code, before, proposed, *, history_totals,
                       user_direct=(), locks=(), item_groups=None, spent=None, as_of=None):
    """Return advisory corrections, never add money or mutate an allocation."""
    if not re.fullmatch(r'\d{5}', region_code):
        raise ValueError('사용자의 시군구 코드가 필요합니다')
    as_of = as_of or date.today()
    if isinstance(as_of, str):
        as_of = date.fromisoformat(as_of)
    for mapping in (before, proposed, history_totals, spent or {}):
        if any(c not in CATEGORIES or type(n) is not int or n < 0 for c, n in mapping.items()):
            raise ValueError('예산/소비 금액 형식 오류')
    issues, unpriced = [], []
    for category, amount in proposed.items():
        if category in NON_MONTHLY or category in user_direct or category in locks:
            continue
        if history_totals.get(category, 0) <= 0:
            continue
        candidates = [r for r in refs if r['category'] == category and r['tier'] == 'everyday'
                      and (r['scope'] == 'national' or r['region_code'] == region_code)
                      and 0 <= (as_of - date.fromisoformat(r['as_of'])).days <= 180]
        selected_group = (item_groups or {}).get(category)
        # shortcut: unknown subtypes use an explicitly provisional default, upgrade with merchant subtype evidence.
        defaults = {'FASHION_SHOPPING': 'basic_top', 'BEAUTY': 'haircut'}
        group = selected_group or defaults.get(category)
        if group:
            candidates = [r for r in candidates if r['item_group'] == group]
        else:
            candidates = []
        candidates = [r for r in candidates if r['sample_kind'] == 'general']
        local = [r for r in candidates if r['scope'] == 'local']
        candidates = local or candidates
        # Unknown units or mixed currencies/quantities must not produce a guessed floor.
        comparable = [r for r in candidates if r['unit'] in ('1회', '1개', '1벌', '1켤레', '1권')]
        ref = min(comparable, key=lambda r: r['reference_price']) if comparable else None
        if ref is None:
            unpriced.append(category)
        floor = ref['reference_price'] if ref else 0
        baseline = before.get(category, 0)
        excessive_cut = baseline > 0 and (baseline - amount) * 100 >= baseline * 30
        unbuyable = ref is not None and amount < floor
        if unbuyable or excessive_cut:
            retained = math.ceil(baseline * .7 / 5000) * 5000 if excessive_cut else 0
            correction = math.ceil(max(amount, floor, retained, (spent or {}).get(category, 0)) / 5000) * 5000
            issues.append({'category': category, 'reason': 'below_purchase_price' if unbuyable else 'large_reduction',
                           'proposed': amount, 'suggested_amount': correction,
                           'additional_funding_needed': correction - amount,
                           'provisional_item_group': bool(group and not selected_group),
                           'reference': ref, 'requires_reallocation': True})
    return {'status': 'needs_reallocation' if issues else 'insufficient_price_data' if unpriced else 'reviewed',
            'issues': issues, 'unpriced_categories': unpriced,
            'price_coverage': sorted({r['category'] for r in refs if
                (r['scope'] == 'national' or r['region_code'] == region_code)
                and r['sample_kind'] == 'general'
                and 0 <= (as_of - date.fromisoformat(r['as_of'])).days <= 180}),
            'note': '검토 제안이며 월별 필수 소비량이나 충분한 생활비를 보장하지 않음; 기존 엔진 검수·승인 필요'}
