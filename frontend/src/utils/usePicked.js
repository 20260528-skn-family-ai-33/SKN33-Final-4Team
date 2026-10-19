// 여러 개를 골랐다 뺐다 하는 목록 (칩·카드 여러 개 선택)
// 사용법: const banks = usePicked(['신한', '국민'])
//         banks.has('신한') → true / banks.toggle('우리') → 넣거나 빼기 / banks.list → 고른 순서대로
import { useState } from 'react'

export function usePicked(initial = []) {
  const [list, setList] = useState(initial)
  const has = (x) => list.includes(x)
  const toggle = (x) => setList((now) => (now.includes(x) ? now.filter((v) => v !== x) : [...now, x]))
  return { list, has, toggle, set: setList }
}
