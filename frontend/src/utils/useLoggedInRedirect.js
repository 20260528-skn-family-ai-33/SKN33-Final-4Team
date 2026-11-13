// 이미 로그인한 회원이 로그인 · 회원가입 화면에 오면 있어야 할 화면으로 보낸다 (11/12 피드백)
// 예: 가입 → 시작하기 → 뒤로 가기 → 로그인 화면 → [회원가입] → '로그아웃한 뒤 회원가입해 주세요'
// GET /api/auth/me/ 가 회원 정보를 주면 로그인 상태 — 첫 예산을 승인했으면 홈, 아니면 시작하기. 로그인 안 했으면(401 등) 그대로 둔다
import { useEffect } from 'react'
import { useNavigate } from 'react-router'
import { PATHS } from '../routes.js'
import { useApi } from './api.js'

export function useLoggedInRedirect() {
  const navigate = useNavigate()
  const { data } = useApi('/api/auth/me/')
  const to = data?.userId ? (data.onboardingCompleted ? PATHS.home : PATHS.onboard) : null
  useEffect(() => { if (to) navigate(to, { replace: true }) }, [to, navigate])
}
