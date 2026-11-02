// 더미 서비스 실행용 vite 설정 (backend/chat/router_core/MEMO.md 22 · 25절) — 화면은 팀 화면 그대로, 붙는 곳만 다르다
//   cd frontend && npx vite --config vite.fixture.config.js   → http://localhost:5174 → 장고 8001 (spending_fixture.service_settings)
// - 프록시 대상 8001 + CSRF 쿠키 이름(서비스 5173 · 8000 과 다르게 — 같은 localhost 에서 두 로그인이 서로 덮지 않게)
//   5173 에서 같은 일: API_PROXY_TARGET=http://127.0.0.1:8001 VITE_CSRF_COOKIE=mc_fixture_csrftoken npm run dev
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const CSRF_COOKIE = 'mc_fixture_csrftoken'  // backend/spending_fixture/service_settings.py 의 CSRF_COOKIE_NAME 과 같게

export default defineConfig({
  plugins: [react()],
  define: { 'import.meta.env.VITE_CSRF_COOKIE': JSON.stringify(CSRF_COOKIE) },  // utils/api.js 가 읽는 쿠키 이름
  server: {
    port: 5174,
    strictPort: true,  // 5174 가 쓰이고 있으면 다른 포트로 바꾸지 않고 멈춘다 (5173 서비스와 헷갈리지 않게)
    proxy: { '/api': 'http://127.0.0.1:8001' },
  },
})
