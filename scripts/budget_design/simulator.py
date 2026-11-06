"""Local manual/ML budget simulator. Run with the repository's .venv Python."""
import sys
import json
import math
import queue
import subprocess
import threading
import os
from pathlib import Path
import tkinter as tk
from tkinter import ttk

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from inference import CATEGORIES, predict_ml

FIELDS = {"rank": "순위", "draft": "입력 초안(원)", "minimum": "최소 금액",
          "lock": "잠금 금액", "rate": "증액률(%)", "increase": "증액 한도", "decrease": "감액 한도",
          "ceiling": "증액 중단 상한", "warning": "최소액 경고선"}
DISCRETIONARY = {"FASHION_SHOPPING", "BEAUTY", "CULTURE_LEISURE"}


def transaction_list(text, *, month, category):
    """Local synthetic transaction entry. Commas separate purchases, not thousands."""
    if not isinstance(text, str):
        raise ValueError('거래 목록은 금액, 금액@가맹점키 형식으로 입력하세요.')
    if not text.strip():
        return []
    rows = []
    for token in text.split(','):
        pieces = token.strip().split('@')
        value = pieces[0].strip()
        if (len(pieces) > 2 or not value.isascii() or not value.isdecimal()
                or (len(pieces) == 2 and not pieces[1].strip())):
            raise ValueError('거래 목록은 10000, 170000@병원A 형식으로 입력하세요. 천 단위 쉼표는 쓰지 않습니다.')
        row = {'occurred_on': month + '-15', 'category': category, 'net_amount': int(value),
               'transaction_type': 'spending', 'funding': 'living'}
        if len(pieces) == 2:
            row['merchant_key'] = pieces[1].strip().casefold()
        rows.append(row)
    return rows


def money(text, label, *, blank=None):
    value = text.strip().replace(",", "")
    if not value and blank is not None:
        return blank
    if not value or not value.isascii() or not value.isdecimal():
        raise ValueError(f"{label}: 0 이상의 정수를 원 단위로 입력하세요.")
    return int(value)


def example():
    """Editable fixture values, never production policy defaults."""
    ranks = {"FOOD": "1", "EDUCATION": "2", "MEDICAL_HEALTH": "3"}
    return {"living": "1050000", "etc_minimum": "50000", "etc_lock": "",
            "total_limit": "100000",
            "profile": {"age": "27", "sex": "여성", "car": "없음",
                        "income": "3000000", "housing_fixed": "700000"},
            "rows": {code: {
                "rank": ranks.get(code, ""),
                "draft": "0" if code in ("HOUSING_TELECOM", "TRAVEL_LODGING") else "300000" if code == "FOOD" else "100000",
                "minimum": "", "lock": "", "rate": "20", "increase": "1000000", "decrease": "50000",
                "ceiling": "1000000", "warning": "",
            } for code in CATEGORIES}}




def calculate(form, *, use_ml=False, use_llm=False):
    """Use the existing engine; optional ML runs locally without DB/API access."""
    living = money(form["living"], "총생활비")
    etc_minimum = money(form["etc_minimum"], "기타 최소 금액", blank=0)
    etc = money(form["etc_lock"], "기타 잠금 금액") if form["etc_lock"].strip() else etc_minimum
    if etc < etc_minimum:
        raise ValueError("기타 잠금 금액이 기타 최소 금액보다 작습니다.")
    if etc > living:
        raise ValueError(f"기타 금액이 총생활비보다 {etc - living:,}원 많습니다. 설정을 수정하세요.")
    data = {"target": living - etc, "minimums": {}, "locks": {}, "priorities": []}
    draft, increases, decreases, ceilings, warnings, rates = {}, {}, {}, {}, {}, {}
    for code, name in CATEGORIES.items():
        row = form["rows"][code]
        if code in ('TRAVEL_LODGING', 'HOUSING_TELECOM') and any(row[f].strip() for f in ('minimum', 'lock')):
            raise ValueError(f'{name}은 최초 월 예산에서 최소 금액·잠금을 받지 않습니다.')
        if not use_ml:
            draft[code] = money(row["draft"], f"{name} 초안")
        if row["rate"].strip():
            from decimal import Decimal, InvalidOperation
            try:
                rate = Decimal(row["rate"].strip())
                if not rate.is_finite() or rate < 0:
                    raise InvalidOperation
                rates[code] = float(rate)
            except (InvalidOperation, ValueError, OverflowError):
                raise ValueError(f"{name} 증액률: 0 이상의 유한한 숫자를 입력하세요.") from None
        for field, dest in (("minimum", data["minimums"]), ("lock", data["locks"]),
                            ("increase", increases), ("ceiling", ceilings)):
            if row[field].strip():
                dest[code] = money(row[field], f"{name} {FIELDS[field]}")
        decreases[code] = money(row["decrease"], f"{name} 감액 한도")
        if row["rank"].strip():
            if code == 'TRAVEL_LODGING':
                raise ValueError('여행·숙박은 우선순위를 받지 않습니다.')
            data["priorities"].append({"category": code, "rank": money(row["rank"], f"{name} 순위")})
        if code in DISCRETIONARY and row["warning"].strip():
            warnings[code] = money(row["warning"], f"{name} 최소액 경고선")
    ranks = sorted(row["rank"] for row in data["priorities"])
    if not 3 <= len(ranks) <= 9 or ranks != list(range(1, len(ranks) + 1)):
        raise ValueError("순위는 중복 없이 1·2·3부터 연속으로 입력하세요. 여행 제외 최대 9위입니다.")
    if not use_ml and sum(draft.values()) != data["target"]:
        raise ValueError(f"입력 초안 합계 {sum(draft.values()):,}원 ≠ 기타 제외 배분액 {data['target']:,}원. 초안 금액을 맞춰 주세요.")
    active = [c for c in CATEGORIES if c not in ('TRAVEL_LODGING', 'HOUSING_TELECOM')]
    for row in data["priorities"]:
        code = row["category"]
        if row["rank"] <= 3 and code in active and code not in data["locks"] and (code not in increases or code not in ceilings or code not in rates):
            raise ValueError(f"{CATEGORIES[code]}: 실험용 증액률·증액 한도·상한을 입력하세요.")
    policy = {"total_limit": money(form["total_limit"], "총이동 한도"),
              "increase_rates": rates, "increase_limits": increases, "decrease_limits": decreases, "ceilings": ceilings}

    # Standalone, minimal settings: deliberately do not load project .env/DB settings.
    from django.conf import settings
    if not settings.configured:
        settings.configure(SECRET_KEY="local-synthetic-simulator", AUTH_USER_MODEL="users.User",
                           INSTALLED_APPS=["django.contrib.auth", "django.contrib.contenttypes",
                                           "users", "budgets", "finance", "chat"],
                           DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}})
    import django
    django.setup()
    from budgets.design_candidates import build_candidates, ml_profile
    from budgets.recommend import allocation_conflicts
    # Integer weights preserve the exact typed ML amounts, including .5 round ties.
    ratios = draft if data["target"] else None
    ml = None
    if use_ml:
        draft = dict.fromkeys(CATEGORIES, 0)
        ratios = None
        effective = {**data, 'locks': {**data['locks'], **{c: 0 for c in CATEGORIES if c not in active}}}
        if data['target'] and not allocation_conflicts(effective) and any(c not in data['locks'] for c in active):
            fields = form['profile']
            if fields['sex'] not in ('남성', '여성') or fields['car'] not in ('없음', '있음'):
                raise ValueError('ML 성별·차량 보유 값을 선택하세요.')
            raw_profile = {
                'age': money(fields['age'], '나이'), 'sex': {'남성':'male', '여성':'female'}[fields['sex']],
                'has_vehicle': fields['car'] == '있음', 'target': data['target'],
                'take_home_income': money(fields['income'], '월 실수령액'),
                'housing_fixed': money(fields['housing_fixed'], '확정 주거통신 고정비'),
            }
            try:
                mapped = ml_profile(raw_profile)
            except ValueError:
                raise ValueError('ML은 나이 20~34세, 양수 월 실수령액·배분액이 필요합니다.') from None
            ml = predict_ml(mapped)
            ratios = {code: ml['ratios'][name] for code, name in CATEGORIES.items()}
            draft = {code: ml['draft_amounts'][name] for code, name in CATEGORIES.items()}
    car = form['profile'].get('car')
    reality_profile = {'has_vehicle': car == '있음'} if car in ('있음', '없음') else None
    result = build_candidates(data, ratios, policy=policy, warning_thresholds=warnings,
                              reality_profile=reality_profile)
    selected = None
    if result['status'] == 'proposed':
        if use_llm:
            from dotenv import dotenv_values
            from budgets.design_selection import choose_budget
            from budgets.design_tracing import CONFIG_KEYS
            # Read allowlisted API/tracing settings without loading DB credentials into this process.
            local_env = dotenv_values(ROOT / '.env')
            key = os.environ.get('OPENAI_API_KEY') or local_env.get('OPENAI_API_KEY', '')
            trace_config = {k: os.environ.get(k, local_env.get(k) or '') for k in CONFIG_KEYS}
            selected = choose_budget(data, result, ml_ratios=ratios, api_key=key or '',
                                     living_budget=living, other_reserved=etc, trace_config=trace_config,
                                     reality_profile=reality_profile)
        else:
            from budgets.design_candidates import select_candidate
            chosen = select_candidate(data, result['candidates'])
            selected = {'candidate_id': chosen['candidate_id'], 'allocations': chosen['result']['allocations'],
                        'selection_source': 'rule', 'fallback': chosen['fallback'],
                        'explanations': chosen['explanations']}
    display = None
    if result['status'] == 'proposed':
        from budgets.design_units import finalize_budget
        def final(view):
            return finalize_budget(data, {'status': 'proposed', 'engine': result,
                'system_floors': result.get('system_floors', {}), **view}, policy=policy,
                other_reserved=etc, other_locked=bool(form['etc_lock'].strip()))
        candidates = [final({**c['result'], 'result': c['result'], 'explanations': c['explanations']}) for c in result['candidates']]
        display = {'candidates': candidates, 'selected': final(selected)}
    return {"living": living, "etc": etc, "draft": draft, "engine": result, 'display': display,
            "source": 'ml' if ml else 'ml_skipped' if use_ml else 'manual', "ml": ml,
            "selected": selected}


def describe(result):
    engine = result["engine"]
    lines = []
    if result['source'] == 'ml':
        lines.append('실제 로컬 ML 초안 사용 · 화면 초안은 소수 둘째 자리 표시, 계산에는 원 비율 그대로 전달')
        lines.append(f"모델: {result['ml']['artifact_id']} / 평균비율 대체: {result['ml']['used_fallback']} / 음수 보정: {result['ml']['negative_clipped']}")
    elif result['source'] == 'ml_skipped':
        lines.append('배분액 0원·조건 충돌·전체 잠금 중 하나에 해당하여 ML 호출을 생략했습니다. 초안 칸은 미사용입니다.')
    if engine["status"] == "needs_upper_allocation_review":
        lines.append(f"시스템 하한을 확보하기에 {engine['shortfall']:,}원이 부족합니다. 생활비를 다시 검토해 주세요.")
    elif engine["status"] == "infeasible":
        lines.append("조건 충돌: 후보를 만들 수 없습니다. 설정 수정 또는 예산 조정 챗봇 상담이 필요합니다.")
        for conflict in engine["result"]["conflicts"]:
            if "shortfall" in conflict:
                lines.append(f"부족액: {conflict['shortfall']:,}원")
            elif "category" in conflict:
                lines.append(f"{CATEGORIES[conflict['category']]}: 잠금 금액이 최소 금액보다 작습니다.")
            elif "difference" in conflict:
                lines.append(f"전체 잠금액과 배분액의 차이: {conflict['difference']:+,}원")
    else:
        moved = sum(t["amount"] for t in engine["transfers"])
        lines.append(f"개인화 후보 비교 정보 — 이동액: {moved:,}원 / 기타: {result['etc']:,}원 / 기타 포함 총액: {result['living']:,}원")
        lines.extend(f"{CATEGORIES[c]} 증액 목표: {amount:,}원 (입력 초안 × 증액률, 천 원 단위 반올림)"
                     for c, amount in engine["increase_targets"].items())
        lines.extend(f"{CATEGORIES[t['from']]} → {CATEGORIES[t['to']]}: {t['amount']:,}원" for t in engine["transfers"])
        labels = {"category_limit": "항목 증액 한도 또는 상한 도달", "target_or_category_limit": "비율 증액 목표 또는 항목 한도 도달",
                  "zero_rate_target": "초안 0원 또는 반올림한 증액 목표 0원", "total_limit": "총이동 한도 도달", "no_funding": "감액 가능한 재원 소진(천 원 미만 포함)"}
        lines.extend(f"{CATEGORIES[c]} 중단: {labels[reason]}" for c, reason in engine["stop_reasons"].items())
    for code, amount in engine.get('system_floors', {}).items():
        lines.append(f"시스템 하한: {CATEGORIES[code]} {amount:,}원")
    for code in engine.get('floor_applied', []):
        lines.append(f"{CATEGORIES[code]}: 시스템 하한 적용")
    for code in engine.get('floor_ignored_locked', []):
        lines.append(f"{CATEGORIES[code]}: 사용자 잠금 금액 우선")
    for warning in engine["warnings"]:
        if warning["code"] in ("high_minimum", "minimum_above_ceiling"):
            label = "최소 요청액이 경고선을 초과" if warning["code"] == "high_minimum" else "최소 요청액이 증액 중단 상한을 초과"
            lines.append(f"안내: {CATEGORIES[warning['category']]} {label} ({warning['amount']:,}원 / 기준 {warning['reference']:,}원). 자동 감축하지 않습니다.")
    selected = (result.get('display') or {}).get('selected', result.get('selected'))
    if selected:
        lines.append(f"처리 결과: {selected['candidate_id']} / 방식: {selected['selection_source']}")
        lines.extend(e['text'] for e in selected.get('explanations', []) if e['text'])
        if selected['fallback']:
            lines.append(f"규칙 대체: {selected['fallback']}")
    return "\n".join(lines)


class Simulator:
    def __init__(self, root):
        self.root = root
        self.pending = None
        root.title("예산 설계 — 직접 입력 / 실제 ML 테스트 (로컬 전용)")
        root.geometry("1440x960")
        root.minsize(1150, 780)
        frame = ttk.Frame(root, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="ML 초안 → LLM 조정 판단 → 서버 도구 계산·검수 · 저장 없음", font=("맑은 고딕", 12, "bold")).pack(anchor="w")
        ttk.Label(frame, text="초기 숫자는 테스트 예시입니다. 최종 예산은 만 원 우선·필요 시 5천 원 단위이며, 사용자 잠금액은 그대로 유지합니다.").pack(anchor="w", pady=(4, 8))
        top = ttk.Frame(frame)
        top.pack(fill="x")
        self.top = {}
        for key, label in (("living", "총생활비(고정비 제외)"), ("etc_minimum", "기타 최소액"),
                           ("etc_lock", "기타 잠금액(선택)"), ("total_limit", "실험 총이동 한도")):
            ttk.Label(top, text=label).pack(side="left", padx=(0, 5))
            var = tk.StringVar()
            self.top[key] = var
            ttk.Entry(top, textvariable=var, width=12).pack(side="left", padx=(0, 14))
        profile_bar = ttk.Frame(frame)
        profile_bar.pack(fill='x', pady=(8, 0))
        self.profile = {}
        for key, label in (('age','나이(20~34)'), ('sex','성별'), ('car','차량'),
                           ('income','월 실수령액(원)'), ('housing_fixed','확정 주거통신 고정비(원)')):
            ttk.Label(profile_bar, text=label).pack(side='left', padx=(0, 5))
            var = tk.StringVar()
            self.profile[key] = var
            if key in ('sex','car'):
                ttk.Combobox(profile_bar, textvariable=var, values=('남성','여성') if key == 'sex' else ('없음','있음'),
                             state='readonly', width=5).pack(side='left', padx=(0, 14))
            else:
                ttk.Entry(profile_bar, textvariable=var, width=12).pack(side='left', padx=(0, 14))
        ttk.Label(frame, text="ML 실행은 아래 직접 입력 초안을 사용하지 않습니다. 합성 조건만 입력하세요. ML 10개 초안 → 주거·통신·여행 기본 제외 → 최소·잠금·개인화 순서입니다.").pack(anchor='w', pady=(6, 0))
        ttk.Label(frame, text="직접 입력 초안 합계 = 총생활비 − 기타. 여행은 최초 월 예산에서 순위·최소 금액·잠금을 받지 않으며 항상 제외합니다.").pack(anchor="w", pady=(6, 4))
        grid = ttk.Frame(frame)
        grid.pack(fill="x")
        ttk.Label(grid, text="카테고리").grid(row=0, column=0, sticky="w")
        for column, label in enumerate(FIELDS.values(), 1):
            ttk.Label(grid, text=label).grid(row=0, column=column, padx=4, sticky="w")
            grid.columnconfigure(column, weight=1)
        self.rows = {}
        for row, (code, name) in enumerate(CATEGORIES.items(), 1):
            ttk.Label(grid, text=name, width=13).grid(row=row, column=0, pady=3, sticky="w")
            self.rows[code] = {}
            for column, field in enumerate(FIELDS, 1):
                var = tk.StringVar()
                self.rows[code][field] = var
                if (field == "warning" and code not in DISCRETIONARY) or (code == 'TRAVEL_LODGING' and field != 'draft') or (code == 'HOUSING_TELECOM' and field in ('minimum', 'lock')):
                    ttk.Label(grid, text="—").grid(row=row, column=column)
                else:
                    ttk.Entry(grid, textvariable=var, width=6 if field in ("rank", "rate") else 11).grid(row=row, column=column, padx=3, sticky="ew")
        ttk.Label(frame, text="최소액·잠금액 빈칸=설정 없음. 잠금 0=0원 고정. 경고선은 쇼핑·뷰티·문화만 선택 입력. 상한은 추가 증액의 중단 기준이며 기본안 자체를 깎지는 않습니다.").pack(anchor="w", pady=(4, 8))
        actions = ttk.Frame(frame)
        actions.pack(fill="x")
        self.use_llm = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text='LLM 예산 조정 (외부 전송: 생활비·항목 금액·순위·보호 조건·한도 / 최대 3회 호출 / 합성 입력 전용)',
                        variable=self.use_llm).pack(anchor='w')
        self.buttons = []
        for text, command in (('직접 입력 계산', self.run), ('ML로 예측·계산', self.run_ml), ('예시 다시 불러오기', self.load_example),
                              ('A·B 거래별 비교 창', self.open_comparison)):
            button = ttk.Button(actions, text=text, command=command)
            button.pack(side='left', padx=(0, 10))
            self.buttons.append(button)
        self.status = tk.StringVar(value="입력 후 ‘금액 계산하기’를 누르세요.")
        ttk.Label(actions, textvariable=self.status).pack(side="left", padx=8)
        columns = ("category", "draft", "base", "priority", "delta", "recommended")
        self.table = ttk.Treeview(frame, columns=columns, show="headings", height=11)
        for key, label in zip(columns, ("카테고리", "사용한 초안", "최소·잠금 반영 기본안", "규칙 개인화안", "규칙 개인화 − 기본", "최종 추천 금액")):
            self.table.heading(key, text=label)
            self.table.column(key, width=175, anchor="e" if key != "category" else "w")
        self.table.pack(fill="x", pady=(10, 8))
        self.details = tk.Text(frame, height=7, wrap="word", font=("맑은 고딕", 10), state="disabled")
        self.details.pack(fill="both", expand=True)
        self.load_example()

    def open_comparison(self):
        subprocess.Popen([sys.executable, str(Path(__file__).with_name('compare_window.py'))],
                         creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

    def load_example(self):
        values = example()
        for key, var in self.top.items():
            var.set(values[key])
        for key, var in self.profile.items():
            var.set(values['profile'][key])
        for code, row in self.rows.items():
            for field, var in row.items():
                var.set(values["rows"][code][field])
        self.clear()
        self.status.set("테스트 예시를 불러왔습니다. 숫자를 바꾸거나 바로 계산하세요.")

    def clear(self):
        self.table.delete(*self.table.get_children())
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.configure(state="disabled")

    def form(self):
        form = {key: var.get() for key, var in self.top.items()}
        form['profile'] = {key: var.get() for key, var in self.profile.items()}
        form['use_llm'] = self.use_llm.get()
        form["rows"] = {code: {key: var.get() for key, var in row.items()} for code, row in self.rows.items()}
        return form

    def run_ml(self):
        self.run_async(use_ml=True)

    def run_async(self, *, use_ml):
        if self.pending is not None:
            return
        self.clear()
        form = self.form()
        self.pending = queue.Queue()
        results = self.pending
        for button in self.buttons:
            button.state(['disabled'])
        self.status.set('계산·예산 조정 중입니다. 입력 변경 시 결과를 폐기합니다.')
        def work():
            try:
                results.put((calculate(form, use_ml=use_ml, use_llm=form['use_llm']), None))
            except ValueError as error:
                results.put((None, str(error)))
            except Exception as error:
                results.put((None, f'실행 오류: {type(error).__name__}'))
        threading.Thread(target=work, daemon=True).start()
        self.root.after(50, lambda: self.poll_ml(form))

    def poll_ml(self, form):
        try:
            result, error = self.pending.get_nowait()
        except queue.Empty:
            self.root.after(50, lambda: self.poll_ml(form))
            return
        self.pending = None
        for button in self.buttons:
            button.state(['!disabled'])
        if self.form() != form:
            self.status.set('실행 중 입력이 변경되어 이전 결과를 폐기했습니다. 다시 실행하세요.')
            return
        if error:
            self.show_error(error)
        else:
            self.show_result(result)

    def show_error(self, error):
        self.clear()
        self.status.set('입력 또는 ML 실행 상태를 확인하세요.')
        self.details.configure(state='normal')
        self.details.insert('1.0', error)
        self.details.configure(state='disabled')

    def run(self):
        if self.pending is not None:
            return
        if self.use_llm.get():
            self.run_async(use_ml=False)
            return
        self.clear()
        try:
            result = calculate(self.form())
        except ValueError as error:
            self.show_error(str(error))
        else:
            self.show_result(result)

    def show_result(self, result):
        self.clear()
        engine = result["engine"]
        report = describe(result)
        display = result.get('display')
        if display and (display['selected']['status'] != 'proposed' or any(c['status'] != 'proposed' for c in display['candidates'])):
            self.show_error('금액 단위와 최소액·하한·조정 한도를 함께 지킬 수 없습니다. 생활비 또는 설정을 확인해 주세요.')
            return
        if engine["status"] != "proposed":
            self.status.set("설정 충돌 — 결과를 생성하지 않았습니다.")
        else:
            self.status.set(("실제 ML · " if result["source"] == "ml" else "직접 입력 · " if result["source"] == "manual" else "ML 생략 · ") + "계산·검수 완료. 실제 예산에는 반영하지 않습니다.")
            base = display['candidates'][0]['allocations'] if display else engine["candidates"][0]["result"]["allocations"]
            adjusted = display['candidates'][-1]['allocations'] if display else engine["candidates"][-1]["result"]["allocations"]
            recommended = display['selected']['allocations'] if display else result['selected']['allocations']
            if display:
                report += f"\n최종 예산: 만 원 우선·필요 시 5천 원 / 배분 잔액: {display['selected']['unallocated']:,}원(여윳돈)"
            for code, name in CATEGORIES.items():
                if code not in engine["active_categories"]:
                    name += " (제외)"
                draft_text = f"{result['draft'][code]:,.2f}" if result['source'] == 'ml' else '—' if result['source'] == 'ml_skipped' else f"{result['draft'][code]:,}"
                self.table.insert("", "end", values=(name, draft_text, f"{base[code]:,}",
                                                     f"{adjusted[code]:,}", f"{adjusted[code]-base[code]:+,}",
                                                     f"{recommended[code]:,}"))
            draft_total = '—' if result['source'] == 'ml_skipped' else f"{sum(result['draft'].values())+result['etc']:,.2f}"
            base_total = sum(base.values()) + (display['candidates'][0]['other_reserved'] if display else result['etc'])
            adjusted_total = sum(adjusted.values()) + (display['candidates'][-1]['other_reserved'] if display else result['etc'])
            recommended_total = sum(recommended.values()) + (display['selected']['other_reserved'] if display else result['etc'])
            self.table.insert("", "end", values=("기타 포함 합계", draft_total,
                                                 f"{base_total:,}", f"{adjusted_total:,}",
                                                 f"{adjusted_total-base_total:+,}", f"{recommended_total:,}"))
        self.details.configure(state="normal")
        self.details.insert("1.0", report)
        self.details.configure(state="disabled")


if __name__ == "__main__":
    window = tk.Tk()
    Simulator(window)
    window.mainloop()
