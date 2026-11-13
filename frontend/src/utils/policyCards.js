// 추천 순번은 대화 내부에서 유지하고, 화면 제목에는 정책명만 보여 준다.
export const policyTitle = (title) => title.replace(/^\d+\.\s+/, '')
// 그 순번은 서버가 "N번"으로 알아듣는 번호다 → 카드에 따로 보여 줘 '더 보기'(4·5·6번)에서도 말한 번호와 맞게 한다
export const policyNumber = (title) => title.match(/^(\d+)\.\s/)?.[1]

// 카드 안 [자세히 보기] 버튼의 선택지 값 앞부분 (서버 agent.DETAIL 과 같다). 칩 목록에서는 뺀다
export const DETAIL = 'detail:'

export function policyChoices(content, choices = content.choices) {
  // ID가 없는 이전 메시지는 기존 선택지를 유지한다.
  if (content.agent !== 'policy' || !content.sections?.length || !content.sections.every((s) => s.policyNo)) return choices
  return (choices ?? []).filter((c) => !/^\d+번 저장$/.test(c.label) && !c.value.startsWith(DETAIL))
}

// 지역 드롭다운 [시·도 | 시·군·구 | 구]. 목록 이름은 시도를 뺀 나머지 ('수원시 장안구', '관악구'). 세종처럼 시도 자신뿐이면 비운다
export function cities(top) {
  return [...new Set((top?.regions ?? []).filter((r) => r.name !== top.name).map((r) => r.name.split(' ')[0]))]
}

export function districts(top, city) {
  return (top?.regions ?? []).map((r) => r.name.split(' ')).filter(([c, gu]) => c === city && gu).map(([, gu]) => gu)
}

// 마감까지 남은 날 (마감일 'YYYY-MM-DD', 오늘 = 0). 저장한 정책 화면의 D-day 배지
export function daysLeft(end, today = new Date()) {
  const [y, m, d] = end.split('-').map(Number)
  return Math.round((new Date(y, m - 1, d) - new Date(today.getFullYear(), today.getMonth(), today.getDate())) / 86_400_000)
}
