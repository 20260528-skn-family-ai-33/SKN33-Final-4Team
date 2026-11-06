"""입력과 정답의 순서를 한 곳에서 관리한다."""
CATEGORIES = ('식비', '주거·통신', '교통·차량', '생활용품', '패션·쇼핑',
              '의료·건강', '뷰티·미용', '교육', '문화·여가', '여행·숙박', '기타')
CODES = ('food', 'housing_communication', 'transport_vehicle', 'household_goods',
         'fashion_shopping', 'medical_health', 'beauty_care', 'education',
         'culture_leisure', 'travel_accommodation', 'other')
LABELS = ('Food', 'Housing / communication', 'Transport', 'Household goods', 'Fashion',
          'Health', 'Beauty', 'Education', 'Culture / leisure', 'Travel', 'Other')
AMOUNTS = tuple('target_amount_' + c for c in CODES)
RATIOS = tuple('target_ratio_' + c for c in CODES)
PROFILE = ('age', 'sex', 'net_income', 'vehicle_count', 'fixed_expense_amount')
BUDGET = 'living_budget_amount'
