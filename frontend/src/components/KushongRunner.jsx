// 첨부된 로딩 SVG 자체에 움직임과 reduced-motion 처리가 포함되어 있다.
export default function KushongRunner({ size = 96 }) {
  return <span className="mc-run-motion" style={{ width: size, height: size }} aria-hidden="true">
    <img className="mc-run-dog" src="/mascots/kushong/runner-loading.svg" width={size} height={size}
      alt="" draggable={false} />
  </span>
}
