"use strict";

const swaggerSettings = {{ settings|safe }};

const requestInterceptor = (request) => {
  const target = new URL(request.url, window.location.href);
  if (target.origin !== window.location.origin) {
    throw new Error("이 문서에서는 현재 서버와 같은 출처의 API만 호출할 수 있어요.");
  }
  if (request.method && request.method.toUpperCase() !== "GET") {
    throw new Error("현재 서버 확인 화면에서는 GET 요청만 실행할 수 있어요.");
  }
  // 로그인 쿠키를 직접 입력하거나 브라우저 저장소에 보관하지 않는다.
  request.credentials = "same-origin";
  return request;
};

const ui = SwaggerUIBundle({
  ...swaggerSettings,
  url: "{{ schema_url|escapejs }}",
  dom_id: "#swagger-ui",
  presets: [SwaggerUIBundle.presets.apis],
  layout: "BaseLayout",
  requestInterceptor,
});
