import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // 프록시: 리액트(5173)에서 /api 로 보내는 요청을 장고(8000)에게 대신 전해 준다
    // → 리액트 코드에는 fetch('/api/home/') 처럼 주소 뒷부분만 쓰면 된다
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
