// 추천 순번은 대화 내부에서 유지하고, 화면 제목에는 정책명만 보여 준다.
export const policyTitle = (title) => title.replace(/^\d+\.\s+/, '')

export function policyChoices(content, choices = content.choices) {
  // ID가 없는 이전 메시지는 기존 선택지를 유지한다.
  if (content.agent !== 'policy' || !content.sections?.length || !content.sections.every((s) => s.policyNo)) return choices
  return (choices ?? []).filter((c) => !/^\d+번 저장$/.test(c.label))
}
