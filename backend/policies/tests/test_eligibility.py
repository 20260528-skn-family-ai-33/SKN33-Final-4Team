from datetime import date

from django.test import SimpleTestCase

from policies.eligibility import CHECK, MET, UNMET, Profile, age_on, judge, keep
from policies.models import Policy
from policies.normalize import policy_fields
from policies.search import Hit
from policies.tests.test_normalize import row

TODAY = date(2026, 11, 10)
BORN_27 = date(1999, 3, 1)  # 2026-11-10 에 만 27세
PERSON = Profile(birth_date=BORN_27, gender="male", job="0013003", specials=frozenset())  # 미취업 · 특화 대상 없음


def policy(**changes):
    """저장하지 않는 정책 (DB 없이 판단만 본다). 기본은 모든 조건 제한없음 · 만 19~34세"""
    return Policy(**policy_fields(row(**changes)))


def status(item, profile=PERSON, **changes):
    verdict = judge(policy(**changes), profile, TODAY)
    return next(c.status for c in verdict.conditions if c.item == item)


class AgeTest(SimpleTestCase):
    def test_만_나이(self):
        self.assertEqual(age_on(date(1999, 11, 10), TODAY), 27)
        self.assertEqual(age_on(date(1999, 11, 11), TODAY), 26)

    def test_범위_안팎(self):
        self.assertEqual(status("나이"), MET)
        self.assertEqual(status("나이", sprtTrgtMaxAge="26"), UNMET)
        self.assertEqual(status("나이", sprtTrgtMinAge="28", sprtTrgtMaxAge="39"), UNMET)
        self.assertEqual(status("나이", sprtTrgtMinAge="0", sprtTrgtMaxAge="39"), MET)  # 아래만 비었다

    def test_나이_정보가_없거나_생년월일을_모르면_확인_필요(self):
        self.assertEqual(status("나이", sprtTrgtMinAge="0", sprtTrgtMaxAge="0"), CHECK)
        self.assertEqual(status("나이", Profile()), CHECK)

    def test_상한이_아이_나이면_떨어뜨리지_않는다(self):
        verdict = judge(policy(sprtTrgtMinAge="0", sprtTrgtMaxAge="1"), PERSON, TODAY)  # 부모급여: 0~23개월 아동
        age = verdict.conditions[0]
        self.assertEqual((verdict.status, age.status, age.detail), (CHECK, CHECK, "자녀 나이 조건으로 보임 (만 ~1세)"))

    def test_상한에_곧_닿으면_표시(self):
        soon = Profile(birth_date=date(1992, 12, 1))  # 만 33세, 21일 뒤 34세 — 상한 34세를 아직 넘지는 않는다
        self.assertFalse(judge(policy(), soon, TODAY).soon_over)
        at_top = Profile(birth_date=date(1991, 12, 1))  # 만 34세, 21일 뒤 35세
        verdict = judge(policy(), at_top, TODAY)
        self.assertEqual((verdict.status, verdict.soon_over), (MET, True))
        far = Profile(birth_date=date(1992, 6, 1))  # 만 34세, 다음 생일은 7개월 뒤
        self.assertFalse(judge(policy(), far, TODAY).soon_over)


class CodeTest(SimpleTestCase):
    def test_규칙_다섯_줄(self):
        self.assertEqual(status("취업 상태", jobCd="0013010,0013001"), MET)  # 1. 제한없음이 있으면 충족
        self.assertEqual(status("취업 상태", jobCd=""), CHECK)  # 2. 칸이 비었다
        self.assertEqual(status("취업 상태", Profile(birth_date=BORN_27), jobCd="0013001"), CHECK)  # 2. 사용자 값 모름
        self.assertEqual(status("취업 상태", jobCd="0013001,0013003"), MET)  # 3. 사용자 코드가 있다
        self.assertEqual(status("취업 상태", jobCd="0013001,0013009"), CHECK)  # 4. 기타
        self.assertEqual(status("취업 상태", jobCd="0013001"), UNMET)  # 5. 재직자 전용

    def test_묻지_않는_학력_전공_혼인은_제한없음이_아니면_확인_필요(self):
        self.assertEqual(status("학력", schoolCd="0049005"), CHECK)
        self.assertEqual(status("전공", plcyMajorCd="0011005"), CHECK)
        self.assertEqual(status("혼인", mrgSttsCd="0055001"), CHECK)
        self.assertEqual(status("혼인"), MET)

    def test_소득은_탈락시키지_않는다(self):
        self.assertEqual(status("소득"), MET)
        verdict = judge(policy(earnCndSeCd="0043002", earnEtcCn="연소득 5천만 원 이하"), PERSON, TODAY)
        income = next(c for c in verdict.conditions if c.item == "소득")
        self.assertEqual((income.status, income.detail), (CHECK, "연소득 5천만 원 이하"))


class SpecialTest(SimpleTestCase):
    def test_특화_대상(self):
        self.assertEqual(status("특화 대상", sbizCd="0014007"), UNMET)  # 군인 전용, 사용자는 해당 없음
        self.assertEqual(status("특화 대상", Profile(specials=frozenset({"0014007"})), sbizCd="0014007"), MET)
        self.assertEqual(status("특화 대상", Profile(), sbizCd="0014007"), CHECK)  # 건너뛰기 = 모름
        self.assertEqual(status("특화 대상", sbizCd="0014007,0014009"), CHECK)  # 기타

    def test_여성은_성별로_판단한다(self):
        self.assertEqual(status("특화 대상", Profile(gender="female"), sbizCd="0014002"), MET)
        self.assertEqual(status("특화 대상", Profile(gender="male"), sbizCd="0014002"), UNMET)  # 특화 대상을 건너뛰었어도
        self.assertEqual(status("특화 대상", Profile(gender="female"), sbizCd="0014002,0014005"), MET)
        self.assertEqual(status("특화 대상", Profile(gender="male"), sbizCd="0014002,0014005"), CHECK)  # 장애인인지 모름
        self.assertEqual(status("특화 대상", Profile(), sbizCd="0014002"), CHECK)


class VerdictTest(SimpleTestCase):
    def test_가장_나쁜_조건을_따른다(self):
        self.assertEqual(judge(policy(), PERSON, TODAY).status, MET)
        checked = judge(policy(schoolCd="0049005"), PERSON, TODAY)
        self.assertEqual((checked.status, [c.item for c in checked.to_check]), (CHECK, ["학력"]))
        dropped = judge(policy(schoolCd="0049005", jobCd="0013001"), PERSON, TODAY)
        self.assertEqual((dropped.status, [c.item for c in dropped.unmet]), (UNMET, ["취업 상태"]))

    def test_불충족만_빼고_순서는_그대로(self):
        def hit(number, **changes):
            return Hit(policy(plcyNo=number, **changes), 0, None, [], "open", False, False)
        hits = [hit("A"), hit("B", jobCd="0013001"), hit("C", schoolCd="0049005")]
        kept, dropped = keep(hits, PERSON, TODAY)
        self.assertEqual([h.policy.pk for h, _ in kept], ["A", "C"])
        self.assertEqual([(h.policy.pk, v.status) for h, v in dropped], [("B", UNMET)])
