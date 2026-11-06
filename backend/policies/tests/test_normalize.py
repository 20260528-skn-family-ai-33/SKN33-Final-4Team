from datetime import date

from django.test import SimpleTestCase

from policies import codes, normalize, regions


def row(**changes):
    """가짜 정책 한 건 (실제 데이터 아님)"""
    base = {
        "plcyNo": "T0001", "plcyNm": "테스트 청년 지원", "plcyKywdNm": "보조금", "plcyExplnCn": "청년 설명", "plcySprtCn": "월 10만 원 지원",
        "lclsfNm": "일자리", "mclsfNm": "취업", "aplyPrdSeCd": "0057001", "aplyYmd": "20261101 ~ 20261130",
        "sprtTrgtMinAge": "19", "sprtTrgtMaxAge": "34", "sprtTrgtAgeLmtYn": "Y", "addAplyQlfcCndCn": "",
        "zipCd": "41590,44130", "jobCd": "0013010", "schoolCd": "0049010", "plcyMajorCd": "0011009", "sbizCd": "0014010",
        "mrgSttsCd": "0055003", "earnCndSeCd": "0043001", "earnEtcCn": "", "aplyUrlAddr": "", "refUrlAddr1": "", "refUrlAddr2": "",
        "sprvsnInstPicNm": "가상의 담당자", "operInstPicNm": "가상의 담당자", "inqCnt": "1",
    }
    return {**base, **changes}


class PeriodTest(SimpleTestCase):
    def test_여러_기간은_문자_백슬래시N으로_이어진다(self):
        fields = normalize.policy_fields(row(aplyYmd="20261001 ~ 20261031\\N20261201 ~ 20261231"))
        self.assertEqual(len(fields["periods"]), 2)
        self.assertEqual(fields["last_apply_end"], date(2026, 12, 31))
        self.assertEqual(fields["apply_type"], "specific")

    def test_끝이_시작보다_앞서면_모름(self):
        fields = normalize.policy_fields(row(aplyYmd="20260601 ~ 20250710"))
        self.assertEqual((fields["apply_type"], fields["periods"], fields["last_apply_end"]), ("unknown", [], None))

    def test_없는_날짜와_빈_기간은_모름(self):
        self.assertEqual(normalize.policy_fields(row(aplyYmd="20260231 ~ 20260301"))["apply_type"], "unknown")
        self.assertEqual(normalize.policy_fields(row(aplyYmd=""))["apply_type"], "unknown")

    def test_상시와_마감(self):
        self.assertEqual(normalize.policy_fields(row(aplyPrdSeCd="0057002", aplyYmd=""))["apply_type"], "always")
        self.assertEqual(normalize.policy_fields(row(aplyPrdSeCd="0057003", aplyYmd=""))["apply_type"], "closed")


class DomainTest(SimpleTestCase):
    def test_코드표에_없는_이름을_대분류로_통일한다(self):
        self.assertEqual(normalize.domains(row(lclsfNm="금융･복지･문화")), ["복지문화"])
        self.assertEqual(normalize.domains(row(lclsfNm="교육･직업훈련")), ["교육"])
        self.assertEqual(normalize.domains(row(lclsfNm="참여･기반")), ["참여권리"])

    def test_쉼표_분류는_중복_없이(self):
        self.assertEqual(normalize.domains(row(lclsfNm="일자리,교육,교육･직업훈련")), ["일자리", "교육"])

    def test_자산_형성은_정책명_키워드로_붙인다(self):
        self.assertEqual(normalize.domains(row(lclsfNm="복지문화", plcyNm="청년 희망 통장")), ["복지문화", codes.ASSET])
        self.assertEqual(normalize.domains(row(lclsfNm="")), [])


class AgeTest(SimpleTestCase):
    def test_플래그는_무시하고_숫자만_본다(self):
        self.assertEqual(normalize.age_range(row(sprtTrgtAgeLmtYn="N")), (19, 34))
        self.assertEqual(normalize.age_range(row(sprtTrgtMinAge="0", sprtTrgtMaxAge="0")), (None, None))

    def test_원문과_어긋나면_모름(self):
        self.assertEqual(normalize.age_range(row(addAplyQlfcCndCn="대상: 만 18~39세")), (None, None))
        self.assertEqual(normalize.age_range(row(addAplyQlfcCndCn="대상: 만 19~34세")), (19, 34))


class FieldsTest(SimpleTestCase):
    def test_담당자_이름은_저장하지_않는다(self):
        raw = normalize.policy_fields(row())["raw"]
        self.assertNotIn("sprvsnInstPicNm", raw)
        self.assertNotIn("operInstPicNm", raw)

    def test_조회수만_바뀌면_해시가_같고_기간이_바뀌면_다르다(self):
        same = normalize.policy_fields(row(inqCnt="99"))["content_hash"]
        self.assertEqual(same, normalize.policy_fields(row())["content_hash"])
        self.assertNotEqual(same, normalize.policy_fields(row(aplyYmd="20261201 ~ 20261231"))["content_hash"])

    def test_http가_아닌_링크는_버린다(self):
        fields = normalize.policy_fields(row(aplyUrlAddr="www.example.com", refUrlAddr1="https://example.com", refUrlAddr2="-"))
        self.assertEqual((fields["apply_url"], fields["ref_urls"]), ("", ["https://example.com"]))

    def test_빠진_키와_빈_값(self):
        data = row()
        del data["schoolCd"]
        fields = normalize.policy_fields({**data, "mrgSttsCd": ""})
        self.assertEqual((fields["school_codes"], fields["marriage_code"]), ([], ""))

    def test_소득_설명의_대시는_빈_값(self):
        self.assertEqual(normalize.policy_fields(row(earnEtcCn="-"))["income_note"], "")

    def test_임베딩_문장에_자격_조건을_넣지_않고_같은_설명은_한_번만(self):
        text = normalize.embed_text(row(plcySprtCn="청년 설명", addAplyQlfcCndCn="만 19~34세 거주자"))
        self.assertEqual(text.count("청년 설명"), 1)
        self.assertNotIn("거주자", text)
        self.assertIn("거주자", normalize.policy_fields(row(addAplyQlfcCndCn="만 19~34세 거주자"))["search_text"])

    def test_태그는_지우고_부등호는_남긴다(self):
        self.assertEqual(normalize.embed_text(row(plcySprtCn="<p>지원</p> 소득 < 100%&nbsp;")).split("\n")[-1], "지원 내용: 지원 소득 < 100%")


class RegionTest(SimpleTestCase):
    def test_최종_선택_지역은_256곳(self):
        self.assertEqual(sum(r["final"] for r in regions.REGIONS.values()), 256)
        self.assertTrue(all(code in r["match"] for code, r in regions.REGIONS.items()))

    def test_구는_상위_시와_시도까지_비교한다(self):
        self.assertEqual(regions.REGIONS["41597"]["match"], ["41597", "41590", "41000"])
        self.assertFalse(regions.REGIONS["41590"]["final"])

    def test_세종은_시도이면서_최종_선택(self):
        self.assertTrue(regions.REGIONS["36110"]["final"])

    def test_폐지된_인천_구는_새_구와_이어진다(self):
        self.assertIn("28140", regions.REGIONS["28125"]["match"])
        self.assertIn("28260", regions.REGIONS["28290"]["match"])
        self.assertIn("28110", regions.KNOWN_CODES)
