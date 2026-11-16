// 머니쿠션 마스코트 (쿠션 위 강아지, 원본: 머니쿠션_마스코트_아이콘/01_mascot/mascot_cushion_dog.svg).
// 소비 유형(spending_analysis/personality.py 의 mascot 코드)마다 포즈 · 눈 · 입 · 소품을 바꾼다. 얼굴 · 색은 원본 그대로.
// 장식용이라 유형 이름은 옆 글자로도 적혀 있다 — 색 · 그림만으로 뜻을 전하지 않는다.
const INK = '#1B2230'
const CREAM = '#FFF8E3'
const EDGE = '#E6D6A8'

// pose: sit(앉기) · stand(두 발로 서기) · jump(점프)
// cushion: 방석 위인지 · tilt: 고개 기울기(도) · arms: 팔(시작 x,y → 끝 x,y, 없으면 포즈 기본) · armsOver: 팔을 얼굴 위에 그림
// eyes: dot · happy(^^) · star · shades / mouth: smile · open · tongue · wavy
// head: 머리에 붙는 소품 / props: 몸 쪽 소품
const MOODS = {
  basic: { pose: 'sit', cushion: true, tilt: -8, eyes: 'dot', mouth: 'smile', head: ['question'] },
  hello: { pose: 'sit', cushion: true, tilt: -4, eyes: 'happy', mouth: 'open', blush: true, head: ['sparkle'],
    armsOver: true, arms: [[163, 326, 163, 372], [214, 290, 304, 196]] },  // 인사 (시작하기 · 가이드): 방석 위에서 한 손 흔들기
  planner: { pose: 'sit', cushion: true, eyes: 'dot', mouth: 'smile', head: ['glasses'], props: ['clipboard'],
    arms: [[163, 326, 163, 372], [200, 300, 240, 318]] },  // 계획 척척형: 안경 + 체크리스트 들고
  stretch: { pose: 'stand', tilt: 6, eyes: 'dot', mouth: 'wavy', head: ['sweat'],
    arms: [[146, 290, 130, 336], [214, 290, 318, 176]] },  // 이번만 조금 더형: 머리 긁적 + 땀방울
  saver: { pose: 'sit', cushion: true, eyes: 'happy', mouth: 'smile', blush: true, props: ['coins', 'piggy'], armsOver: true,
    arms: [[150, 300, 130, 346], [210, 300, 230, 346]] },  // 차곡차곡 모으는형: 방석 위에서 돼지저금통 꼭 안기 + 동전 탑
  shopping: { pose: 'stand', eyes: 'star', mouth: 'open', props: ['bags'],
    arms: [[146, 290, 116, 330], [214, 290, 244, 330]] },  // 사고 싶은 게 많아형: 양손 쇼핑백
  fun: { pose: 'jump', eyes: 'happy', mouth: 'open', head: ['party'], props: ['confetti'] },  // 즐거움은 못 참아형: 점프
  food: { pose: 'sit', eyes: 'happy', mouth: 'tongue', blush: true, head: ['drool'], props: ['bowl'] },  // 맛있는 게 제일 좋아형: 밥그릇 앞 침흘리기
  travel: { pose: 'stand', eyes: 'shades', mouth: 'smile', props: ['suitcase'],
    arms: [[146, 290, 130, 336], [214, 290, 246, 322]] },  // 어디든 떠나고 싶어형: 선글라스 + 캐리어
  beauty: { pose: 'sit', tilt: 5, eyes: 'dot', mouth: 'smile', blush: true, head: ['sparkle'], props: ['mirror'],
    arms: [[163, 326, 163, 372], [200, 302, 316, 266]] },  // 나를 가꾸는 게 좋아형: 손거울
}

const star = (cx, cy, r) => Array.from({ length: 10 }, (_, i) => {
  const a = Math.PI / 5 * i - Math.PI / 2
  const d = i % 2 ? r * 0.45 : r
  return `${(cx + d * Math.cos(a)).toFixed(1)},${(cy + d * Math.sin(a)).toFixed(1)}`
}).join(' ')

// 팔다리: 테두리 선 위에 크림색 선을 겹쳐 둥근 막대로 그린다
function Limb({ from, w = 24 }) {
  const [x1, y1, x2, y2] = from
  return <g strokeLinecap="round">
    <line x1={x1} y1={y1} x2={x2} y2={y2} stroke={EDGE} strokeWidth={w + 4} />
    <line x1={x1} y1={y1} x2={x2} y2={y2} stroke={CREAM} strokeWidth={w} />
  </g>
}

// 목줄: 턱 바로 아래 목에 감기는 짧은 띠 + 원화 메달 (가슴까지 늘어지면 팔을 든 포즈에서 겨드랑이에 걸려 보인다)
const Collar = () => <g>
  <path d="M140 284 Q180 318 220 284" fill="none" stroke="#D2452F" strokeWidth="12" strokeLinecap="round" />
  <circle cx="180" cy="318" r="12" fill="#FFC738" stroke="#E79F12" strokeWidth="2" />
  <path d="M174.5 313.5 l2.6 9 l2.9 -7.5 l2.9 7.5 l2.6 -9 M173 317.6 h14" fill="none" stroke="#8A5200" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
</g>

const Cushion = () => <g>
  <polygon points="104,350 256,350 292,402 68,402" fill="#DDF58C" stroke="#DDF58C" strokeWidth="24" strokeLinejoin="round" />
  <polygon points="104,350 256,350 292,402 68,402" fill="#182235" stroke="#182235" strokeWidth="24" strokeLinejoin="round" opacity="0.26" />
  <polygon points="104,338 256,338 292,390 68,390" fill="#DDF58C" stroke="#DDF58C" strokeWidth="24" strokeLinejoin="round" />
  <polygon points="112,345 248,345 284,391 76,391" fill="none" stroke="#182235" strokeOpacity="0.32" strokeWidth="2.6" strokeLinecap="round" strokeLinejoin="round" strokeDasharray="0.1 9" />
</g>

const Tail = ({ x, y }) => <g>
  <circle cx={x} cy={y} r="17" fill="#F6B55F" />
  <path d={`M${x} ${y - 10} a10 10 0 1 1 -9.5 7`} fill="none" stroke="#E88F3C" strokeWidth="3.5" strokeLinecap="round" />
</g>

// 포즈별 몸통 (머리 · 팔은 따로). arms = 기본 팔/앞다리
const BODIES = {
  sit: {
    arms: [[163, 326, 163, 372], [197, 326, 197, 372]],
    draw: () => <g>
      <Tail x={252} y={318} />
      <path d="M132 262 C120 300 112 340 124 372 Q180 392 236 372 C248 340 240 300 228 262 Z" fill={CREAM} stroke={EDGE} strokeWidth="2" />
      <ellipse cx="180" cy="272" rx="40" ry="24" fill="#F1E3BC" />
      <ellipse cx="126" cy="366" rx="24" ry="18" fill={CREAM} stroke={EDGE} strokeWidth="2" />
      <ellipse cx="234" cy="366" rx="24" ry="18" fill={CREAM} stroke={EDGE} strokeWidth="2" />
    </g>,
  },
  stand: {
    arms: [[146, 290, 130, 336], [214, 290, 230, 336]],
    draw: () => <g>
      <Tail x={240} y={300} />
      <Limb from={[162, 340, 158, 392]} w={28} /><Limb from={[198, 340, 202, 392]} w={28} />
      <ellipse cx="154" cy="396" rx="20" ry="11" fill={CREAM} stroke={EDGE} strokeWidth="2" />
      <ellipse cx="206" cy="396" rx="20" ry="11" fill={CREAM} stroke={EDGE} strokeWidth="2" />
      <path d="M138 262 C126 296 128 338 146 350 Q180 362 214 350 C232 338 234 296 222 262 Z" fill={CREAM} stroke={EDGE} strokeWidth="2" />
      <ellipse cx="180" cy="272" rx="36" ry="20" fill="#F1E3BC" />
    </g>,
  },
  jump: {
    arms: [[146, 288, 40, 240], [214, 288, 320, 240]],
    draw: () => <g>
      <Tail x={242} y={292} />
      <Limb from={[162, 340, 130, 380]} w={28} /><Limb from={[198, 340, 230, 380]} w={28} />
      <path d="M138 262 C126 296 128 338 146 350 Q180 362 214 350 C232 338 234 296 222 262 Z" fill={CREAM} stroke={EDGE} strokeWidth="2" />
      <ellipse cx="180" cy="272" rx="36" ry="20" fill="#F1E3BC" />
    </g>,
  },
}

function Eyes({ kind }) {
  if (kind === 'happy') return <g fill="none" stroke={INK} strokeWidth="9" strokeLinecap="round">
    <path d="M96 212 Q119 184 142 212" /><path d="M218 212 Q241 184 264 212" />
  </g>
  if (kind === 'star') return <g fill="#FFC738" stroke={INK} strokeWidth="4" strokeLinejoin="round">
    <polygon points={star(119, 204, 30)} /><polygon points={star(241, 204, 30)} />
  </g>
  if (kind === 'shades') return <g>
    <rect x="84" y="182" width="72" height="46" rx="18" fill={INK} /><rect x="204" y="182" width="72" height="46" rx="18" fill={INK} />
    <path d="M156 196 Q180 186 204 196" fill="none" stroke={INK} strokeWidth="6" />
    <path d="M96 192 l16 0 M216 192 l16 0" stroke="#5B6B86" strokeWidth="5" strokeLinecap="round" />
  </g>
  return <g>
    <circle cx="119" cy="204" r="27" fill={INK} /><circle cx="241" cy="204" r="27" fill={INK} />
    <circle cx="110" cy="194" r="6.5" fill="#fff" /><circle cx="250" cy="194" r="6.5" fill="#fff" />
  </g>
}

function Mouth({ kind }) {
  const line = { fill: 'none', stroke: '#33261F', strokeWidth: 6.5, strokeLinecap: 'round', strokeLinejoin: 'round' }
  if (kind === 'smile') return <path d="M160 280 Q180 298 200 280" {...line} />
  if (kind === 'wavy') return <path d="M156 286 q12 -10 24 0 q12 10 24 0" {...line} />
  if (kind === 'open') return <path d="M157 275 Q180 314 203 275 Z" fill="#8B3A2B" stroke="#33261F" strokeWidth="3" strokeLinejoin="round" />
  if (kind === 'tongue') return <g>
    <path d="M160 280 Q180 296 200 280" {...line} />
    <path d="M178 288 h20 v14 a10 10 0 0 1 -20 0 Z" fill="#F28A9B" stroke="#D96A7E" strokeWidth="2.5" />
  </g>
  return null
}

// 머리에 붙는 소품 (고개를 기울이면 같이 기운다)
const HEAD = {
  question: <text x="304" y="112" fontSize="54" fontWeight="800" fill="#2655DB" textAnchor="middle">?</text>,
  glasses: <g fill="#FFFFFF" fillOpacity="0.18" stroke="#3A2A20" strokeWidth="6">
    <circle cx="119" cy="204" r="38" /><circle cx="241" cy="204" r="38" />
    <path d="M157 200 Q180 188 203 200" fill="none" /><path d="M81 196 L64 184 M279 196 L296 184" fill="none" strokeLinecap="round" />
  </g>,
  sweat: <path d="M296 112 q16 22 0 32 q-16 -10 0 -32 Z" fill="#8FD3F4" stroke="#5BB4E0" strokeWidth="2" />,
  party: <g>
    <polygon points="152,66 208,66 180,6" fill="#7AA7FF" stroke="#2655DB" strokeWidth="3" strokeLinejoin="round" />
    <path d="M160 50 L200 50 M168 32 L192 32" stroke="#FFC738" strokeWidth="5" />
    <circle cx="180" cy="8" r="8" fill="#FF8FA3" />
  </g>,
  // 침: 입꼬리에서 길게 늘어진 방울 (맛있는 게 제일 좋아형)
  drool: <g fill="#A8DDF7" stroke="#6CBFE6" strokeWidth="2">
    <path d="M154 282 C147 302 144 330 150 344 a11 11 0 0 0 18 -6 C166 322 161 300 160 284 Z" />
    <circle cx="156" cy="336" r="3" fill="#FFFFFF" stroke="none" />
  </g>,
  sparkle: <g fill="#FFC738"><polygon points={star(302, 96, 16)} /><polygon points={star(58, 124, 10)} /></g>,
}

// 몸 쪽 소품
const PROPS = {
  clipboard: <g transform="translate(228 276)">
    <rect width="58" height="70" rx="8" fill="#FFFFFF" stroke="#2655DB" strokeWidth="4" />
    <rect x="18" y="-6" width="22" height="12" rx="4" fill="#2655DB" />
    <path d="M12 24 l7 7 l14 -14 M12 48 l7 7 l14 -14" fill="none" stroke="#2655DB" strokeWidth="5" strokeLinecap="round" strokeLinejoin="round" />
  </g>,
  bags: <g>
    {[[86, 326, '#FF8FA3', '#C2475E'], [222, 326, '#7AA7FF', '#2655DB']].map(([x, y, fill, line]) => <g key={x} transform={`translate(${x} ${y})`}>
      <path d="M14 14 v-6 a14 14 0 0 1 28 0 v6" fill="none" stroke={line} strokeWidth="4" />
      <rect x="2" y="12" width="52" height="50" rx="8" fill={fill} stroke={line} strokeWidth="3" />
      <circle cx="28" cy="36" r="7" fill="#FFF" opacity="0.8" />
    </g>)}
  </g>,
  confetti: <path d="M60 90 l8 12 M306 80 l-10 12 M44 150 l12 2 M318 150 l-12 2 M84 40 l4 12 M276 36 l-4 12" stroke="#E88F3C" strokeWidth="6" strokeLinecap="round" />,
  // 차곡차곡 모으는형: 돼지저금통(다리 앞) + 옆에 쌓인 동전
  piggy: <g>
    <ellipse cx="180" cy="356" rx="56" ry="40" fill="#FFB3C1" stroke="#E07A93" strokeWidth="3" />
    <path d="M146 324 l-6 -16 l18 8 Z M214 324 l6 -16 l-18 8 Z" fill="#FFB3C1" stroke="#E07A93" strokeWidth="3" strokeLinejoin="round" />
    <ellipse cx="180" cy="364" rx="16" ry="11" fill="#FF9BB0" stroke="#E07A93" strokeWidth="2.5" />
    <circle cx="174" cy="364" r="2.6" fill="#B94E68" /><circle cx="186" cy="364" r="2.6" fill="#B94E68" />
    <path d="M168 330 h24" stroke="#B94E68" strokeWidth="5" strokeLinecap="round" />
    <circle cx="180" cy="306" r="11" fill="#FFC738" stroke="#E79F12" strokeWidth="2.5" />
  </g>,
  coins: <g fill="#FFC738" stroke="#E79F12" strokeWidth="2.5">
    {[388, 376, 364, 352].map((y) => <ellipse key={y} cx="292" cy={y} rx="22" ry="8" />)}
  </g>,
  bowl: <g>
    <ellipse cx="180" cy="364" rx="62" ry="12" fill="#8A5A2B" />
    <path d="M110 362 h140 a70 34 0 0 1 -140 0 Z" fill="#D2452F" stroke="#A8331F" strokeWidth="3" />
    <path d="M150 360 q8 -16 18 -2 q10 -18 22 0 q10 -14 20 2" fill="#C98A4A" stroke="#8A5A2B" strokeWidth="2" />
    <text x="180" y="390" fontSize="20" fontWeight="800" fill="#FFF3C4" textAnchor="middle">₩</text>
  </g>,
  suitcase: <g transform="translate(232 314)">
    <path d="M18 0 v-12 h20 v12" fill="none" stroke="#2655DB" strokeWidth="5" />
    <rect width="58" height="74" rx="10" fill="#7AA7FF" stroke="#2655DB" strokeWidth="3" />
    <path d="M14 10 V64 M44 10 V64" stroke="#2655DB" strokeWidth="3" />
    <circle cx="12" cy="80" r="6" fill={INK} /><circle cx="46" cy="80" r="6" fill={INK} />
  </g>,
  mirror: <g>
    <path d="M318 264 L330 232" stroke="#E79F12" strokeWidth="8" strokeLinecap="round" />
    <circle cx="336" cy="206" r="28" fill="#CFE8FF" stroke="#E79F12" strokeWidth="6" />
    <path d="M322 196 l12 -10" stroke="#FFFFFF" strokeWidth="5" strokeLinecap="round" />
  </g>,
}

// 들고 있는 소품은 팔 아래(발이 손잡이를 덮게), 밥그릇 · 책은 다리 앞, 속도선 · 꽃가루는 몸 밖
const FRONT = new Set(['bowl', 'piggy'])
const AROUND = new Set(['confetti'])

const HEAD_SHIFT = { jump: '' }

export const MASCOT_MOODS = Object.keys(MOODS)

export default function Mascot({ mood = 'basic', label, size = 88 }) {
  const known = Boolean(MOODS[mood])
  const look = known ? MOODS[mood] : MOODS.basic
  const body = BODIES[look.pose]
  const props = look.props ?? []
  const arms = look.arms ?? body.arms
  const lift = look.pose === 'jump' ? 'translate(0 -22)' : ''
  const headMove = [HEAD_SHIFT[look.pose] ?? '', look.tilt ? `rotate(${look.tilt} 180 170)` : ''].join(' ').trim()
  const brow = (look.head ?? []).includes('glasses') ? 148 : 158
  const armLimbs = <g>{arms.map((a, i) => <Limb key={i} from={a} w={look.pose === 'sit' && a[0] === a[2] ? 26 : 22} />)}</g>
  return (
    <svg className="mc-mascot" viewBox="-24 -24 408 444" width={size} height={size * 444 / 408}
      role={label ? 'img' : undefined} aria-label={label} aria-hidden={label ? undefined : true} data-mood={known ? mood : 'basic'}>
      {look.cushion ? <Cushion /> : <ellipse cx="180" cy="404" rx={look.pose === 'jump' ? 64 : 92} ry="9" fill="#182235" opacity="0.14" />}
      <g transform={lift}>
        {body.draw()}
        <Collar />
        {props.filter((p) => !FRONT.has(p) && !AROUND.has(p)).map((p) => <g key={p}>{PROPS[p]}</g>)}
        {!look.armsOver && armLimbs}
        {props.filter((p) => FRONT.has(p)).map((p) => <g key={p}>{PROPS[p]}</g>)}
        <g transform={headMove}>
          <path d="M142 60 L51 30 Q34 24 38.5 41.5 L72 172 Z" fill="#F6B55F" />
          <path d="M218 60 L309 30 Q326 24 321.5 41.5 L288 172 Z" fill="#F6B55F" />
          <path d="M180 50 C243 50 294 101 294 164 C294 230 224 290 180 290 C136 290 66 230 66 164 C66 101 117 50 180 50 Z" fill={CREAM} stroke={EDGE} strokeWidth="2" />
          <ellipse cx="146" cy={brow} rx="18" ry="10" fill="#E88F3C" transform={`rotate(24 146 ${brow})`} />
          <ellipse cx="214" cy={brow} rx="18" ry="10" fill="#E88F3C" transform={`rotate(-24 214 ${brow})`} />
          {look.blush && <g fill="#F6A5A0" opacity="0.75"><ellipse cx="94" cy="246" rx="17" ry="9" /><ellipse cx="266" cy="246" rx="17" ry="9" /></g>}
          <Eyes kind={look.eyes} />
          <ellipse cx="180" cy="262" rx="14" ry="9.5" fill="#33261F" />
          <Mouth kind={look.mouth} />
          {(look.head ?? []).map((key) => <g key={key}>{HEAD[key]}</g>)}
        </g>
                {look.armsOver && armLimbs}
      </g>
      {props.filter((p) => AROUND.has(p)).map((p) => <g key={p}>{PROPS[p]}</g>)}
    </svg>
  )
}
