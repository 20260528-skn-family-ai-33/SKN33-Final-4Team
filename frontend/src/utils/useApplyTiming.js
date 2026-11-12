// 관리 화면(고정지출 · 정기 저축)의 [저장] → 적용 시점 묻기 (11/12)
// 추가 · 수정 · 그만두기는 팝업에서 이미 서버에 저장된다. [저장]에서 고르는 건 '이번 예산을 지금 다시 짤지'다
//   mark(): 팝업 저장 뒤 부른다. 무엇이든 바꿨으면 changed → [저장]이 보인다
//   save(needsAsk): 화면이 '이번 예산이 실제로 달라지는지'(고정지출 합계 · 저축 최소 금액 비교)를 알려 준다.
//     달라지고 첫 예산을 승인했으면 가운데 창으로 묻는다 — 이번 주기부터 → 지킬 돈(새 추천값부터) · 다음 주기부터 → backTo + 토스트
//     아니면 묻지 않고 '저장했어요' 뒤 backTo 로 (다시 추천 · AI 호출을 쓸데없이 하지 않게)
import { createElement, useState } from 'react'
import { useNavigate } from 'react-router'
import { usePopup } from '../popups/context.js'
import ApplyTimingPopup from '../popups/ApplyTimingPopup.jsx'
import { useApi } from './api.js'
import { toast } from './toast.js'
import { PATHS } from '../routes.js'

export function useApplyTiming(backTo) {
  const navigate = useNavigate()
  const { openPopup, closePopup } = usePopup()
  const me = useApi('/api/auth/me/')
  const started = me.data?.onboardingCompleted === true
  const ready = !me.loading  // 첫 예산 승인 여부를 아직 모르면 [저장]을 잠근다 (모르고 누르면 묻지 않고 지나감)
  const [changed, setChanged] = useState(false)
  const mark = () => setChanged(true)
  const back = () => navigate(backTo, { replace: true })
  const save = (needsAsk) => {
    if (!(needsAsk && started)) { toast('저장했어요'); back(); return }
    openPopup(createElement(ApplyTimingPopup, { onPick: (k) => {
      closePopup()
      if (k === 'current_cycle') {
        toast('이번 예산을 다시 정해요')
        navigate(PATHS.protect, { replace: true, state: { from: 'my', refreshRecommendation: true } })
      } else {
        toast('다음 주기부터 반영돼요')
        back()
      }
    } }))
  }
  return { started, ready, changed, mark, save }
}
