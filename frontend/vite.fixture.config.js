// 더미 서비스 실행용 vite 설정 (backend/chat/router_core/MEMO.md 22절) — 팀의 vite.config.js 는 그대로 둔다
//   cd frontend && npx vite --config vite.fixture.config.js   → http://localhost:5174 → 장고 8001 (spending_fixture.service_settings)
// - CSRF 쿠키 이름을 서비스(5173 · 8000)와 다르게 읽는다: utils/api.js 를 고치지 않고 이 실행에서만 바꿔 읽게 한다
// - 화면 맨 위에 '테스트 환경 · 기준일' 띠 (기준일은 fixture 설계 파일의 as_of)
// - 챗봇 화면: pages/AssistantPage.jsx(팀 화면, mock 라우터) 대신 src/fixture/FixtureAssistantPage.jsx(실제 API)를 연결 (2단계)
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

const CSRF_COOKIE = 'mc_fixture_csrftoken'  // backend/spending_fixture/service_settings.py 의 CSRF_COOKIE_NAME 과 같게
const design = JSON.parse(readFileSync(new URL('../backend/spending_fixture/data/design.json', import.meta.url), 'utf8'))
const AS_OF = design.as_of.slice(0, 10)

function fixtureCsrfCookie() {
  return {
    name: 'fixture-csrf-cookie',
    enforce: 'pre',
    transform(code, id) {
      if (!id.split('?')[0].endsWith('/src/utils/api.js')) return null
      const out = code.replaceAll("readCookie('csrftoken')", `readCookie('${CSRF_COOKIE}')`)
      // api.js 의 쿠키 읽기가 바뀌었으면 조용히 넘어가지 않고 멈춘다 (서비스 쿠키를 섞어 쓰지 않게)
      if (out === code) throw new Error("utils/api.js 에서 readCookie('csrftoken') 을 찾지 못했어요 — vite.fixture.config.js 를 맞춰 주세요")
      return out
    },
  }
}

const FIXTURE_PAGE = fileURLToPath(new URL('./src/fixture/FixtureAssistantPage.jsx', import.meta.url))

// AssistantPage.jsx 를 가져오는 곳(App.jsx)은 그대로 두고, 이 실행에서만 fixture 화면으로 바꿔 끼운다
function fixtureAssistantPage() {
  return {
    name: 'fixture-assistant-page',
    enforce: 'pre',
    async resolveId(source, importer, options) {
      if (!source.endsWith('AssistantPage.jsx') || importer?.includes('/src/fixture/')) return null
      const found = await this.resolve(source, importer, { ...options, skipSelf: true })
      return found?.id.endsWith('/src/pages/AssistantPage.jsx') ? FIXTURE_PAGE : null
    },
  }
}

// 색만이 아니라 글자로 테스트 환경임을 알린다. 클릭은 아래 화면으로 통과
const BAND = `<div role="note" style="position:fixed;top:0;left:0;right:0;z-index:9999;pointer-events:none;`
  + `padding:2px 8px;font:600 12px/1.5 sans-serif;text-align:center;background:#fff3cd;color:#5c4400;`
  + `border-bottom:1px solid #e0c46c">테스트 환경 · 기준일 ${AS_OF}</div>`

function fixtureBand() {
  return { name: 'fixture-band', transformIndexHtml: (html) => html.replace('<body>', `<body>\n    ${BAND}`) }
}

export default defineConfig({
  plugins: [react(), fixtureCsrfCookie(), fixtureBand(), fixtureAssistantPage()],
  server: {
    port: 5174,
    strictPort: true,  // 5174 가 쓰이고 있으면 다른 포트로 바꾸지 않고 멈춘다 (5173 서비스와 헷갈리지 않게)
    proxy: { '/api': 'http://127.0.0.1:8001' },
  },
})
