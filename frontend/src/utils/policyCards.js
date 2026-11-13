// 추천 순번은 대화 내부에서 유지하고, 화면 제목에는 정책명만 보여 준다.
export const policyTitle = (title) => title.replace(/^\d+\.\s+/, '')
// 그 순번은 서버가 "N번"으로 알아듣는 번호다 → 카드에 따로 보여 줘 '더 보기'(4·5·6번)에서도 말한 번호와 맞게 한다
export const policyNumber = (title) => title.match(/^(\d+)\.\s/)?.[1]

export function policyChoices(content, choices = content.choices) {
  // ID가 없는 이전 메시지는 기존 선택지를 유지한다.
  if (content.agent !== 'policy' || !content.sections?.length || !content.sections.every((s) => s.policyNo)) return choices
  return (choices ?? []).filter((c) => !/^\d+번 저장$/.test(c.label))
}
