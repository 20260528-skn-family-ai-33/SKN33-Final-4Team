"""소비 분석 검증용 가상 사회초년생 3명 적재 도구 (data/design.json → 현재 모델)

design.json 은 Django fixture 가 아니라 설계 명세다. loaddata 하지 않고 여기서 모델 행으로 바꾼다.
- 기본은 dry-run: DB 연결 · 쓰기 없이 생성 예정 건수 · 합계 · 검사 결과만 출력
- --apply: 이 fixture 사용자만 지우고 다시 넣는다 (한 트랜잭션). ID · 시각이 모두 결정적이라 반복해도 같다
- --reset: 이 fixture 사용자만 지운다. 기존 DB flush/drop 은 하지 않는다
- 로컬 money_cushion_router_fixture(또는 그 자동 테스트 DB)가 아니면 쓰기를 거부한다

approved 예산은 테스트 스냅샷이다. 실제 사용자 승인 · 엔진 검수 · ML 예측이 아니다.

사용 (backend/ 에서):
  python -m spending_fixture.seed [--create-db | --apply | --reset | --status]
"""
import argparse
import hashlib
import json
import os
import sys
import uuid
from collections import Counter, defaultdict
from datetime import date, datetime, time
from importlib import import_module
from pathlib import Path
from zoneinfo import ZoneInfo

DATA = Path(__file__).with_name("data")
# 첨부 08_전달파일해시.json 의 04 · 05 해시. 사본이 바뀌면 멈춘다
SHA256 = {
    "design.json": "7d8cb4f6644696d237609c533a807b9267bc94a732ef5724c615681e8eba6c26",
    "expected.json": "66216db3f340fc8e6ae0c7210ad9d1778b8dfa9007103bf78c6500fead9034c2",
}
KST = ZoneInfo("Asia/Seoul")
AS_OF = datetime(2026, 10, 29, 23, 59, 59, tzinfo=KST)
NS = uuid.UUID("5e1f7c2a-2026-4029-a000-000000f1c7e5")  # fixture 전용 UUID5 이름공간
ENGINE = "fixture-snapshot-v1"  # 실제 엔진 버전이 아님을 드러낸다
NON_SPENDING_DESC = {"income": "가상급여입금", "saving": "가상저축이체", "transfer": "가상송금",
                     "own_transfer": "가상내계좌이체", "card_payment": "가상카드대금"}


def load(name):
    raw = (DATA / name).read_bytes()
    if hashlib.sha256(raw).hexdigest() != SHA256[name]:
        raise SystemExit(f"{name} 해시가 첨부 자료와 다르다")
    return json.loads(raw)


def uid(*parts):
    return uuid.uuid5(NS, ":".join(map(str, parts)))


def at(day, hour):
    """그 날짜의 고정 시각 (KST). 실행 날짜와 무관"""
    day = date.fromisoformat(day) if isinstance(day, str) else day
    return datetime.combine(day, time(hour), KST)


def user_ids(design):
    return {p: uid(p, "user") for p in design["personas"]}


def plan(design):
    """→ [(모델 이름, 칸 dict, 고정 시각 dict)] 삽입 순서대로. DB 를 읽지 않는다"""
    rows = []

    def add(model, fields, **stamps):
        rows.append((model, fields, stamps))

    users = user_ids(design)
    tx_rows = design["transaction_design_rows"]
    by_persona = defaultdict(list)
    for r in tx_rows:
        by_persona[r["persona"]].append(r)

    for p, persona in design["personas"].items():
        u = users[p]
        asset = design["assets"][p]
        joined = datetime.fromisoformat(asset["card"]["first_connected_at"])
        runs = [c for c in design["coverage_plan"] if c["persona"] == p]
        fetched = {kind: max((at(c["covered_to"], 23) for c in runs if c["asset"] == f"{p}-{kind}" and c["status"] == "success"),
                             default=None) for kind in ("bank", "card")}

        add("User", dict(user_id=u, email=persona["synthetic_email"], nickname=f"fixture-{p}",
                         password="!fixture-unusable", is_staff=False), created_at=joined, updated_at=joined)
        add("FinancialProfile", dict(
            user_id=u, birth_date=date.fromisoformat(persona["birth_date"]), gender=persona["gender"],
            housing_type=persona["housing_type"], has_car=persona["has_car"],
            monthly_take_home_income=persona["income"], payday=persona["payday"], living_budget=persona["living"],
            savings_floor=persona["savings"], cushion_monthly=persona["cushion"],
            monthly_disposable_income=None, disposable_income_basis=None), updated_at=joined)
        for rank, code in enumerate(persona["priorities"], start=1):
            add("CategoryPriority", dict(category_priority_id=uid(p, "priority", rank), user_id=u, category_id=code, rank=rank))
        for code, amount in persona["minimums"].items():
            add("CategoryMinimum", dict(category_minimum_id=uid(p, "minimum", code), user_id=u, category_id=code, min_amount=amount))

        conn = {kind: uid(p, kind, "connection") for kind in ("bank", "card")}
        bank, card = uid(p, "bank"), uid(p, "card")
        for kind in ("bank", "card"):
            add("Connection", dict(connection_id=conn[kind], user_id=u, business_type=kind,
                                   organization_code=asset[kind]["organization_code"], alias=asset[kind]["connection_alias"],
                                   status=asset[kind]["status"]), created_at=joined)
        add("BankAccount", dict(account_id=bank, user_id=u, connection_id=conn["bank"], source_account_key=f"fixture:{p}-bank",
                                masked_number=f"FIXTURE-{p}-****", name="가상 입출금 통장", alias=f"{p}-bank",
                                account_type=asset["bank"]["account_type"], currency=asset["bank"]["currency"],
                                balance=None, withdrawable_amount=None, is_selected=True, fetched_at=fetched["bank"]))
        add("Card", dict(card_id=card, user_id=u, connection_id=conn["card"], payment_account_id=bank,
                         issuer_code=asset["card"]["organization_code"], name=f"가상 {asset['card']['card_type']}카드",
                         masked_number=f"FIXTURE-{p}-****", source_card_key=f"fixture:{p}-card",
                         card_type=asset["card"]["card_type"], origin=asset["card"]["origin"], is_selected=True,
                         first_connected_at=joined, fetched_at=fetched["card"]))

        # 예산 주기 · 버전. 급여 입금 행이 주기 안에 있을 때만 confirmed_income
        salary_days = [date.fromisoformat(r["occurred_on"]) for r in by_persona[p] if r["event"] == "salary"]
        fixed_rows = [r for r in by_persona[p] if r["funding"] == "fixed"]
        recurring = {}
        for r in fixed_rows:
            recurring.setdefault(r["merchant_name"], r)
        fixed_lines = Counter()
        for r in recurring.values():
            fixed_lines[r["category"]] += r["amount_won"]
        cycles = []
        for c in design["budget_cycles"][p]:
            start, end = date.fromisoformat(c["start"]), date.fromisoformat(c["end_exclusive"])
            cycle = uid(p, "cycle", start)
            cycles.append((start, end, cycle))
            add("BudgetCycle", dict(cycle_id=cycle, user_id=u, starts_on=start, ends_before=end, expected_payday=end,
                                    confirmed_income=persona["income"] if any(start <= d < end for d in salary_days) else None,
                                    approved_carryover=0, status=c["status"]))
            versions = [(1, c["budget_status"], persona["category_budget_won"])]
            if p == "A" and c["status"] == "active":
                changes = design["budget_versions"]["A_current"]["validated_v2_changes"]
                versions.append((2, "validated", {**persona["category_budget_won"], **changes}))
            for no, status, living in versions:
                budget = uid(p, "budget", start, no)
                stamp = at(start, 9) if no == 1 else at("2026-10-28", 9)
                add("Budget", dict(
                    budget_id=budget, user_id=u, cycle_id=cycle, version_no=no, status=status,
                    base_budget_id=uid(p, "budget", start, 1) if no > 1 else None, task_id=None,
                    reason="테스트 스냅샷 (실제 승인 · 엔진 검수 아님)", funding_amount=persona["income"],
                    engine_version=ENGINE, input_hash=None, input_snapshot=None, validation_summary=None,
                    validated_at=stamp if status == "validated" else None,
                    approved_at=stamp if status == "approved" else None), created_at=stamp)
                lines = [("living", code, amount) for code, amount in living.items()]
                lines += [("fixed", code, amount) for code, amount in fixed_lines.items()]
                lines += [("savings", None, persona["savings"]), ("cushion", None, persona["cushion"])]
                for bucket, code, amount in lines:
                    add("BudgetItem", dict(item_id=uid(budget, bucket, code), user_id=u, budget_id=budget,
                                           bucket=bucket, category_id=code, allocated_amount=amount))
            if c["status"] == "active":
                for code, amount in persona["locks"].items():
                    add("BudgetCategorySetting", dict(setting_id=uid(p, "setting", start, code), user_id=u, cycle_id=cycle,
                                                      category_id=code, locked=True, locked_amount=amount),
                        updated_at=at(start, 9))

        # 고정지출 → 납부 회차. 거래로 실제 납부가 있는 회차만
        for name, r in recurring.items():
            add("RecurringExpense", dict(recurring_id=uid(p, "recurring", name), user_id=u, payment_account_id=bank,
                                         payment_card_id=None, name=name, amount=r["amount_won"],
                                         payment_day=date.fromisoformat(r["occurred_on"]).day, category_id=r["category"],
                                         status="confirmed", source_type="manual", confirmed_at=joined), created_at=joined)
        for r in fixed_rows:
            day = date.fromisoformat(r["occurred_on"])
            start = next(s for s, e, _ in cycles if s <= day < e)
            recurring_id = uid(p, "recurring", r["merchant_name"])
            net = r["amount_won"] - r["canceled_amount_won"]
            add("PaymentDue", dict(
                due_id=uid("due", r["plan_id"]), user_id=u, obligation_type="recurring", recurring_id=recurring_id,
                payment_account_id=bank, occurrence_key=f"recurring:{recurring_id}:{start.isoformat()}",  # budgets.due_periods 형식
                due_date=day, confirmed_amount=r["amount_won"], **design["payment_due_defaults"],
                paid_amount=net, matched_at=at(day, 23)), created_at=at(day, 9))

        for r in by_persona[p]:
            canceled = r["canceled_amount_won"]
            add("FinancialTransaction", dict(
                transaction_id=uid("tx", r["plan_id"]), user_id=u, source_kind=r["source_kind"],
                account_id=bank if r["source_kind"] == "bank" else None, card_id=card if r["source_kind"] == "card" else None,
                source_key=f"fixture:{r['plan_id']}", request_key=None,
                occurred_on=date.fromisoformat(r["occurred_on"]), occurred_time=time.fromisoformat(r["occurred_time"]),
                amount=r["amount_won"], direction="in" if r["transaction_type"] == "income" else "out",
                source_description=r["merchant_name"] or NON_SPENDING_DESC[r["transaction_type"]], source_detail=None,
                merchant_name=r["merchant_name"], is_canceled=canceled > 0 and canceled == r["amount_won"],
                canceled_amount=canceled or None, balance_after=None, transaction_type=r["transaction_type"],
                suggested_category_id=None, suggestion_confidence=None, confirmed_category_id=r["category"],
                classification_source=r["classification_source"], confirmation_state=r["confirmation_state"]),
                created_at=at(r["occurred_on"], 23), updated_at=at(r["occurred_on"], 23))
        for r in fixed_rows:
            add("PaymentMatch", dict(match_id=uid("match", r["plan_id"]), user_id=u, due_id=uid("due", r["plan_id"]),
                                     transaction_id=uid("tx", r["plan_id"]),
                                     matched_amount=r["amount_won"] - r["canceled_amount_won"],
                                     matched_at=at(r["occurred_on"], 23)), created_at=at(r["occurred_on"], 23))
        for r in by_persona[p]:
            if r["same_purchase_with"]:  # 같은 구매의 계좌 출금 → 목록 · 집계에서 빠진다
                add("TransactionLink", dict(link_id=uid("link", r["plan_id"]), user_id=u,
                                            from_transaction_id=uid("tx", r["same_purchase_with"]),
                                            to_transaction_id=uid("tx", r["plan_id"]), relation_type="SAME_PURCHASE",
                                            matched_amount=r["amount_won"], confirmation_state="confirmed",
                                            confirmed_at=at(r["occurred_on"], 23)))
        for c in runs:
            kind = "bank" if c["asset"].endswith("bank") else "card"
            done = at(c["requested_to"], 23)
            ok = c["status"] == "success"
            add("SyncRun", dict(
                sync_id=uid(p, "sync", c["asset"], c["requested_from"]), user_id=u, connection_id=conn[kind],
                account_id=None, card_id=card if kind == "card" else None, product_code=c["product_code"],
                requested_from=date.fromisoformat(c["requested_from"]), requested_to=date.fromisoformat(c["requested_to"]),
                covered_from=c["covered_from"] and date.fromisoformat(c["covered_from"]),
                covered_to=c["covered_to"] and date.fromisoformat(c["covered_to"]),
                status=c["status"], error_code=None if ok else "sync_error", started_at=done, completed_at=done))
    return rows


def check(design, rows):
    """설계 · 계획 단계 검사 → [(이름, 통과 여부)]. DB 없이 돈다"""
    categories = {code for code, *_ in import_module("budgets.migrations.0003_seed_categories").CATEGORIES}
    tx = design["transaction_design_rows"]
    by_pk, refs = {}, []
    for model, fields, _ in rows:
        pk = next(iter(fields.values()))  # 각 dict 의 첫 칸이 PK
        by_pk[pk] = fields.get("user_id", pk if model == "User" else None)
        refs.append(fields)
    cross = [f for f in refs for k, v in f.items()
             if k.endswith("_id") and k != "user_id" and v in by_pk and by_pk[v] not in (None, f.get("user_id"))]
    counts = Counter(model for model, _, _ in rows)
    expected = design["expected_baseline_counts"]
    c_first = min(r["occurred_on"] for r in tx if r["persona"] == "C")
    return [
        ("category 시드 12개 = 설계 category_names", categories == set(design["category_names"])),
        ("거래 설계 행 414 (A 189 · B 205 · C 20)",
         Counter(r["persona"] for r in tx) == Counter(design["row_counts_by_persona"]) and len(tx) == 414),
        ("plan_id 중복 없음", len({r["plan_id"] for r in tx}) == len(tx)),
        ("모든 카테고리가 시드에 있음", all(r["category"] in categories for r in tx)),
        ("금액 정수 · 취소액 범위", all(isinstance(r["amount_won"], int) and 0 <= r["canceled_amount_won"] <= r["amount_won"] for r in tx)),
        ("기준일 이후 거래 없음", all(r["occurred_on"] <= AS_OF.date().isoformat() for r in tx)),
        ("C 는 10/20 이전 거래 · 성공 조회 없음", c_first >= "2026-10-20" and all(
            c["covered_from"] is None or c["covered_from"] >= "2026-10-20" for c in design["coverage_plan"] if c["persona"] == "C")),
        ("실패 조회는 covered 범위 없음", all(c["status"] == "success" or c["covered_from"] is c["covered_to"] is None
                                       for c in design["coverage_plan"])),
        ("미분류 예산 줄 없음", not any(m == "BudgetItem" and f["category_id"] == "UNCLASSIFIED" for m, f, _ in rows)),
        ("사용자 간 FK 섞임 없음", not cross),
        ("모델별 건수 = 설계 기본 건수",
         all(counts.get(_MODEL_OF[t], 0) == n for t, n in expected.items() if t in _MODEL_OF)),
        ("모든 고정 시각 ≤ 기준 시각", all(v is None or v <= AS_OF for _, f, s in rows for v in [*s.values(), *(
            x for x in f.values() if isinstance(x, datetime))])),
    ]


# 설계 기본 건수의 테이블 이름 → 모델 이름 (category 는 마이그레이션 시드라 따로 센다)
_MODEL_OF = {
    "app_user": "User", "financial_profile": "FinancialProfile", "category_priority": "CategoryPriority",
    "category_minimum": "CategoryMinimum", "connection": "Connection", "bank_account": "BankAccount", "card": "Card",
    "budget_cycle": "BudgetCycle", "budget": "Budget", "budget_item": "BudgetItem",
    "budget_category_setting": "BudgetCategorySetting", "recurring_expense": "RecurringExpense",
    "payment_due": "PaymentDue", "financial_transaction": "FinancialTransaction", "payment_match": "PaymentMatch",
    "transaction_link": "TransactionLink", "sync_run": "SyncRun", "spending_allocation": "SpendingAllocation",
}


def models():
    from django.apps import apps
    return {name: apps.get_model(app, name) for app, names in {
        "users": ["User", "FinancialProfile", "CategoryPriority", "CategoryMinimum"],
        "finance": ["Connection", "BankAccount", "Card", "RecurringExpense", "PaymentDue", "FinancialTransaction",
                    "PaymentMatch", "TransactionLink", "SyncRun"],
        "budgets": ["Category", "BudgetCycle", "Budget", "BudgetItem", "BudgetCategorySetting", "SpendingAllocation"],
    }.items() for name in names}


def guard():
    """쓰기 전: 로컬 fixture DB(또는 그 자동 테스트 DB)인지 확인"""
    from django.db import connection
    from spending_fixture.settings import FIXTURE_DB, LOCAL_HOSTS
    s = connection.settings_dict
    if s["NAME"] not in (FIXTURE_DB, f"test_{FIXTURE_DB}") or s["HOST"] not in LOCAL_HOSTS:
        raise SystemExit(f"대상 DB가 로컬 {FIXTURE_DB} 가 아니라서 멈춘다")


def reset(design):
    """이 fixture 가 만든 사용자(결정적 ID + 가상 이메일)만 지운다. 같은 이메일의 다른 회원이 있으면 멈춘다"""
    guard()
    User = models()["User"]
    ids = user_ids(design)
    emails = [p["synthetic_email"] for p in design["personas"].values()]
    if User.objects.filter(email__in=emails).exclude(pk__in=ids.values()).exists():
        raise SystemExit("fixture 이메일을 가진 다른 회원이 있어서 지우지 않는다")
    return User.objects.filter(pk__in=ids.values()).delete()[0]


def apply(design):
    from django.db import transaction
    guard()
    m = models()
    rows = plan(design)
    with transaction.atomic():
        reset(design)
        for name in dict.fromkeys(model for model, _, _ in rows):  # 삽입 순서 유지
            batch = [(f, s) for model, f, s in rows if model == name]
            m[name].objects.bulk_create([m[name](**f) for f, _ in batch])
            # auto_now(_add) 칸은 저장 때 실행 시각이 들어가므로 고정 시각으로 다시 쓴다
            stamped = defaultdict(list)
            for f, s in batch:
                if s:
                    stamped[tuple(sorted(s.items()))].append(next(iter(f.values())))
            for stamps, pks in stamped.items():
                m[name].objects.filter(pk__in=pks).update(**dict(stamps))
    return rows


def variant_b_approved_v2(design):
    """변형 시나리오(기본 적재에 섞지 않음, 테스트 안에서만): B 현재 주기 v1 → superseded, v2 → approved
    v2 = v1 생활비에 설계의 variant_only_approved_v2_changes 적용 (생활비 총액 그대로). 실제 승인 · 저장 엔진 검증 아님"""
    from django.db import transaction
    guard()
    m = models()
    spec = design["budget_versions"]["B_current"]
    v1 = m["Budget"].objects.get(user_id=uid("B", "user"), cycle__status="active", version_no=1)
    stamp = at("2026-10-28", 9)
    with transaction.atomic():
        m["Budget"].objects.filter(pk=v1.pk).update(status=spec["variant_only_v1_status"])
        v2 = m["Budget"].objects.create(
            budget_id=uid("B", "budget", v1.cycle.starts_on, 2), user_id=v1.user_id, cycle_id=v1.cycle_id, version_no=2,
            status="approved", base_budget=v1, reason=v1.reason, funding_amount=v1.funding_amount,
            engine_version=ENGINE, approved_at=stamp)
        m["Budget"].objects.filter(pk=v2.pk).update(created_at=stamp)
        changes = spec["variant_only_approved_v2_changes"]
        m["BudgetItem"].objects.bulk_create([
            m["BudgetItem"](item_id=uid(v2.pk, i.bucket, i.category_id), user_id=i.user_id, budget=v2, bucket=i.bucket,
                            category_id=i.category_id,
                            allocated_amount=changes.get(i.category_id, i.allocated_amount) if i.bucket == "living" else i.allocated_amount)
            for i in v1.items.all()])
    return v2


def db_summary(design):
    """DB 에 실제로 들어간 fixture 행: 모델별 건수 · 사람별 거래 합계"""
    from django.db.models import Sum
    m = models()
    users = list(user_ids(design).values())
    counts = {"category": m["Category"].objects.count()}
    for table, name in _MODEL_OF.items():
        qs = m[name].objects.all()
        counts[table] = (qs.filter(pk__in=users) if name == "User" else qs.filter(user_id__in=users)).count()
    sums = {p: m["FinancialTransaction"].objects.filter(user_id=u).aggregate(amount=Sum("amount"), canceled=Sum("canceled_amount"))
            for p, u in user_ids(design).items()}
    return counts, sums


def main(argv=None):
    os.environ["DJANGO_SETTINGS_MODULE"] = "spending_fixture.settings"  # 서비스 DB 설정으로 돌지 않게 고정
    import django
    django.setup()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    action = parser.add_mutually_exclusive_group()
    for flag in ("--create-db", "--apply", "--reset", "--status"):
        action.add_argument(flag, action="store_true")
    args = parser.parse_args(argv)
    design = load("design.json")

    if args.create_db:
        return create_db()
    if args.reset:
        print("지운 행:", reset(design))
        return 0
    if args.apply:
        apply(design)
    if args.apply or args.status:
        counts, sums = db_summary(design)
        print(json.dumps({"db_counts": counts, "tx_sums": sums}, ensure_ascii=False, indent=1))
        diff = {t: (counts[t], n) for t, n in design["expected_baseline_counts"].items() if counts.get(t) != n}
        print("설계 기본 건수와 다른 표:", diff or "없음")
        return 1 if diff else 0

    rows = plan(design)
    print("dry-run: DB 연결 · 쓰기 없음")
    print("생성 예정 건수:", dict(Counter(model for model, _, _ in rows)))
    tx = design["transaction_design_rows"]
    for p in design["personas"]:
        mine = [r for r in tx if r["persona"] == p]
        by_type = Counter()
        for r in mine:
            by_type[r["transaction_type"]] += r["amount_won"] - r["canceled_amount_won"]
        dup = sum(r["amount_won"] for r in mine if r["excluded_same_purchase"])
        print(f"{p}: 행 {len(mine)} · 유형별 순금액 {dict(by_type)} · 같은 구매로 빠질 금액 {dup}")
    results = check(design, rows)
    for name, ok in results:
        print("통과" if ok else "실패", name)
    return 0 if all(ok for _, ok in results) else 1


def create_db():
    """로컬 컨테이너에 fixture DB 가 없으면 만든다. 접속 정보는 출력하지 않는다"""
    import psycopg
    from psycopg import sql
    from django.db import connection
    from spending_fixture.settings import FIXTURE_DB, LOCAL_HOSTS
    s = connection.settings_dict
    if s["HOST"] not in LOCAL_HOSTS:
        raise SystemExit("로컬 DB가 아니라서 멈춘다")
    with psycopg.connect(dbname="postgres", user=s["USER"], password=s["PASSWORD"], host=s["HOST"], port=s["PORT"],
                         autocommit=True) as conn:
        if conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", [FIXTURE_DB]).fetchone():
            print(f"{FIXTURE_DB} 이미 있음")
        else:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(FIXTURE_DB)))
            print(f"{FIXTURE_DB} 생성 (host={s['HOST']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
