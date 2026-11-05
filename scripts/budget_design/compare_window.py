"""Editable local A/B window; delegates all money calculations to compare_plans."""
from datetime import date
from decimal import Decimal, InvalidOperation
from math import isfinite
import os
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from compare_plans import CATEGORIES, compare, test_input
from simulator import money, transaction_list

ROW_FIELDS = ('m0', 'm1', 'm2', 'rank', 'minimum', 'lock')
POLICY_FIELDS = ('rate', 'increase', 'decrease', 'ceiling', 'cut')
EXCLUDED = ('HOUSING_TELECOM', 'TRAVEL_LODGING')


def example_form(name='shortfall'):
    case = test_input(name)
    inp, profile, policy = case['input'], case['profile'], case['a_policy']
    ranks = {r['category']: str(r['rank']) for r in inp['priorities']}
    months = list(case['months'])
    rows = {}
    for code in CATEGORIES:
        row = {f'm{i}': str(sum(t['net_amount'] for t in case['months'][month]['items']
                               if t['category'] == code)) for i, month in enumerate(months)}
        row.update({f't{i}': ', '.join(str(t['net_amount']) for t in case['months'][month]['items']
                                     if t['category'] == code) for i, month in enumerate(months)})
        row.update(rank=ranks.get(code, ''), minimum=str(inp['minimums'].get(code, '')),
                   lock=str(inp['locks'].get(code, '')), rate=str(policy['increase_rates'].get(code, 10)),
                   increase=str(policy['increase_limits'].get(code, 100000)),
                   decrease=str(policy['decrease_limits'][code]),
                   ceiling=str(policy['ceilings'].get(code, 1000000)), cut=str(case['b_cut_rates'][code]))
        rows[code] = row
    return {'living': str(inp['target'] + case['other_reserved']), 'etc': str(case['other_reserved']),
            'spare': '0',
            'age': str(profile['age']), 'sex': '여성' if profile['sex'] == 'female' else '남성',
            'car': '있음' if profile['has_vehicle'] else '없음', 'income': str(profile['take_home_income']),
            'fixed': str(profile['housing_fixed']), 'total_limit': str(policy['total_limit']),
            'threshold': str(case['increase_threshold'] * 100), 'rows': rows, 'use_llm': False,
            'target_month': '11', 'forecast_down': '', 'forecast_up': '', 'forecast_text_limit': '120',
            'user_note': '', 'forecast_enabled': False,
            'input_mode': 'monthly_totals', 'user_reincluded_one_offs': []}


def percent(value, label, *, positive=False, maximum=None):
    try:
        number = Decimal(value.strip())
        if not number.is_finite() or number < 0 or (positive and number == 0):
            raise InvalidOperation
        if maximum is not None and number > maximum:
            raise InvalidOperation
        result = float(number)
        if not isfinite(result):
            raise InvalidOperation
        return result
    except (InvalidOperation, ValueError, OverflowError):
        suffix = ' (0 초과~100)' if positive and maximum == 100 else ' (0 이상)'
        raise ValueError(f'{label}: 올바른 퍼센트를 입력하세요{suffix}.') from None


def parse_form(form):
    from plan_b_history import CHECKS
    living = money(form['living'], '총생활비')
    etc = money(form['etc'], '기타 확보액', blank=0)
    available_spare = money(form.get('spare', ''), '기존 여윳돈', blank=0)
    if living <= etc:
        raise ValueError('총생활비는 기타 확보액보다 커야 합니다.')
    age = money(form['age'], '나이')
    if not 20 <= age <= 34:
        raise ValueError('A의 ML 비교는 나이 20~34세에서 가능합니다.')
    if form['sex'] not in ('남성', '여성') or form['car'] not in ('있음', '없음'):
        raise ValueError('성별과 차량 보유 여부를 선택하세요.')
    income = money(form['income'], '월 실수령액')
    if income == 0:
        raise ValueError('월 실수령액은 0원보다 커야 합니다.')
    fixed = money(form['fixed'], '확정 주거·통신 고정비')
    if available_spare and living + fixed + available_spare > income:
        raise ValueError('생활비·확정 고정비·기존 여윳돈 합계가 실수령액보다 큽니다.')
    inp = {'target': living - etc, 'minimums': {}, 'locks': {}, 'priorities': []}
    mode = form.get('input_mode', 'monthly_totals')
    if mode not in ('monthly_totals', 'transactions'):
        raise ValueError('소비 입력 모드를 확인하세요.')
    months = {m: {'coverage_complete': True, 'normalization_checks': dict.fromkeys(CHECKS, 'verified'),
                  **({'items': []} if mode == 'transactions' else {'monthly_totals': {}})}
              for m in ('2026-08', '2026-09', '2026-10')}
    policy = {'total_limit': money(form['total_limit'], 'A 총이동 한도'),
              'increase_rates': {}, 'increase_limits': {}, 'decrease_limits': {}, 'ceilings': {}}
    cut_rates = {}
    for code, label in CATEGORIES.items():
        row = form['rows'][code]
        if code in EXCLUDED and (row['minimum'].strip() or row['lock'].strip()):
            raise ValueError(f'{label}은 최소금액·잠금을 받지 않습니다.')
        if code == 'TRAVEL_LODGING' and row['rank'].strip():
            raise ValueError('여행·숙박은 우선순위를 받지 않습니다.')
        rank = money(row['rank'], f'{label} 순위') if row['rank'].strip() else None
        if rank is not None:
            inp['priorities'].append({'category': code, 'rank': rank})
        for field, target in (('minimum', 'minimums'), ('lock', 'locks')):
            if row[field].strip():
                inp[target][code] = money(row[field], f'{label} {field}')
        for i, (month, data) in enumerate(months.items()):
            if mode == 'transactions':
                data['items'].extend(transaction_list(row[f't{i}'], month=month, category=code))
            else:
                data['monthly_totals'][code] = money(row[f'm{i}'], f'{label} {month} 소비액')
        # Inactive categories have no adjustable policy; the zero is never applied to an active donor.
        policy['decrease_limits'][code] = 0 if code in EXCLUDED else money(row['decrease'], f'{label} A 감액 한도')
        if code not in EXCLUDED:
            cut_rates[code] = percent(row['cut'], f'{label} B 감액 한도', positive=True, maximum=100)
            if rank is not None and rank <= 3:
                policy['increase_rates'][code] = percent(row['rate'], f'{label} A 증액률')
                policy['increase_limits'][code] = money(row['increase'], f'{label} A 증액 한도')
                policy['ceilings'][code] = money(row['ceiling'], f'{label} A 증액 상한')
    ranks = sorted(r['rank'] for r in inp['priorities'])
    if not 3 <= len(ranks) <= 9 or ranks != list(range(1, len(ranks) + 1)):
        raise ValueError('순위는 중복·건너뛰기 없이 1·2·3위부터 입력하세요(여행 제외 최대 9위).')
    inp['priorities'].sort(key=lambda row: row['rank'])
    target_month = money(form.get('target_month', '11'), '예산 대상 월')
    if not 1 <= target_month <= 12:
        raise ValueError('예산 대상 월은 1~12로 입력하세요.')
    down, up = form.get('forecast_down', '').strip(), form.get('forecast_up', '').strip()
    band = None
    if down or up:
        band = {'down_percent': percent(down, '예측 하향 범위', maximum=100),
                'up_percent': percent(up, '예측 상향 범위')}
    text_limit = money(form.get('forecast_text_limit', '120'), '설명 최대 글자 수')
    if text_limit == 0:
        raise ValueError('설명 최대 글자 수는 양수여야 합니다.')
    return {'input': inp, 'months': months, 'as_of': date(2026, 11, 3), 'other_reserved': etc,
            'available_spare': available_spare,
            'include_forecast': True, 'target_month': target_month, 'forecast_band': band,
            'user_note': form.get('user_note', ''), 'forecast_text_limit': text_limit,
            'forecast_enabled': form.get('forecast_enabled', False),
            'profile': {'age': age, 'sex': 'female' if form['sex'] == '여성' else 'male',
                        'has_vehicle': form['car'] == '있음', 'take_home_income': income,
                        'housing_fixed': fixed},
            'a_policy': policy, 'b_cut_rates': cut_rates,
            'b_variant': 'one_off_review', 'user_reincluded_one_offs': form.get('user_reincluded_one_offs', []),
            'increase_threshold': percent(form['threshold'], 'B 증가율 기준') / 100}


class ComparisonWindow:
    def __init__(self, root):
        self.root, self.pending = root, None
        root.title('예산 설계 — A·B 직접 입력 비교 (여윳돈 충당)')
        width, height = min(1450, root.winfo_screenwidth()-60), min(950, root.winfo_screenheight()-100)
        root.geometry(f'{width}x{height}')
        root.minsize(min(1100, width), min(760, height))
        frame = ttk.Frame(root, padding=12)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='같은 초기 생활비로 A · B 비교 — B는 부족 시 입력한 여윳돈에서 충당',
                  font=('맑은 고딕', 13, 'bold')).pack(anchor='w')
        ttk.Label(frame, text='합성 조건만 입력하세요. 실제 예산에 저장하지 않습니다. 초기 한도·비율은 테스트 예시입니다.').pack(anchor='w', pady=(2, 8))
        self.fields, self.rows = {}, {c: {} for c in CATEGORIES}
        for fields in ((('living', '총생활비(고정비 제외)'), ('etc', '기타 확보액'),
                        ('age', '나이(20~34세)'), ('sex', '성별'), ('car', '자차')),
                       (('income', '월 실수령액'), ('fixed', '확정 주거·통신 고정비'),
                        ('total_limit', 'A 총이동 한도'), ('threshold', 'B 증가율 기준(%)')),
                       (('target_month', '예산 대상 월'), ('forecast_down', '예측 하향 범위(%)'),
                        ('forecast_up', '예측 상향 범위(%)'), ('forecast_text_limit', '설명 최대 글자 수')),
                       (('spare', '기존 여윳돈(B 생활비 부족 시 사용)'),)):
            bar = ttk.Frame(frame)
            bar.pack(fill='x', pady=3)
            for key, label in fields:
                ttk.Label(bar, text=label).pack(side='left', padx=(0, 5))
                var = self.fields[key] = tk.StringVar()
                if key in ('sex', 'car'):
                    widget = ttk.Combobox(bar, textvariable=var, state='readonly', width=6,
                        values=('남성', '여성') if key == 'sex' else ('없음', '있음'))
                else:
                    widget = ttk.Entry(bar, textvariable=var, width=13)
                widget.pack(side='left', padx=(0, 14))
        note_bar = ttk.Frame(frame)
        note_bar.pack(fill='x')
        ttk.Label(note_bar, text='합성 사용자 상황 메모 (실제 개인정보 입력 금지)').pack(side='left')
        self.fields['user_note'] = tk.StringVar()
        ttk.Entry(note_bar, textvariable=self.fields['user_note']).pack(side='left', fill='x', expand=True)
        mode_bar = ttk.Frame(frame)
        mode_bar.pack(fill='x')
        self.input_mode = tk.StringVar(value='monthly_totals')
        for label, mode in (('월 합계 입력 → 중앙값 사용', 'monthly_totals'), ('거래 목록 입력 → 일회성 제외', 'transactions')):
            ttk.Radiobutton(mode_bar, text=label, value=mode, variable=self.input_mode, command=self.change_mode).pack(side='left', padx=5)
        self.month_entries, self.reincluded, self.candidate_source = [], {}, None
        notebook = ttk.Notebook(frame)
        notebook.pack(fill='x', pady=8)
        for title, fields, labels in (
            ('소비액 · 사용자 설정', ROW_FIELDS,
             ('8월 변동 소비', '9월 변동 소비', '10월 변동 소비', '우선순위', '최소금액', '잠금금액')),
            ('실험용 조정 한도', POLICY_FIELDS,
             ('A 증액률(%)', 'A 증액 한도(원)', 'A 감액 한도(원)', 'A 증액 상한(원)', 'B 감액 한도(%)')),
            ('거래 목록 (쉼표로 구분)', ('t0', 't1', 't2'), ('8월 거래 금액 목록', '9월 거래 금액 목록', '10월 거래 금액 목록'))):
            page = ttk.Frame(notebook, padding=6)
            notebook.add(page, text=title)
            ttk.Label(page, text='카테고리').grid(row=0, column=0, sticky='w')
            for col, label in enumerate(labels, 1):
                ttk.Label(page, text=label).grid(row=0, column=col, sticky='w', padx=4)
                page.columnconfigure(col, weight=1)
            for index, (code, label) in enumerate(CATEGORIES.items(), 1):
                ttk.Label(page, text=label + (' (월 예산 제외)' if code in EXCLUDED else '')).grid(row=index, column=0, sticky='w')
                for col, field in enumerate(fields, 1):
                    var = self.rows[code][field] = tk.StringVar()
                    disabled = ((code in EXCLUDED and field in ('minimum', 'lock', *POLICY_FIELDS))
                                or (code == 'TRAVEL_LODGING' and field == 'rank'))
                    entry = ttk.Entry(page, textvariable=var, width=12, state='disabled' if disabled else 'normal')
                    entry.grid(
                        row=index, column=col, padx=4, pady=2, sticky='ew')
                    if field.startswith('m') and field != 'minimum':
                        self.month_entries.append(entry)
                    if field.startswith('t'):
                        var.trace_add('write', lambda *_: self.update_totals())
        ttk.Label(frame, text='2026년 8~10월 · 고정비 제외 · 거래 예: 10000, 170000@병원A (천 단위 쉼표 금지) · 빈 최소/잠금=미설정, 잠금 0=0원 고정').pack(anchor='w')
        actions = ttk.Frame(frame)
        actions.pack(fill='x', pady=8)
        self.use_llm = tk.BooleanVar(value=False)
        ttk.Checkbutton(actions, text='A LLM 사용', variable=self.use_llm).pack(side='left')
        self.forecast_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(actions, text='B LLM 예측 (합성 입력 외부 전송 · 비용 발생)', variable=self.forecast_enabled).pack(side='left')
        self.run_button = ttk.Button(actions, text='A·B 비교 계산', command=self.run)
        self.run_button.pack(side='left', padx=12)
        self.presets = ttk.Combobox(actions, values=('생활비 부족', '예산 여유', '의료비 급증'), state='readonly', width=12)
        self.presets.current(0)
        self.presets.pack(side='left')
        self.example_button = ttk.Button(actions, text='예시 불러오기', command=self.load_example)
        self.example_button.pack(side='left', padx=5)
        self.status = tk.StringVar()
        ttk.Label(frame, textvariable=self.status, wraplength=1350).pack(anchor='w')
        columns = ('category', 'history', 'median', 'basis', 'a', 'b', 'delta', 'forecast', 'ml_raw')
        self.details = tk.Text(frame, height=4, wrap='word', state='disabled')
        self.details.pack(side='bottom', fill='x')
        result_tabs = ttk.Notebook(frame)
        result_tabs.pack(fill='both', expand=True, pady=6)
        results = ttk.Frame(result_tabs)
        result_tabs.add(results, text='A·B 결과 / 기준값 비교')
        candidate_page = ttk.Frame(result_tabs)
        result_tabs.add(candidate_page, text='일회성 후보 · 다시 포함')
        canvas = tk.Canvas(candidate_page, highlightthickness=0)
        candidate_scroll = ttk.Scrollbar(candidate_page, orient='vertical', command=canvas.yview)
        canvas.configure(yscrollcommand=candidate_scroll.set)
        candidate_scroll.pack(side='right', fill='y')
        canvas.pack(side='left', fill='both', expand=True)
        self.candidate_frame = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=self.candidate_frame, anchor='nw')
        self.candidate_frame.bind('<Configure>', lambda event: canvas.configure(scrollregion=canvas.bbox('all')))
        self.table = ttk.Treeview(results, columns=columns, show='headings', height=11)
        for key, label in zip(columns, ('카테고리', '원래 평균(원)', '중앙값(원)', 'B 출발 기준(원)', 'A 최종 예산(원)', 'B 규칙 예산(원)', 'B − A(원)', 'B LLM 예측(원)', '조정 전 ML(원)')):
            self.table.heading(key, text=label)
            self.table.column(key, width=115, anchor='w' if key == 'category' else 'e')
        scroll = ttk.Scrollbar(results, orient='vertical', command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right', fill='y')
        self.table.pack(side='left', fill='both', expand=True)
        self.load_example()

    def form(self):
        return {**{k: v.get() for k, v in self.fields.items()}, 'use_llm': self.use_llm.get(),
                'forecast_enabled': self.forecast_enabled.get(),
                'input_mode': self.input_mode.get(),
                'user_reincluded_one_offs': [key for key, var in self.reincluded.items() if var.get()],
                'rows': {c: {k: v.get() for k, v in row.items()} for c, row in self.rows.items()}}

    def clear(self):
        self.table.delete(*self.table.get_children())
        self.set_details('')

    def set_details(self, text):
        self.details.configure(state='normal')
        self.details.delete('1.0', 'end')
        self.details.insert('1.0', text)
        self.details.configure(state='disabled')

    def load_example(self):
        if self.pending is not None:
            return
        form = example_form(('shortfall', 'surplus', 'medical')[self.presets.current()])
        self.input_mode.set('monthly_totals')
        self.reincluded = {}
        self.candidate_source = None
        for child in self.candidate_frame.winfo_children():
            child.destroy()
        for key, var in self.fields.items():
            var.set(form[key])
        for code, row in self.rows.items():
            for key, var in row.items():
                var.set('' if code in EXCLUDED and key in ('minimum', 'lock', *POLICY_FIELDS) else form['rows'][code][key])
        self.clear()
        self.change_mode()
        self.status.set('금액과 설정을 수정한 뒤 A·B 비교 계산을 누르세요.')

    def run(self):
        if self.pending is not None:
            return
        self.clear()
        snapshot = self.form()
        source = self.history_source(snapshot)
        if source != self.candidate_source:
            for var in self.reincluded.values():
                var.set(False)
            snapshot['user_reincluded_one_offs'] = []
        try:
            case = parse_form(snapshot)
        except ValueError as error:
            self.status.set(str(error))
            return
        results = self.pending = queue.Queue()
        self.run_button.state(['disabled'])
        self.example_button.state(['disabled'])
        self.status.set('A·B 계산 중… 입력을 바꾸면 이전 조건의 결과는 표시하지 않습니다.')
        def work():
            try:
                results.put((compare(case, use_llm=snapshot['use_llm']), None))
            except Exception as error:
                results.put((None, type(error).__name__))
        threading.Thread(target=work, daemon=True).start()
        self.root.after(50, lambda: self.poll(snapshot))

    def poll(self, snapshot):
        try:
            result, error = self.pending.get_nowait()
        except queue.Empty:
            self.root.after(50, lambda: self.poll(snapshot))
            return
        self.pending = None
        self.run_button.state(['!disabled'])
        self.example_button.state(['!disabled'])
        if self.form() != snapshot:
            self.status.set('입력이 변경되어 이전 결과를 폐기했습니다. 다시 계산하세요.')
        elif error:
            self.status.set(f'실행 실패: {error}')
        else:
            self.candidate_source = self.history_source(snapshot)
            self.show_result(result)

    @staticmethod
    def history_source(form):
        return (form['input_mode'], tuple(tuple(row.get(k, '') for k in ('m0','m1','m2','t0','t1','t2'))
                                         for row in form['rows'].values()))

    def change_mode(self):
        for entry in self.month_entries:
            entry.configure(state='readonly' if self.input_mode.get() == 'transactions' else 'normal')
        self.update_totals()

    def update_totals(self):
        if self.input_mode.get() != 'transactions':
            return
        for code, row in self.rows.items():
            for i, month in enumerate(('2026-08', '2026-09', '2026-10')):
                if f't{i}' not in row:
                    continue
                try:
                    items = transaction_list(row[f't{i}'].get(), month=month, category=code)
                    row[f'm{i}'].set(str(sum(t['net_amount'] for t in items)))
                except ValueError:
                    row[f'm{i}'].set('입력 확인')

    def show_result(self, result):
        self.clear()
        a, b = result['a'].get('allocations'), result['b'].get('allocations')
        forecast_result = result.get('b_forecast', {})
        forecast = forecast_result.get('allocations') if forecast_result.get('llm_status') == 'completed' else None
        self.table.heading('forecast', text='B LLM 예측(원)' if forecast is not None else
                           'B LLM 결과 검수 실패' if forecast_result.get('attempts',0) else 'B LLM 호출 안 됨')
        history = result['history'].get('categories', {})
        for code, label in CATEGORIES.items():
            data = history.get(code, {})
            avg = data.get('average')
            self.table.insert('', 'end', values=(label, '—' if avg is None else f'{float(avg):,.0f}',
                '—' if not data else f"{float(data['median']):,.0f}",
                '—' if not data else f"{float(data['baseline_amount']):,.0f}",
                '—' if a is None else f'{a[code]:,}', '—' if b is None else f'{b[code]:,}',
                '—' if a is None or b is None else f'{b[code]-a[code]:+,}',
                '—' if forecast is None else f'{forecast[code]:,}',
                '—' if not result.get('ml_reference') else f"{float(result['ml_reference'][code]):,.0f}"))
        self.table.insert('', 'end', values=('배분 합계(기타 제외)', '—', '—', '—',
            '—' if a is None else f'{sum(a.values()):,}', '—' if b is None else f'{sum(b.values()):,}',
            '—' if a is None or b is None else f'{sum(b.values())-sum(a.values()):+,}',
            '—' if forecast is None else f'{sum(forecast.values()):,}'))
        if b is not None:
            self.table.insert('', 'end', values=('여윳돈 → 생활비', '—', '—', '—', '0',
                f"{result['b'].get('spare_transfer', 0):,}", '—',
                '—' if forecast is None else f"{forecast_result.get('spare_transfer', 0):,}"))
            self.table.insert('', 'end', values=('남는 여윳돈', '—', '—', '—', '—',
                f"{result['b'].get('total_spare', result['b'].get('unallocated', 0)):,}", '—',
                '—' if forecast is None else f"{forecast_result.get('total_spare', forecast_result['unallocated']):,}"))
        labels = {'proposed': '계산 완료', 'infeasible': '설정 충돌', 'needs_data': '이력 확인 필요',
                  'needs_policy': '정책 설정 필요', 'mixed': '소비 범위 확인 필요',
                  'needs_upper_allocation_review': '생활비 배분 재검토 필요', 'error': '실행 오류'}
        statuses, details = [], []
        if 'selection' in result:
            choice = result['selection']
            why = {'history_not_ready': '최근 3개월 이력 부족·미확인',
                   'transaction_counts_unavailable': '거래 건수 확인 불가',
                   'sparse_transactions': f"카테고리별 최대 {choice['max_category_count']}건(10건 이하)",
                   'sufficient_transactions': f"카테고리별 최대 {choice['max_category_count']}건(11건 이상)"}[choice['reason']]
            statuses.append(f"자동 선택: {choice['plan'].upper()} — {why}")
            details.append(f"자동 선택: {choice['plan'].upper()} / {why} / 실행 경로: {result['selected_variant']}")
        for name in ('a', 'b', *(['b_forecast'] if 'b_forecast' in result else [])):
            item = result[name]
            mode = item.get('selection_source', item.get('llm_status', ''))
            mode = {'rule': '규칙', 'llm_tools': 'LLM', 'completed': 'LLM', 'not_run': 'LLM 미실행',
                    'rule_fallback': '규칙 대체', 'fallback': '규칙 대체', 'single_candidate': '단일 후보'}.get(mode, mode)
            if name == 'b':
                statuses.append(f"B 규칙: {labels.get(item['status'], item['status'])} / {item['seconds']:.1f}초")
            elif name == 'b_forecast' and item.get('reason') == 'forecast_band_infeasible':
                required = item['forecast_required']
                shortage = item['forecast_shortfall']
                message = f"B LLM 미실행: 예측 하한 합계 {required:,}원이 생활비보다 {shortage:,}원 큽니다. 기존 여윳돈 또는 하향 범위를 확인하세요."
                statuses.append(message)
                details.insert(0, message + ' 연결 오류가 아니며, B 규칙 결과는 별도 열에 표시합니다.')
            elif name == 'b_forecast':
                attempts = item.get('attempts',0)
                if item.get('llm_status') == 'completed':
                    statuses.append(f"B LLM 예측: 호출 {attempts}회 · 검수 통과 / {item['seconds']:.1f}초")
                elif attempts:
                    violations = ', '.join(item.get('violation_types',[]))
                    statuses.append(f"B LLM 예측: 호출 {attempts}회 · 결과 사용 불가 ({violations})")
                else:
                    reason = item.get('reason') or item.get('fallback') or item['status']
                    message = {'forecast_band_required':'예측 하향·상향 범위 입력 필요',
                        'forecast_disabled':'B LLM 예측 체크 꺼짐', 'synthetic_only':'합성 입력만 외부 호출 가능',
                        'llm_model_missing':'LLM_MODEL 설정 없음', 'openai_key_missing':'OPENAI_API_KEY 설정 없음',
                        'ml_reference_unavailable':'ML 하한 조회 실패', 'shortfall_exceeds_cut_limits':'감액 한도 안에서 생활비를 맞출 수 없음',
                        'ml_floor_unaffordable':'최소액·잠금·ML 하한 합이 생활비 초과',
                        'history_not_ready':'소비 이력 준비 안 됨'}.get(reason, reason)
                    statuses.append(f'B LLM 예측: 호출 안 됨 — {message}')
            else:
                statuses.append(f"{name.upper()}: {labels.get(item['status'], item['status'])} / {mode} / {item['seconds']:.1f}초")
            if item.get('reason') or item.get('fallback'):
                details.append(f"{name.upper()} 사유: {item.get('reason') or item.get('fallback')}")
            if item.get('ml_floors'):
                details.append(f"{name.upper()} ML 감액 하한: " + ', '.join(
                    f'{CATEGORIES[c]} {amount:,}원' for c, amount in item['ml_floors'].items()
                    if c not in item.get('ml_floor_ignored_locked', [])))
            if 'available_spare' in item:
                details.append(f"{name.upper()} 기존 여윳돈 {item['available_spare']:,}원 / 생활비 충당 {item['spare_transfer']:,}원 / 남은 기존 여윳돈 {item['spare_remaining']:,}원")
            shortage = item.get('funding_shortfall') or item.get('shortfall') or item.get('remaining_shortfall')
            if shortage and item['status'] != 'proposed':
                details.append(f"{name.upper()}: 감액 한도·보호 기준을 지키고 기존 여윳돈을 사용해도 {shortage:,}원이 부족해요. 생활비 또는 설정을 검토해 주세요.")
            if name == 'b_forecast':
                details.append(f"B 예측: 시도 {item.get('attempts', 0)} / 검수 실패 {item.get('validation_failures', 0)} / 토큰 {item.get('token_usage')} / 비용은 제공자 청구 확인")
            details.extend(f"{name.upper()}: {row['text']}" for row in item.get('explanations', item.get('baseline_explanations', [])) if row['text'])
            if isinstance(item.get('summary'), list):
                details.extend(f"{name.upper()}: {row['text']}" for row in item['summary'])
            trace = item.get('langsmith', {})
            if trace.get('trace_id'):
                details.append(f"{name.upper()} 추적: {trace['status']} / {trace['trace_id']}")
        self.status.set('    |    '.join(statuses))
        details.insert(0, ('B 여윳돈(기존 잔액+배분 잔액): ' + f"{result['b'].get('total_spare', result['b'].get('unallocated', 0)):,}원 / " if b is not None else '') +
                       'B 기준: ' + result['history'].get('baseline', '-') +
                       (' / 거래 내역 없음 → 월 합계 중앙값 사용' if result['history'].get('one_off_unavailable') else ''))
        self.reincluded = {}
        for child in self.candidate_frame.winfo_children():
            child.destroy()
        candidates = result['b'].get('one_off_candidates', [])
        ttk.Label(self.candidate_frame, text='체크한 뒤 다시 계산하면 평소 소비에 포함합니다. 가맹점 키는 표시하지 않습니다.').pack(anchor='w')
        if not candidates:
            ttk.Label(self.candidate_frame, text='월 합계 모드에서는 판정할 수 없습니다.' if result['history'].get('one_off_unavailable') else '일회성 후보가 없습니다.').pack(anchor='w')
        for candidate in candidates:
            var = self.reincluded[candidate['id']] = tk.BooleanVar(value=candidate['reincluded_by_user'])
            score = '나머지 거래 없음' if candidate['score'] is None else f"{candidate['score']:.3f}"
            state = '다른 달에도 결제 → 포함' if candidate['excluded_by_recurrence'] else '제외' if candidate['excluded'] else '포함'
            text = f"다시 포함 | {CATEGORIES[candidate['category']]} {candidate['month']} {candidate['amount']:,}원 | {candidate['method']} · score {score} | {state}"
            ttk.Checkbutton(self.candidate_frame, text=text, variable=var,
                            state='disabled' if candidate['excluded_by_recurrence'] else 'normal').pack(anchor='w', pady=4)
        details.append('B는 예산이 남으면 설정·하한 반영 후 여윳돈으로 남깁니다. 부족한 경우에는 우선순위에 따라 감액합니다.')
        self.set_details('\n'.join(details))


def main():
    root = tk.Tk()
    try:
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
        import django
        django.setup()
        ComparisonWindow(root)
    except Exception as error:
        messagebox.showerror('비교 창 실행 실패', f'실행 환경을 확인하세요: {type(error).__name__}', parent=root)
        root.destroy()
        return
    root.mainloop()


if __name__ == '__main__':
    main()
