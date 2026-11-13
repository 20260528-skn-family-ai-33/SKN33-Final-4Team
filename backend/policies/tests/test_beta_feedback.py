from datetime import date
from unittest import mock

from django.test import TestCase

from policies import agent, tools
from policies.eligibility import Profile
from policies.tests.test_agent import Talk, chose, rows
from policies.tests.test_search import add, near


class BetaFeedbackTest(TestCase):
    def detail(self, policy, *topics):
        return tools.detail(policy.pk, topics, Profile(), date(2026, 11, 12))

    def test_documents_in_application_are_not_reported_missing(self):
        policy = add('DOCS', sbmsnDcmntCn='',
                     plcyAplyMthdCn='회원가입 후 신청 · 개인정보동의 및 통장사본 등록 · 신청기관 선택하기')
        value = dict(self.detail(policy, 'documents')['rows'])['제출 서류']
        self.assertIn('신청 방법에 기재된 내용:', value)
        self.assertIn('통장사본 등록', value)
        self.assertIn('전체 서류 목록과 제출 여부는 운영 기관에 확인', value)
        self.assertNotIn('주민등록등본', value)

    def test_documents_missing_stays_unknown_and_other_source_is_labelled(self):
        policy = add('EMPTY', sbmsnDcmntCn='', plcyAplyMthdCn='홈페이지에서 신청')
        self.assertIsNone(dict(self.detail(policy, 'documents')['rows'])['제출 서류'])
        policy.raw.update(etcMttrCn='해당자는 재학증명서를 제출합니다.')
        policy.save(update_fields=['raw'])
        self.assertIn('기타 안내에 기재된 내용: 해당자는 재학증명서를 제출합니다.',
                      dict(self.detail(policy, 'documents')['rows'])['제출 서류'])

    def test_long_eligibility_income_and_exclusions_are_preserved(self):
        extra = '구직단념청년 요건을 확인합니다. ' * 40 + '마지막 대상: 자립준비청년'
        income = '소득 세부 기준입니다. ' * 20 + '끝 조건'
        policy = add('LONG', addAplyQlfcCndCn=extra, earnCndSeCd='0043002', earnEtcCn=income,
                     ptcpPrpTrgtCn='현재 사업에 참여 중인 사람은 제외')
        value = dict(self.detail(policy, 'eligibility')['rows'])['자격']
        self.assertIn('마지막 대상: 자립준비청년', value)
        self.assertIn('끝 조건', value)
        self.assertIn('현재 사업에 참여 중인 사람은 제외', value)
        self.assertNotIn('…', value)

    def test_guarantee_question_survives_which_policy_selection(self):
        add('P1', vector=near(1), plcyNm='청년 전월세 대출보증', srngMthdCn='접수 후 보증기관 심사')
        add('P2', vector=near(2), plcyNm='청년내일저축계좌')
        plan = mock.Mock(return_value=chose('search'))
        talk = Talk(plan=plan)
        talk.lives_in_dongtan()
        talk.send('월세 지원 있어?')
        self.assertEqual(talk.last['reply']['sections'][0]['policy_no'], 'P1')
        self.assertIn('지원 형태: 대출 보증 (지원금 지급이 아니에요)', rows(talk.last))
        plan.return_value = chose('detail', topics=['eligibility'])  # 베타에서 실제 빠진 의도
        asked = talk.send('추천한 정책 신청하면 무조건 지원 받을 수 있는 거지?')
        self.assertEqual(asked['reply']['lines'], [agent.WHICH])
        result = talk.send('1. 청년 전월세 대출보증')
        self.assertEqual(result['reply']['lines'][0], agent.NOT_GUARANTEED)
        self.assertIn('심사·선정 절차: 접수 후 보증기관 심사', rows(result))
        self.assertEqual(result['reply']['sections'][0]['policy_no'], 'P1')

    def test_missing_selection_is_not_invented_even_without_planner(self):
        add('P1', srngMthdCn='', operInstCdNm='운영기관')
        talk = Talk()
        talk.lives_in_dongtan()
        talk.send('월세 지원 있어?')
        result = talk.send('1번 신청하면 무조건 받을 수 있어?')
        self.assertEqual(result['reply']['lines'][0], agent.NOT_GUARANTEED)
        self.assertIn('심사·선정 절차: 수집한 공고의 해당 항목에서 확인되지 않아요. 운영기관에 확인해 주세요.', rows(result))

    def test_loan_interest_grant_is_not_labelled_as_loan(self):
        policy = add('INTEREST', plcyNm='청년 전세대출 이자 지원')
        self.assertIsNone(tools.support_type(policy))

    def test_search_for_full_interest_support_is_not_a_guarantee_followup(self):
        add('P1')
        talk = Talk(plan=mock.Mock(return_value=chose('search')))
        talk.lives_in_dongtan()
        result = talk.send('대출 이자 100% 지원하는 정책 찾아줘')
        self.assertNotIn(agent.NOT_GUARANTEED, result['reply']['lines'])
        self.assertEqual(result['reply']['sections'][0]['policy_no'], 'P1')

    def test_exact_longer_policy_name_wins_and_ambiguous_names_keep_the_planner_choice(self):
        """PR 117 리뷰 1: '청년 월세 지원'이 '청년 월세 지원 특별사업'에 포함돼도 지목한 긴 이름의 정책을 고른다"""
        add('P1', vector=near(1), plcyNm='청년 월세 지원', srngMthdCn='일반 심사')
        add('P2', vector=near(2), plcyNm='청년 월세 지원 특별사업', srngMthdCn='특별 심사')
        add('P3', vector=near(3), plcyNm='청년 전세 지원', srngMthdCn='전세 심사')
        plan = mock.Mock(return_value=chose('search'))
        talk = Talk(plan=plan)
        talk.lives_in_dongtan()
        talk.send('월세 지원 있어?')
        plan.return_value = chose('detail', 1, ['eligibility'])  # 해석기가 틀려도 정확한 이름이 앞선다
        result = talk.send('청년 월세 지원 특별사업 신청하면 무조건 받을 수 있어?')
        self.assertEqual(result['reply']['sections'][0]['policy_no'], 'P2')
        self.assertIn('심사·선정 절차: 특별 심사', rows(result))
        plan.return_value = chose('detail', 3, ['eligibility'])  # 두 정책 이름이 다 나오면 모호 → 해석기 선택
        result = talk.send('청년 월세 지원 특별사업이랑 청년 전세 지원 중 무조건 받는 거 있어?')
        self.assertEqual(result['reply']['sections'][0]['policy_no'], 'P3')

    def test_new_search_with_guarantee_words_stays_a_search(self):
        """PR 117 재리뷰: '무조건'이 있어도 새 검색이면 검색한다. 확답 안내는 보여 준 정책의 후속 질문에만"""
        add('RENT', vector=near(1), plcyNm='청년 월세 지원')
        add('LIVING', vector=near(2), plcyNm='청년 생활비 지원')
        for plan in (mock.Mock(return_value=chose('search')), False):  # 해석기가 검색을 골랐을 때 · 해석기 없이 규칙만
            with self.subTest(plan=bool(plan)):
                talk = Talk(plan=plan)
                talk.lives_in_dongtan()
                first = talk.send('신청하면 무조건 받을 수 있는 월세 지원 정책 있어?')  # 첫 질문 — 되묻지 않고 검색
                self.assertNotEqual(first['reply']['lines'], [agent.WHICH])
                self.assertNotIn(agent.NOT_GUARANTEED, first['reply']['lines'])
                self.assertTrue(first['reply']['sections'])
                again = talk.send('그럼 무조건 받을 수 있는 생활비 지원 정책도 있어?')  # 추천 뒤 새 검색 — 이전 정책 자격이 아니다
                self.assertNotIn(agent.NOT_GUARANTEED, again['reply']['lines'])
                self.assertNotEqual(again['reply']['lines'][0], agent.DETAIL_HEAD)
                self.assertTrue(again['reply']['sections'])
