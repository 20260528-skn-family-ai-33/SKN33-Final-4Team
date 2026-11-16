// 매월 며칠 입력칸 (11/12) — 숫자로 입력 + 칸 안 [직접 입력|말일] 전환. 월급 급여일 · 고정지출 출금일 · 저축 이체일 · 대출 납부일 공통
// props: value = '' | '1'~'31' (글자), onChange(글자), id, name = 폼(FormData)으로 읽는 화면이면 같은 이름의 숨은 칸을 둔다,
//        what = '급여일' 같은 칸 이름 (안내 · 오류 문구), optional = 비워도 되는 칸(비우면 '모름')
// 숫자만 2자리까지 받는다. 1~31 이 아니면 칸 아래에 바로 알려 준다(저장 막기는 부르는 쪽에서 dayBad 로)
// 29 · 30 · 31일은 그날이 없는 달은 말일로 계산한다는 안내 (API_RULES '매월 N일'). [말일] = 31 로 보내고 칸은 '말일'로 잠근다
import { useState } from 'react'
import { LAST_DAY, dayBad, dayDigits, dayOk } from '../utils/days.js'

export default function DayInput({ id, name, value, onChange, what = '날짜', optional = false }) {
  const [lastMode, setLast] = useState(value === String(LAST_DAY))
  // 부르는 쪽이 값을 바꿔 31 이 아니게 되면(저장 뒤 서버 값으로 되돌림 등) '말일' 잠금도 풀린 것으로 본다
  const last = lastMode && value === String(LAST_DAY)
  const bad = dayBad(value)
  const errorId = `${id}-error`
  return (
    <>
      <span className="mc-unitinput mc-unit-toggle mc-day-toggle">
        <input id={id} className={bad ? 'mc-bad' : undefined} type="text" inputMode="numeric" autoComplete="off"
          placeholder={optional ? '모름' : '25'} value={last ? '말일' : value} disabled={last} aria-invalid={bad}
          aria-describedby={bad ? errorId : undefined} onChange={(e) => onChange(dayDigits(e.target.value))} />
        {/* '일'은 입력한 숫자 바로 뒤에 붙는다: 같은 글자를 안 보이게 깔고 그 뒤에 '일' (빈 칸이면 예시 25 뒤, 비워도 되는 칸은 안 붙임) */}
        {!last && (value || !optional) && <span className="mc-day-suffix" aria-hidden="true"><span>{value || '25'}</span>일</span>}
        <span className="mc-unit-switch" role="group" aria-label={`${what} 입력 방식`}>
          {[[false, '직접 입력'], [true, '말일']].map(([l, label]) => (
            <button key={label} type="button" aria-pressed={last === l}
              onClick={() => { if (last !== l) { setLast(l); onChange(l ? String(LAST_DAY) : '') } }}>{label}</button>
          ))}
        </span>
      </span>
      {name && <input type="hidden" name={name} value={value} />}
      {bad && <span className="mc-field-error mc-day-error" id={errorId} role="alert">{what}은 1~31 중에서 입력해 주세요.</span>}
      {dayOk(value) && Number(value) >= 29 && !last && <span className="mc-day-note">{value}일이 없는 달은 말일을 {what}로 계산해요.</span>}
    </>
  )
}
