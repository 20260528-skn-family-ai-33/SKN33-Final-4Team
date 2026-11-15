// 청년 정책 지역 고르기 말풍선: [시·도 | 시·군·구 | 구] 드롭다운 한 줄 (content.inputs 에 region_select 일 때)
// 어느 칸까지 골라도 된다 — 고른 데까지의 전체 이름("전국", "경기도", "경기도 수원시", "경기도 수원시 장안구")을 문장으로 보낸다
// props: onSubmit(text) / done = 이미 지난 질문이거나 보내는 중이면 true (잠금)
import { useState } from 'react'
import { useApi } from '../utils/api.js'
import { cities, districts } from '../utils/policyCards.js'

const NATIONWIDE = '전국'

export default function RegionSelect({ onSubmit, done = false }) {
  const regions = useApi('/api/policies/regions/').data?.items ?? []  // [{ code, name, regions: [{ code, name }] }]
  const [sido, setSido] = useState('')
  const [city, setCity] = useState('')
  const [gu, setGu] = useState('')
  const top = regions.find((t) => t.name === sido)
  const cityList = cities(top)
  const guList = districts(top, city)
  // 고른 시도에 없는 단계의 칸은 뺀다 (인천 · 서울 = 2칸, 전국 · 세종 = 1칸). 시도를 고르기 전에는 3칸
  const showCity = !sido || cityList.length > 0
  const showGu = !sido || cityList.some((name) => districts(top, name).length)

  const submit = (e) => {
    e.preventDefault()
    if (sido) onSubmit?.([sido, city, gu].filter(Boolean).join(' '))
  }

  return (
    <form className="mc-cf mc-region-select" onSubmit={submit}>
      <fieldset disabled={done} className="mc-cf-set">
        <div className="mc-region-row" style={{ gridTemplateColumns: `repeat(${1 + showCity + showGu}, minmax(0, 1fr))` }}>
          <select aria-label="시·도" value={sido} onChange={(e) => { setSido(e.target.value); setCity(''); setGu('') }}>
            <option value="" disabled>시·도</option>
            <option value={NATIONWIDE}>전국</option>
            {regions.map((t) => <option key={t.code} value={t.name}>{t.name}</option>)}
          </select>
          {showCity && <select aria-label="시·군·구" value={city} disabled={!cityList.length} onChange={(e) => { setCity(e.target.value); setGu('') }}>
            <option value="">전체</option>
            {cityList.map((name) => <option key={name}>{name}</option>)}
          </select>}
          {showGu && <select aria-label="구" value={gu} disabled={!guList.length} onChange={(e) => setGu(e.target.value)}>
            <option value="">전체</option>
            {guList.map((name) => <option key={name}>{name}</option>)}
          </select>}
        </div>
        <button type="submit" className="mc-primary mc-cf-go" disabled={!sido}>이 지역으로 찾기</button>
      </fieldset>
    </form>
  )
}
