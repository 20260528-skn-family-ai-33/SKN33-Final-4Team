// 소비 유형 코드와 첨부 이미지 파일명이 일치한다. 모르는 코드는 기본 포즈로 표시한다.
export const MASCOT_MOODS = ['basic', 'hello', 'planner', 'saver', 'stretch', 'shopping', 'fun', 'food', 'travel', 'beauty']

export default function Mascot({ mood = 'basic', label, size = 88 }) {
  const pose = MASCOT_MOODS.includes(mood) ? mood : 'basic'
  return <img className="mc-mascot" src={`/mascots/kushong/${pose}.svg`} width={size} height={size}
    alt={label ?? ''} aria-hidden={label ? undefined : true} data-mood={pose} draggable={false} />
}
