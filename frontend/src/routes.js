// 화면 주소 목록. 데이터 API 는 같은 모양 앞에 /api 를 붙인다 (예: /budgets → /api/budgets/)
export const PATHS = {
  // 하단 탭
  home: '/',
  txs: '/finance/transactions',
  budget: '/budgets',
  fund: '/budgets/cushion',
  assistant: '/chat',
  // 회원 (users)
  login: '/login',
  signup: '/signup',
  onboard: '/start',
  profile: '/profile',
  my: '/my',
  // 계좌 연결 흐름 (finance): 은행 선택 → 기관 인증 → 계좌 선택 → 카드 선택 → 연결 결과 → 대출 확인
  connect: '/finance/connect',
  connectAuth: '/finance/connect/auth',
  connectAccounts: '/finance/connect/accounts',
  connectCards: '/finance/connect/cards',
  connectDone: '/finance/connect/done',
  loanCheck: '/finance/loans',
  connections: '/finance/connections',
  // 첫 예산 설정 흐름 (budgets): 월급 → 고정지출 → 저축 확인 → 방향 → 지킬 돈 → 추천 예산
  salary: '/budgets/salary',
  fixed: '/budgets/fixed',
  savings: '/budgets/savings',
  direction: '/budgets/direction',
  protect: '/budgets/protect',
  recommend: '/budgets/recommend',
}
