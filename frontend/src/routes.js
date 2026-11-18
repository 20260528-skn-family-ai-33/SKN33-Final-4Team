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
  myInfo: '/my/info',
  myPolicies: '/my/policies',
  // 계좌 연결 흐름 (finance, 11/18 화면 분리): 은행 선택(계좌 동의) → 은행 인증 → 계좌 선택
  //   → 카드 연결(선택: 카드사 선택 · 카드 동의 → 카드사 인증 → 카드 선택) → 대출 정보 연결(선택: 대출 동의 → 불러오기 → 대출 확인) → 연결 결과
  connect: '/finance/connect',
  connectAuth: '/finance/connect/auth',
  connectAccounts: '/finance/connect/accounts',
  connectCard: '/finance/connect/card',
  connectCards: '/finance/connect/cards',
  connectLoan: '/finance/connect/loan',
  connectDone: '/finance/connect/done',
  loanCheck: '/finance/loans',
  connections: '/finance/connections',
  cardReviews: '/finance/card-reviews',
  // 첫 예산 설정 흐름 (budgets): 월급 → 고정지출 → 저축 확인 → 지킬 돈 → 추천 예산 (대출 확인은 11/18 계좌 연결 단계로 옮김)
  //   예산 산정 방향(생활 안정형/저축 집중형)은 없애고 내 정보의 자취 여부 비율로 나눈다 (2026-10-27)
  salary: '/budgets/salary',
  fixed: '/budgets/fixed',
  savings: '/budgets/savings',
  protect: '/budgets/protect',
  recommend: '/budgets/recommend',
}

// 계좌 선택 저장 뒤 갈 화면 (location.state)
//   from: 'manage' = 연결 관리에서 들어옴 → 연결 관리로 돌아간다 (카드 선택 · 카드 동의를 거치지 않음)
//   가입 과정 = 카드 연결 화면 (11/18: 카드는 그 화면에서 연결할지 고른다)
export const afterAccountPick = (state) => (state?.from === 'manage' ? PATHS.connections : PATHS.connectCard)
