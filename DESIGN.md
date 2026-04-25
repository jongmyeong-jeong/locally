# Locally Design System

## Product Context

Locally는 한국어 오디오를 로컬 환경에서 전사하는 도구다. 
모든 처리는 사용자 기기 안에서 이루어지며, 클라우드나 외부 서버 업로드가 없다. 
사용자는 오디오 파일을 녹음하거나 업로드하고, Whisper로 로컬 전사를 수행한 뒤, AI 요약을 검토하고 복사한다.

## 1. Visual Theme & Atmosphere

"Local processing made calm" — 로컬 처리의 안정감을 차분한 시각 언어로 전달한다.

Locally는 **productivity SaaS 로컬 웹앱**이다. 
사용자는 매일 long-form 한국어 transcript을 다루므로, 시각 언어는 하루 종일 봐도 피로하지 않은 monochrome-first tone을 유지해야 한다.

**Key Characteristics:**
- **Border-defined surfaces**: surface 간 분리는 `1px solid rgba(34,42,53,0.10)` border로 명확히 한다.
- **Crisp, not fluffy**: shadow는 최소한으로 사용한다. fluffy elevation보다 crisp separation을 선호한다.
- **Productive density**: Section spacing은 40–64px로 유지한다. 과도한 여백(80px+)은 피한다.
- **Monochrome-first**: 첫인상에서 흰색 → 옅은 회색 → near-black의 grayscale로 읽혀야 한다.
- **Korean readability first**: 어떤 시스템 룰도 한국어 본문 reading 가독성을 침해하지 않는다.

## 2. Color Palette & Roles

### Primary

| Token | Value | Usage |
| --- | --- | --- |
| `--color-text-primary` | `#171717` | 본문 텍스트, heading, 가장 강한 대비 |
| `--color-action-primary` | `#171717` | Primary CTA 배경. text-primary와 같은 값을 의도적으로 공유 |
| `--color-on-primary` | `#ffffff` | dark CTA 위에 올라가는 텍스트 |

### Surface

| Token | Value | Usage |
| --- | --- | --- |
| `--color-bg-page` | `#f8f9fa` | 가장 바깥 워크스페이스 배경. 살짝 cool gray |
| `--color-bg-card` | `#ffffff` | 카드/패널/모달 본체 |
| `--color-bg-subpanel` | `#fafafa` | 카드 안의 카드 (glossary panel, summary block 등 inner section) |

### Text Hierarchy

| Token | Value | Usage |
| --- | --- | --- |
| `--color-text-primary` | `#171717` | heading, 본문 |
| `--color-text-secondary` | `#4d4d4d` | description copy, form label, 카드 보조 텍스트 |
| `--color-text-tertiary` | `#666666` | timestamp, file metadata, count, hint |
| `--color-text-disabled` | `#808080` | placeholder, 비활성 버튼 라벨 |

### Border

| Token | Value | Usage |
| --- | --- | --- |
| `--color-border-subtle` | `rgba(0, 0, 0, 0.08)` | 일반 카드/패널/divider/input border |
| `--color-border-strong` | `rgba(0, 0, 0, 0.12)` | hover/active/selected 강조 border |

alpha 기반으로 3단계 surface(`#f8f9fa` / `#ffffff` / `#fafafa`) 위에서 perceived contrast가 일정하게 유지된다.

### Interactive States

| Token | Value | Usage |
| --- | --- | --- |
| `--color-bg-hover` | `rgba(0, 0, 0, 0.04)` | row/menu/button hover. surface 종류와 무관하게 일정한 강도 |
| `--color-bg-selected` | `#f4f4f5` | 현재 선택된 list row, active menu item, focused segment |
| `--color-backdrop` | `rgba(0, 0, 0, 0.5)` | modal/dialog 뒤 dim layer |

### Blue — Link & Focus Only

| Token | Value | Usage |
| --- | --- | --- |
| `--color-blue` | `#0072f5` | base blue. link 텍스트에 사용 |
| `--color-link` | `#0072f5` | in-text hyperlink. underline 필수 |
| `--color-focus-ring` | `rgba(0, 114, 245, 0.5)` | `:focus-visible` 시 outline. base blue + alpha 0.5 |

### Semantic — Desaturated

| Token | Value | Usage |
| --- | --- | --- |
| `--color-success` | `#27a644` | 전사 완료, 저장 완료 등 성공 시그널 |
| `--color-warning` | `#d97706` | 모델 미설치, 디스크 공간 부족 등 경고 |
| `--color-error` | `#dc2626` | 업로드 실패, 전사 실패 등 에러 |

Tailwind 600 단계의 vivid tone. status 신호의 명확성을 우선해 채도를 높였다. 단, 큰 면적의 fill은 피하고 텍스트 + 아이콘 + 좁은 fill 영역에만 사용한다.

### Rules

1. **Monochrome-first**: 첫인상에서 흰색 → 옅은 회색 → near-black의 grayscale로 읽혀야 한다. blue/green/orange/red 어느 것도 화면의 정체성이 되어선 안 된다.
2. **Blue는 link와 focus 두 곳에만 허용**한다. selection, hover, active state에 blue tint를 흘리지 않는다.
3. **Workflow 단계는 색으로 표현하지 않는다**. record / transcribe / review / summarize는 텍스트 + 단계 인디케이터(charcoal fill) + 아이콘으로만 구분한다.
4. **Primary CTA는 `#171717` monochrome**. 색상 CTA(blue/green button) 금지.
5. **Border는 alpha 토큰 두 단계만** 사용한다. hex 고정 border를 직접 적기보다 토큰을 통해 일관성을 강제한다.
6. **Semantic color는 signal이지 theme가 아니다**. 큰 면적의 success-green panel 같은 처리를 피한다. 텍스트 + 아이콘 + 좁은 fill 영역에만 사용한다.
7. **warm tone(cream, brown-gray, beige) 금지**. 모든 회색은 `#171717` base의 무채색 grayscale에서 파생된다.


## 3. Typography Rules

### Font Family

```css
--font-sans: 'Pretendard Variable', 'Pretendard', -apple-system, BlinkMacSystemFont,
             'Apple SD Gothic Neo', 'Segoe UI', sans-serif;
--font-mono: ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Monaco,
             'Liberation Mono', monospace;
```

- **Primary**: Pretendard. 한국어 가독성을 시스템 정체성보다 우선한다.
- **Variable 우선**: `Pretendard Variable`이 weight 사이 보간을 매끄럽게 처리. 정적 Pretendard가 fallback.
- **Mono**: 시스템 monospace. 외부 폰트를 추가 로딩하지 않는다 — 로컬 처리 thesis와 정합.

### Weight Tokens

| Token | Weight | Usage |
| --- | --- | --- |
| `--font-weight-regular` | `400` | reading body, UI body |
| `--font-weight-medium` | `500` | UI label, active state, button text |
| `--font-weight-semibold` | `600` | section heading, sub-section, card title |
| `--font-weight-bold` | `700` | display, page title |

4단계 고정. 300 Light(editorial 톤) / 800 ExtraBold(과도한 강조) 사용 금지.

### Type Scale

| Token | Size | Weight | Line-height | Letter-spacing | Usage |
| --- | --- | --- | --- | --- | --- |
| `--type-display` | `32px` | `700` | `1.3` | `-0.03em` | 온보딩, 빈 상태, 모델 설치 같은 entry/event 화면 |
| `--type-page-title` | `24px` | `700` | `1.3` | `-0.02em` | 메인 화면의 page title |
| `--type-section-heading` | `20px` | `600` | `1.3` | `-0.02em` | 카드/패널 단위 section title |
| `--type-sub-section` | `18px` | `600` | `1.4` | `-0.01em` | 서브 그룹 heading |
| `--type-card-title` | `16px` | `600` | `1.4` | `-0.01em` | 작은 카드/리스트 항목 title |
| `--type-reading` | `16px` | `400` | `1.6` | `0` | transcript, summary, long-form description |
| `--type-ui` | `14px` | `400` | `1.5` | `0` | sidebar item, button, form label, list row body |
| `--type-ui-medium` | `14px` | `500` | `1.5` | `0` | active state, emphasized UI label |
| `--type-caption` | `12px` | `500` | `1.4` | `0` | badge, tag, secondary metadata |
| `--type-mono-caption` | `12px` | `400` | `1.4` | `0` | timestamp, file size, duration. `font-family: var(--font-mono)` + `font-feature-settings: 'tnum' on` |

Letter-spacing은 사이즈 클수록 더 negative(-0.03 → -0.02 → -0.01 → 0). 한국어 reading body(16px reading)는 `0`으로 가독성 보호. Reading line-height `1.6`은 한국어 productivity 본문 표준값.

### OpenType Features

- **`tnum` on mono caption만**: timestamp / file size / duration 같은 숫자 metadata 컬럼이 OS 간 일관된 폭으로 정렬된다.
- 그 외 본문/heading에는 OpenType feature를 적용하지 않는다. Pretendard default 렌더링이 한국어에 가장 적합.

```css
.mono-caption {
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.4;
  font-feature-settings: 'tnum' on;
}
```

### Reading Override

긴 transcript / summary 본문 같은 super-long reading 영역은 component 단계에서 `line-height: 1.7`까지 늘릴 수 있다. 일반 description은 `1.6`을 유지한다.

### Principles

- **Korean readability first**: 어떤 시스템 룰도 한국어 본문 reading 가독성을 침해하지 않는다.
- **Compression as system, not gimmick**: display의 `-0.03em` negative tracking은 영어/숫자에 대해 미세한 압축감을 주지만, 한국어 자모를 찌그러뜨리지 않는다.
- **Size + weight, not weight alone**: 한국어 Pretendard는 영어 폰트만큼 weight 차이가 시각적으로 크지 않다. 위계는 size 차이를 기본으로 하고 weight를 보조로 사용한다.
- **No editorial tone**: serif, italic, light(<400) weight, handwritten style은 사용하지 않는다. UI는 calm하고 systematic하게 느껴져야 한다.


## 4. Component Stylings

### Border Radius Scale

| Token | Value | Usage |
| --- | --- | --- |
| `--radius-input` | `4px` | text input, textarea, checkbox |
| `--radius-control` | `6px` | button, badge container, dropdown item, list row |
| `--radius-card` | `8px` | card, modal, panel |
| `--radius-pill` | `9999px` | pill badge, status tag, progress track |

작은 컨트롤(input) < 액션(button) < 컨테이너(card) < 의미적 라벨(pill) 순으로 radius가 커진다. radius가 위계로 작동.

### Buttons

4종 고정: `primary` / `secondary` / `ghost` / `destructive`. 그 외 variant 추가 금지.

**Sizes:**

| Size | Height | Padding | Font |
| --- | --- | --- | --- |
| `sm` | `32px` | `0 10px` | `14px / 500` |
| `md` (default) | `36px` | `0 14px` | `14px / 500` |
| `lg` | `48px` | `0 20px` | `15px / 600` |

**Primary** — 가장 강한 액션 (예: "전사 시작", "요약 생성"):
- Default: `bg #171717 / text #ffffff`
- Hover: `bg #2a2a2a / text #ffffff`
- Active: `bg #0a0a0a / text #ffffff`
- Disabled: `bg #e5e5e5 / text #a3a3a3` (`cursor: not-allowed`)
- Focus-visible: `outline: 2px solid #0072f5; outline-offset: 2px`

**Secondary** — 보조 액션 (예: "취소", "이전 단계"):
- Default: `bg #ffffff / text #171717 / border 1px solid rgba(0,0,0,0.08)`
- Hover: `bg #fafafa / text #171717 / border 1px solid rgba(0,0,0,0.12)`
- Active: `bg #f4f4f5 / text #171717 / border 1px solid rgba(0,0,0,0.12)`
- Disabled: `bg #fafafa / text #c4c4c4 / border 1px solid rgba(0,0,0,0.05)`

**Ghost** — inline 액션, 메뉴 항목 (예: list "⋯", "복사" 아이콘):
- Default: `bg transparent / text #171717`
- Hover: `bg rgba(0,0,0,0.04) / text #171717`
- Active: `bg rgba(0,0,0,0.08) / text #171717`
- Disabled: `bg transparent / text #c4c4c4`
- Icon-only: 정사각형(예: `32×32`) + `radius 6px`, 안에 16px 아이콘

**Destructive** — 파괴적 액션. **confirmation modal 안에서만 사용**:
- Default: `bg #ffffff / text #dc2626 / border 1px solid #dc2626`
- Hover: `bg #fef2f2 / text #b91c1c / border 1px solid #b91c1c`
- Active: `bg #fee2e2 / text #b91c1c / border 1px solid #b91c1c`
- Disabled: `bg #ffffff / text #fecaca / border 1px solid #fecaca`

일반 화면에서 destructive 액션은 ghost 버튼으로 시작 → modal에서 destructive로 최종 확인.

### Inputs

**Text input** (default size):
- `bg #ffffff / text #171717 / border 1px solid rgba(0,0,0,0.08) / radius 4px`
- `height 36px / padding 0 12px / font 14px / 400`
- Placeholder: `#808080`
- Hover: `border 1px solid rgba(0,0,0,0.12)`
- Focus-visible: `border 1px solid #0072f5 / outline 2px solid rgba(0,114,245,0.5) / outline-offset 0`
- Disabled: `bg #fafafa / text #808080`
- Error: `border 1px solid #dc2626` + 아래 `12px / #dc2626` 에러 메시지
- With leading icon: `padding-left 36px`, icon `16px` @ `#666666`

### Cards

| State | bg | border | shadow |
| --- | --- | --- | --- |
| Default | `#ffffff` | `1px solid rgba(0,0,0,0.08)` | none |
| Hover (clickable) | `#ffffff` | `1px solid rgba(0,0,0,0.12)` | none |
| Selected | `#f4f4f5` | `1px solid rgba(0,0,0,0.12)` | none |

- Radius: `8px`
- Padding: `--card-padding-sm: 12px` (compact list item) / `--card-padding-md: 16px` (standard) / `--card-padding-lg: 24px` (featured/empty state)
- Non-clickable card: hover 변화 없음. clickable card에만 hover border 적용.
- Sub-panel: `bg #fafafa / border 1px solid rgba(0,0,0,0.08) / radius 6px` (카드보다 한 단계 작은 radius)

### Modal / Dialog

- Container: `bg #ffffff / border 1px solid rgba(0,0,0,0.08) / radius 8px / no shadow`
- Backdrop: `rgba(0, 0, 0, 0.5)`
- Max-width: `sm 360px` (단일 confirm) / `md 480px` (기본 form) / `lg 640px` (설정/긴 form)
- Padding: header `24px` / body `24px` / footer `16px 24px`
- Footer: 우측 정렬, primary button → secondary button 순서 (macOS 컨벤션)
- Close button: 우상단 ghost icon button (X)
- Animation: `opacity 0→1 + scale 0.96→1, 150ms ease-out`

### Pill / Badge

- `radius 9999px / padding 2px 8px / font 12px / 500 / line-height 1.4`

| Variant | bg | text | Usage |
| --- | --- | --- | --- |
| Neutral | `#f4f4f5` | `#4d4d4d` | 일반 메타 (예: "MP3", "12.3MB") |
| Info | `#ebf5ff` | `#0068d6` | 진행 중 ("전사 중", "요약 생성 중") |
| Success | `#f0fdf4` | `#27a644` | 완료 ("전사 완료", "설치됨") |
| Warning | `#fff7ed` | `#d97706` | 경고 ("디스크 공간 부족") |
| Error | `#fef2f2` | `#dc2626` | 실패 ("전사 실패") |

tinted bg는 `~95% lightness` 옅은 톤, text는 `~30% lightness` 진한 톤. 같은 hue 안에서 contrast 4.5+ 유지.

### Tabs

- Container: `border-bottom: 1px solid rgba(0,0,0,0.08)` (탭 바 아래 divider)
- Tab: `padding 0 12px / height 40px / font 14px / 500 / gap 8px`
- Default: `text #666666 / bg transparent`
- Hover: `text #171717 / bg rgba(0,0,0,0.04)` (bg는 탭 영역 안만)
- Active: `text #171717 / bg transparent / border-bottom: 2px solid #171717`
- Focus-visible: `outline 2px solid #0072f5 / outline-offset -2px`

탭 라벨은 한국어 4~6자 또는 영어 1~2단어 이내. 7자 이상이면 dropdown으로 변경 권장.

### List Row / Sidebar Item

- Container: `padding 8px 12px / radius 6px / margin 2px 0`
- Divider 없음. hover/selected bg 차이로만 row 구분.
- Default: `bg transparent`
- Hover: `bg rgba(0,0,0,0.04)`
- Selected: `bg #f4f4f5`
- Selected + hover: `bg #f4f4f5` (selected 우선)
- Focus-visible: `outline 2px solid #0072f5 / outline-offset -2px`

**Row 내부 구조:**
- 좌측: 16px 아이콘 @ `#666666` (옵션) — gap `12px`
- 중앙: title `14px / 500` + subtitle `12px / 400 / #666666` (2줄)
- 우측: trailing meta (timestamp / ghost icon button) `12px / mono / #666666`

100개+ row는 가상 스크롤 권장.

### Form Controls (Toggle / Checkbox / Radio)

모두 monochrome charcoal — `--color-blue`(focus-ring 외)는 사용하지 않는다.

**Checkbox** (`16×16 / radius 4px`):
- Unchecked: `bg #ffffff / border 1px solid rgba(0,0,0,0.16)`
- Checked: `bg #171717 / border 1px solid #171717` + 11px 흰 체크 아이콘
- Hover (unchecked): `border 1px solid rgba(0,0,0,0.24)`
- Hover (checked): `bg #2a2a2a`
- Disabled: `bg #fafafa / border 1px solid rgba(0,0,0,0.08)`

**Radio** (`16×16 / radius 9999px`):
- 동일한 톤. checked 시 흰 bg + charcoal border + 안에 8px charcoal dot.

**Toggle** (`32×18px track / radius 9999px`):
- Off: `bg rgba(0,0,0,0.16) / 14px 흰 knob (subtle shadow)`
- On: `bg #171717 / 14px 흰 knob`
- Transition: `150ms ease-out`

라벨: control 우측 `12px gap`, font `14px / 400 / #171717`.

### Progress Bar

- Track: `bg rgba(0,0,0,0.08) / height 6px / radius 9999px / width 100%`
- Fill: `bg #171717 / height 6px / radius 9999px / transition width 200ms linear`
- Indeterminate: track 위에 30%-width fill이 좌→우 무한 슬라이드. `animation 1.4s cubic-bezier(0.65, 0, 0.35, 1) infinite`
- 라벨: progress bar 위에 좌측 `14px / 500 / #171717` 상태 텍스트 + 우측 `12px / mono / #666666` 진행률 텍스트

**Variants:**
- Inline (list row 안): `height 4px`
- Standalone (메인 화면): `height 6px`

**Spinner** (작은 inline action 전용): `16px / charcoal / 1.5px stroke / 0.8s rotate`. 메인 작업은 progress bar 사용 원칙.

### Toast / Notification

- 위치: 우하단 `bottom 16px / right 16px`
- Container: `bg #ffffff / border 1px solid rgba(0,0,0,0.08) / radius 8px / padding 12px 14px / max-width 360px / min-width 280px`
- 레이아웃: 좌측 16px 아이콘 + 본문 + 우측 닫기 X (ghost icon button)
- Duration: 4초 후 auto-dismiss. hover 시 정지.
- Stack: 새 toast가 위로 쌓임, gap `8px`
- Animation: 진입 `slide-in-from-right 200ms ease-out` / 퇴장 `fade-out 150ms ease-in`

| Type | Icon | Color |
| --- | --- | --- |
| success | `✓` | `#27a644` |
| error | `✕` | `#dc2626` |
| info | `i` | `#0072f5` |
| neutral | `•` | `#666666` |

Toast는 "행위 완료 알림" 전용. 사용자 입력이 필요한 메시지는 modal 사용.

### Tooltip

- `bg #171717 / text #ffffff / radius 4px / padding 4px 8px / font 12px / 500 / line-height 1.4`
- Max-width `240px` (한국어 약 18자)
- Positioning: 기본 `top`, 화면 끝 시 자동 `bottom`
- Delay: `hover 500ms 후 등장 / hover 종료 즉시 제거`
- 키보드 단축키: 라벨 옆에 mono caption 박스 (예: `복사하기  ⌘C`)

Tooltip은 보조 정보 전용. 사용자가 못 봐도 작업 완수 가능해야 함(접근성).

### Dropdown Menu

- Container: `bg #ffffff / border 1px solid rgba(0,0,0,0.08) / radius 8px / padding 4px / min-width 160px / max-width 320px`
- Animation: `opacity 0→1 + scale 0.98→1 from origin top, 120ms ease-out`
- Positioning: trigger 기준 자동 (top/bottom + left/right). 화면 끝 시 자동 flip.
- Z-index: modal보다 위 (`z-50`)

**Dropdown item:**
- `padding 8px 10px / radius 6px / font 14px / 400 / #171717`
- 좌측 16px 아이콘(옵션) + 라벨 + 우측 단축키 mono caption(옵션)
- Hover: `bg rgba(0,0,0,0.04)`
- Focus (keyboard): `bg #f4f4f5`
- Destructive item: `text #dc2626 / hover bg #fef2f2`

**Divider / Group label:**
- Divider: `1px solid rgba(0,0,0,0.08) / margin 4px 0`
- Group label: `padding 6px 10px / font 11px / 600 / uppercase / color #808080`

Dropdown 항목은 6개 이내 권장. 그 이상이면 검색 가능한 popover(combobox) 사용.

### Empty State

- 중앙 정렬, max-width `320px`, vertical-center in container
- Icon, 일러스트, 큰 CTA 사용하지 않는다.
- Heading: `16px / 600 / #171717` (예: "녹음을 시작해 보세요")
- Description: `14px / 400 / #4d4d4d / line-height 1.5` (예: "마이크로 녹음하거나 오디오 파일을 불러올 수 있어요")
- 그 아래 `16px gap` + small ghost button 또는 link

Empty state는 calm thesis와 가장 정합한 미니멀 형태로 유지.

### Transcript Segment

- Segment row: `display flex / gap 24px / padding 8px 0`
- 좌측 timestamp column (고정 폭 `64px`): `12px / mono / #666666 / tnum on / line-height 1.6` (본문 첫 줄과 baseline 정렬)
- 본문 column: `16px / 400 / #171717 / line-height 1.6`
- Speaker label (옵션): 본문 시작에 inline `12px / 600 / #171717 / margin-right 8px`
- Segment hover: 본문 column에 `bg rgba(0,0,0,0.04) / radius 4px / margin -4px / padding 4px`
- Segment selected/playing: 좌측 `2px charcoal vertical bar` 추가, 본문 weight `500`
- Inline action (segment hover 시 우측 등장): ghost icon button "복사", "편집"

**Reading-mode override**: 화자/timestamp 숨기는 모드 (hide timestamps / hide speaker labels 토글). 그때 segment row gap 사라지고 본문만 남음.

### Behavior Rules

- Button과 control은 playful하지 않고 precise해야 한다.
- 배경색 변화보다 border가 더 많은 역할을 하게 한다.
- Semantic color는 theme가 아니라 signal처럼 보여야 한다.
- 실제 selected / focused state를 설명하지 않는 soft blue-washed panel은 피한다.
- 4종 button variant 외 새 variant를 추가하지 않는다. 부족한 케이스는 size 또는 컨테이너 변경으로 해결한다.


## 5. Layout Principles

### Spacing Scale

4px base + 8-multiple 우선. 모든 padding / margin / gap은 토큰에서만 사용한다 (하드코딩 금지).

| Token | Value | Usage |
| --- | --- | --- |
| `--space-0` | `0` | reset |
| `--space-1` | `4px` | badge padding, icon gap |
| `--space-2` | `8px` | small gap, between related items |
| `--space-3` | `12px` | form row gap, list row padding |
| `--space-4` | `16px` | card padding default |
| `--space-5` | `20px` | group spacing |
| `--space-6` | `24px` | section internal gap |
| `--space-8` | `32px` | small section gap |
| `--space-10` | `40px` | section spacing default |
| `--space-12` | `48px` | large section gap |
| `--space-16` | `64px` | page-level section spacing |
| `--space-20` | `80px` | empty state container (rare) |

토큰에 없는 값이 필요하면 임의로 적지 말고 토큰을 추가한다.

### Canvas Structure

Locally는 web productivity 앱(tablet ~ desktop)이라 fluid 레이아웃을 사용한다. tablet에서는 sidebar가 overlay/collapse로 변형되며, desktop에서는 full sidebar + main 구조를 유지한다.

**Top bar:**
- `height 48px / bg #ffffff / border-bottom 1px solid rgba(0,0,0,0.08) / padding 0 16px`
- 좌측: 앱 로고 + 이름
- 중앙: 현재 화면 제목 (옵션, 화면에 따라 비울 수 있음)
- 우측: 액션 메뉴, 설정, 알림

**Sidebar (left):**
- `width 260px / bg #f8f9fa / border-right 1px solid rgba(0,0,0,0.08) / padding 8px`
- 안에 note list + "새 녹음" button + 검색
- Resize: `min-width 200px / max-width 360px` (drag handle on right border)
- Collapse: `⌘\` 또는 ghost button으로 toggle

**Main:**
- `flex 1 / bg #ffffff / overflow-y auto / padding 24px 32px`
- transcript / settings / 화면 콘텐츠

**Right inspector** (optional 3rd zone):
- `width 320px / bg #fafafa / border-left 1px solid rgba(0,0,0,0.08) / padding 16px`
- 화자 정보, glossary 편집 같은 보조 영역. 닫기 X로 dismiss.
- main 영역을 밀어서 줄임 (덮어쓰지 않음)
- `<1200px window`에서는 modal로 fallback

### Reading Column

main 안에서 long-form 한국어 본문은 max-width로 reading 폭 제한.

```css
.reading-column {
  max-width: 720px;  /* --reading-max-width */
  margin: 0 auto;
}
```

한 줄 약 28~30자 — 한국어 long-form reading 정석값. 좌측 timestamp column(64px)은 reading column 안에 포함됨. 본문 effective width `~632px`.

**예외** — reading column 적용 안 함:
- note list / settings list 같은 ul 영역 (reading 아님, fluid)
- 카드 안 본문 (카드 자체 폭 따름)
- code block / mono 영역 (가로 스크롤)

### Section Spacing Rhythm

| 자리 | Token | Value |
| --- | --- | --- |
| 같은 그룹 안 항목 간 | `--space-3` ~ `--space-4` | `12~16px` |
| 그룹 안 sub-section 간 | `--space-6` | `24px` |
| 다른 섹션 사이 | `--space-10` | `40px` |
| page-level 큰 분리 | `--space-16` | `64px` |
| empty state container | `--space-20` | `80px` (드물게) |

한 화면 안에서 같은 위계의 섹션은 동일 간격으로 통일. Top bar 아래 page 첫 콘텐츠까지 여백은 `--space-6` (24px).

### No Card Grid

Multi-card grid layout(2~3 column 격자)을 사용하지 않는다. note list, settings, 모델 선택 모두 **세로 list**로 통일.

이유: 시선 분산을 막고 위에서 아래로 scan하는 productivity 톤을 유지한다. 큰 화면(1600px+)에서도 list. sidebar + main 구조가 이미 가로 분할이라, main 안에서 추가 가로 분할 안 함. 예외 없음.

### Z-index Scale

| Token | Value | Layer |
| --- | --- | --- |
| `--z-base` | `0` | 기본 콘텐츠 |
| `--z-sticky` | `10` | sticky header, sticky toolbar |
| `--z-dropdown` | `30` | dropdown menu, popover, combobox |
| `--z-modal` | `50` | modal/dialog (backdrop 포함) |
| `--z-toast` | `70` | toast notification |
| `--z-tooltip` | `90` | tooltip |

- Z-index 직접 적지 않는다. 항상 토큰 사용.
- 같은 layer 안에선 DOM 순서로 stacking (later = above).
- Modal 안 dropdown은 modal stacking context 안이므로 자동 위. 별도 조정 불필요.
- Toast가 modal보다 위 — modal 작업 결과 알림이 modal 닫기 전에 보여야 함.
- Tooltip이 가장 위 — 어떤 layer 위에서도 보조 정보 가능해야 함.

### Whitespace Philosophy

여백은 task zone과 reading zone을 분리하기 위한 도구다. 콘텐츠 사이 호흡은 spacing token 안에서, 화면 전체의 호흡은 sidebar의 회색 캔버스(`#f8f9fa`)와 main의 흰 캔버스(`#ffffff`) 대비로 만들어진다. 80px 이상 간격은 empty state, onboarding hero 같은 특수 영역만.


## 6. Depth & Elevation

### Philosophy

Depth는 shadow가 아니라 **border + surface tone 차이**로 만들어진다. fluffy elevation보다 crisp separation을 선호한다.

### Elevation Levels — 3-level Fixed

| Level | Surface | bg | border | Usage |
| --- | --- | --- | --- | --- |
| 0 — Flat | Page background | `#f8f9fa` | — | sidebar bg, page canvas |
| 1 — Bordered | Card / Modal / Dropdown | `#ffffff` | `1px solid rgba(0,0,0,0.08)` | 모든 컨테이너 컴포넌트 |
| 2 — Nested | Sub-panel inside Level 1 | `#fafafa` | `1px solid rgba(0,0,0,0.08)` | 카드 안 glossary panel, summary block, transcript inner section |

**State overlays** — depth 단계가 아닌, 같은 Level 안의 일시적 시각 변화:
- Hover: `bg + rgba(0,0,0,0.04) overlay`
- Selected: `bg #f4f4f5`
- Active/pressed: `bg #f4f4f5 + border-strong`
- Focus: `outline 2px solid #0072f5 / outline-offset`

Level 3 이상 추가 금지. 카드 안의 카드의 카드는 UI 설계 실패 시그널 — 정보 구조를 다시 생각한다. 같은 Level 안에서 다른 색 surface 사용 금지.

### Shadow — Almost Unused

Shadow는 정의된 자리에서만 사용한다. 그 외 모든 자리에선 명시적 금지.

| 자리 | Shadow | 이유 |
| --- | --- | --- |
| Toggle knob | `0 1px 2px rgba(0,0,0,0.12)` | track 위에 떠 있는 floating ball 시각화 |
| Focus ring | `outline 2px solid + outline-offset` | shadow가 아니라 outline. 분리된 시그널. |
| 그 외 모든 자리 | 사용 안 함 | border + bg 변화로만 위계 표현 |

**금지 패턴:**
- Multi-layer shadow stack (여러 shadow를 콤마로 합치기)
- 큰 blur shadow (예: `0 20px 40px ...`)
- Inner shadow / inset highlight

**금지 자리:**
- Card / Modal / Dropdown / Popover — border만 사용
- Button hover — bg 변화만, shadow 안 줌
- Pill / Badge — flat

### Decorative Elements

**Gradient — 전면 금지:**
- Linear / radial / conic / mesh gradient 모두
- Background gradient, button gradient, illustration gradient 모두
- 색상 변화는 항상 단일 hex 또는 alpha. 두 색 사이 자연 보간 금지.

**Divider — 정의된 자리만:**
- Modal header ↔ body 사이 (옵션): `1px solid rgba(0,0,0,0.08)`
- Dropdown 안 그룹 분리: `1px solid rgba(0,0,0,0.08) / margin 4px 0`
- List row 사이: 안 씀 (hover/selected bg로만 구분)
- Form field 사이: 안 씀 (spacing으로만 분리)

**Section break:** 페이지 안 가로선 안 씀. 섹션 분리는 spacing(`--space-10` / `--space-16`)으로.

**Inset highlight / 3D 효과:** `rgba(255,255,255,0.15) 0px 2px 0px inset` 같은 inset highlight 사용 안 함. buttons / cards에 3D 느낌 안 줌. flat 유지.

**Pattern / Texture:** noise, grain, subtle pattern, decorative SVG 배경 모두 안 됨.

### Result

UI는 precise하고 quiet하며 trustworthy하게 느껴져야 한다. 시각적 깊이는 페이지 회색(`#f8f9fa`) → 카드 흰색(`#ffffff`) → 서브패널 회색(`#fafafa`)의 3단계 surface tone 차이와, 모든 컨테이너에 들어가는 alpha 8% border 한 줄로 충분히 만들어진다.


## 7. Do's and Don'ts

### Do

**Color:**
- Primary text와 CTA 배경은 `#171717`로 통일한다.
- 모든 회색은 `#171717` base의 무채색 grayscale에서 파생한다.
- Border는 alpha 토큰 두 단계(`rgba(0,0,0,0.08)` / `rgba(0,0,0,0.12)`)만 사용한다.
- Surface는 page `#f8f9fa` / card `#ffffff` / sub-panel `#fafafa` 3단계로 고정한다.
- Hover는 `rgba(0,0,0,0.04)` alpha overlay로, surface 종류와 무관하게 일정한 강도로 적용한다.
- Semantic color(success/warning/error)는 vivid 톤(`#27a644` / `#d97706` / `#dc2626`)으로 사용한다. 큰 면적의 fill 대신 텍스트 + 아이콘 + 좁은 fill 영역에서만 노출한다.

**Typography:**
- Pretendard Variable을 primary font로 사용한다.
- 한국어 reading body(16px)는 letter-spacing `0`으로 가독성을 100% 보호한다.
- Display(32px)부터 카드 title(16px)까지 letter-spacing은 사이즈 클수록 negative(-0.03em → -0.01em).
- 4단계 weight(400/500/600/700)만 사용한다.
- Mono caption에만 `font-feature-settings: 'tnum' on`을 적용한다.

**Component:**
- 모든 컨테이너는 `1px solid rgba(0,0,0,0.08)` border + `radius 8px`로 정의한다.
- Button은 4 variants × 3 sizes 안에서만 사용한다 — 새 variant 추가 금지.
- Pill/Badge는 5종 의미별 컬러(neutral/info/success/warning/error)를 tinted bg + 진한 text로 사용한다.
- Form control(toggle/checkbox/radio)은 monochrome charcoal로 통일한다.
- Empty state는 icon/일러스트 없이 heading + description + small action으로만 구성한다.

**Layout:**
- 모든 spacing은 토큰(`--space-*`)에서만 사용한다 — 하드코딩 금지.
- Reading column은 `max-width 720px`로 한국어 long-form 가독성을 유지한다.
- Section spacing은 24 / 40 / 64 3-tier rhythm으로 위계를 만든다.
- Multi-card layout은 항상 vertical list로 통일한다 — card grid 금지.
- Z-index는 5단계 토큰으로 관리한다.

**Depth:**
- Shadow는 toggle knob과 focus outline 외에는 사용하지 않는다.
- Depth는 border + surface tone 차이로만 만든다.
- Elevation은 3단계(flat / bordered / nested) 안에서만 표현한다.

**Interaction:**
- focus-visible은 `outline 2px solid #0072f5`로 모든 interactive 요소에 일관 적용한다.
- 한국어 라벨이 길어질 수 있으므로 button에는 ellipsis 또는 wrap 룰을 정의한다.
- Toast는 우하단 stack, 4초 auto-dismiss로 사용자 작업 흐름을 방해하지 않는다.

### Don't

**Color:**
- Blue를 link / focus 외 자리에 사용하지 않는다 (selection, hover, active, checkbox tick 등 어디에도).
- Workflow를 색으로 구분하지 않는다 — record/transcribe/review/summarize는 텍스트와 단계 인디케이터로만 표현한다.
- Warm tone(cream, brown-gray, beige) 일체 사용하지 않는다.
- Pure black `#000000`을 본문 텍스트에 사용하지 않는다 — 항상 `#171717`.
- 큰 면적의 컬러 fill(예: success-green 패널)을 만들지 않는다. 색은 작은 시그널 자리에만.

**Typography:**
- 한국어 본문(body 이하)에 negative letter-spacing 적용하지 않는다.
- weight 300 미만(Light/Thin)이나 800 이상(ExtraBold/Black)을 사용하지 않는다.
- serif, italic, handwritten 폰트 사용하지 않는다.
- positive letter-spacing을 한국어에 적용하지 않는다.
- `'Pretendard Variable'` 외 영어 전용 display 폰트를 추가 로딩하지 않는다.

**Component:**
- Multi-layer shadow stack(여러 shadow 콤마로 합치기)을 사용하지 않는다.
- Destructive button을 confirmation modal 밖에 노출하지 않는다.
- Pill radius(`9999px`)를 일반 button에 적용하지 않는다.
- Native browser select / OS notification을 primary UI로 사용하지 않는다.
- Card grid(2~3 column 격자)를 사용하지 않는다.

**Depth & Decoration:**
- Gradient(linear/radial/conic/mesh)를 어디에도 사용하지 않는다.
- 페이지 안 가로선으로 섹션을 자르지 않는다.
- Inset highlight, 3D 버튼 효과, noise/pattern texture를 사용하지 않는다.
- 일러스트, 큰 brand gesture, hero 그래픽을 사용하지 않는다.

**Layout:**
- centered max-width 같은 marketing 톤 컨테이너를 사용하지 않는다.
- 80~120px section spacing을 사용하지 않는다.
- Z-index를 직접 적지 않는다 — 항상 토큰 사용.

**Tone:**
- Black-and-white를 너무 엄격하게 적용해서 link, focus, selected, status, error 같은 실용적 시그널을 약화시키지 않는다.
- "calm"을 "심심함"으로 해석해서 상호작용 시그널(hover, focus, active)을 모두 제거하지 않는다.


## 8. Responsive Behavior

Locally는 **로컬 웹앱**이다. **tablet (768px+) ~ desktop (1920px+)** 지원. mobile (<768px)은 시스템 책임 범위 외. 향후 Electron 포팅 시에도 동일 룰 적용 (Chromium 렌더링 기반이라 web 룰 그대로 활용).

### Supported Viewport

- **Min width**: `768px` (iPad mini portrait). 미만 viewport는 "Locally는 태블릿 이상 화면에서 사용해 주세요" 안내 화면.
- **Max layout width**: `1920px`. 이상 모니터(ultrawide)에선 reading column 720px max로 좌우 여백 흡수.
- **Min height**: `600px` 권장. 미만에서는 vertical scroll로 대응.
- **Mobile (`<768px`)**: 미지원.

### Width Tiers — 4 Levels

| Tier | Width | Sidebar | Main | Inspector |
| --- | --- | --- | --- | --- |
| **Tablet portrait** | `768~1023px` | overlay (기본 hidden + toggle) | full width | modal로 fallback |
| **Tablet landscape** | `1024~1199px` | narrow `200px` (full label, 작은 padding) | fluid | modal로 fallback |
| **Desktop standard** | `1200~1599px` | full `260px` | fluid | side-panel `320px` 가능 |
| **Desktop wide** | `≥1600px` | full `260px` | fluid | side-panel `320px` 가능 |

**Sidebar — Tablet portrait:**
- 기본 hidden. top bar에 sidebar toggle button 노출.
- Toggle 시 좌측에서 slide-in하는 overlay sheet (main 위에 떠 있음).
- Overlay sheet: `bg #ffffff / border-right 1px solid rgba(0,0,0,0.08) / shadow 0 0 16px rgba(0,0,0,0.12)` (overlay라 예외적으로 shadow 허용)
- Sheet 외 영역 tap 시 자동 close.

**Sidebar — Tablet landscape:**
- Narrow mode 자동 적용. width `200px`, padding `4px`. 항목 라벨은 그대로 표시.
- 사용자가 더 줄이면 collapse 가능.

### Touch / Pointer Input

Tablet은 touch input, desktop은 pointer.

**Touch target minimum** (Apple HIG, WCAG 2.5.5):
- 모든 interactive element(button, list row, dropdown item, toggle, checkbox, radio): **최소 `44×44px` tap area**
- Button md(36px height)도 tablet에선 padding으로 effective tap area 44px 확보. 시각적 button은 36px 그대로, hit area만 확장.
- Icon-only ghost button: 시각적 32×32px이라도 `min-height: 44px / min-width: 44px`로 hit area 확보.

**Hover handling:**

```css
@media (hover: hover) {
  .card:hover { border-color: rgba(0, 0, 0, 0.12); }
  .row:hover { background: rgba(0, 0, 0, 0.04); }
}
```

`:hover` 효과는 항상 `@media (hover: hover)` 안에서 정의 — desktop pointer만 적용. Tablet에서는 hover 효과 미적용 — 대신 tap 시 `bg #f4f4f5 + scale(0.98) transient`.

**Right-click / Context menu:**
- Desktop: 우클릭으로 context menu (Section 4 dropdown 재사용).
- Tablet: long-press(500ms+)로 context menu 등장. menu 외 영역 tap 시 자동 dismiss.

### Component-level Adaptation

| Component | Tablet portrait | Tablet landscape | Desktop |
| --- | --- | --- | --- |
| Modal max-width | `min(원래 max, 92vw)` | 동일 | 동일 |
| Toast max-width | `92vw` | `360px` | `360px` |
| Card padding md | `12px` | `16px` | `16px` |
| Section spacing 40 | `32px` | `40px` | `40px` |
| Section spacing 64 | `48px` | `48px` | `64px` |
| Reading column max | `720px` (main 폭 따라 자연 축소) | 동일 | 동일 |
| Sidebar | overlay (0px 기본) | `200px` narrow | `260px` full |
| Top bar height | `52px` (touch margin) | `48px` | `48px` |
| Inspector | modal | modal | side-panel `320px` 가능 |

### Resize Behavior

- 모든 layout 변화는 CSS media query 또는 container query로 자동 적용.
- JavaScript resize listener 사용 안 함 (CSS only).
- Layout 변화에는 transition 적용 안 함 — 즉시 변경 (resize 중 쾌적성 우선).
- Sidebar overlay (tablet portrait) toggle만 `transform 200ms ease-out` slide-in/out.


## 9. Agent Prompt Guide

### Quick Token Reference

```css
/* Color — Primary */
--color-text-primary:    #171717;
--color-action-primary:  #171717;
--color-on-primary:      #ffffff;

/* Color — Surface */
--color-bg-page:         #f8f9fa;
--color-bg-card:         #ffffff;
--color-bg-subpanel:     #fafafa;

/* Color — Text Hierarchy */
--color-text-secondary:  #4d4d4d;
--color-text-tertiary:   #666666;
--color-text-disabled:   #808080;

/* Color — Border (alpha) */
--color-border-subtle:   rgba(0, 0, 0, 0.08);
--color-border-strong:   rgba(0, 0, 0, 0.12);

/* Color — Interactive States */
--color-bg-hover:        rgba(0, 0, 0, 0.04);
--color-bg-selected:     #f4f4f5;
--color-backdrop:        rgba(0, 0, 0, 0.5);

/* Color — Blue (link & focus only) */
--color-blue:            #0072f5;
--color-link:            #0072f5;
--color-focus-ring:      rgba(0, 114, 245, 0.5);

/* Color — Semantic (vivid) */
--color-success:         #27a644;
--color-warning:         #d97706;
--color-error:           #dc2626;

/* Typography — Font Family */
--font-sans: 'Pretendard Variable', 'Pretendard', -apple-system, BlinkMacSystemFont,
             'Apple SD Gothic Neo', 'Segoe UI', sans-serif;
--font-mono: ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Monaco,
             'Liberation Mono', monospace;

/* Typography — Weights */
--font-weight-regular:   400;
--font-weight-medium:    500;
--font-weight-semibold:  600;
--font-weight-bold:      700;

/* Spacing (4px base, 8-multiple 우선) */
--space-1:  4px;   --space-2:  8px;   --space-3:  12px;
--space-4:  16px;  --space-5:  20px;  --space-6:  24px;
--space-8:  32px;  --space-10: 40px;  --space-12: 48px;
--space-16: 64px;  --space-20: 80px;

/* Radius */
--radius-input:    4px;
--radius-control:  6px;
--radius-card:     8px;
--radius-pill:     9999px;

/* Z-index */
--z-base:     0;
--z-sticky:   10;
--z-dropdown: 30;
--z-modal:    50;
--z-toast:    70;
--z-tooltip:  90;

/* Reading column */
--reading-max-width: 720px;
```

### Type Scale Reference

| Token | Size | Weight | Line-height | Letter-spacing |
| --- | --- | --- | --- | --- |
| `--type-display` | 32px | 700 | 1.3 | -0.03em |
| `--type-page-title` | 24px | 700 | 1.3 | -0.02em |
| `--type-section-heading` | 20px | 600 | 1.3 | -0.02em |
| `--type-sub-section` | 18px | 600 | 1.4 | -0.01em |
| `--type-card-title` | 16px | 600 | 1.4 | -0.01em |
| `--type-reading` | 16px | 400 | 1.7 | 0 |
| `--type-ui` | 14px | 400 | 1.5 | 0 |
| `--type-ui-medium` | 14px | 500 | 1.5 | 0 |
| `--type-caption` | 12px | 500 | 1.4 | 0 |
| `--type-mono-caption` | 12px | 400 | 1.4 | 0 (+ `tnum`) |

### Example Component Prompts

#### 메인 워크스페이스 페이지
Locally 메인 워크스페이스 화면. 좌측 sidebar(width 260px, bg #f8f9fa, border-right 1px solid rgba(0,0,0,0.08), padding 8px) 안에 검색 input + "새 녹음" primary md button + note list. 우측 main(flex 1, bg #ffffff, padding 24px 32px) 안에 page title 24px/700/letter-spacing -0.02em + 그 아래 24px gap + transcript 영역. 전체 layout은 fluid full-window. centered max-width 안 씀.

#### Note list row
카드 아닌 list row 컴포넌트. padding 8px 12px / radius 6px / margin 2px 0. 좌측 16px 아이콘 @ #666666 + gap 12px + 중앙 title(14px / 500 / #171717) + subtitle(12px / 400 / #666666) + 우측 timestamp(12px / mono / #666666 / tnum). hover 시 bg rgba(0,0,0,0.04). selected는 bg #f4f4f5. divider 없음.

#### Primary CTA button
primary md button. height 36px / padding 0 14px / bg #171717 / text #ffffff / font 14px-500 / radius 6px. hover bg #2a2a2a, active bg #0a0a0a, disabled bg #808080. focus-visible 시 outline 2px solid #0072f5 / outline-offset 2px.

#### Transcript segment
transcript segment row. display flex / gap 24px / padding 8px 0. 좌측 64px 폭 timestamp column(12px / mono / #666666 / tnum on / line-height 1.6). 우측 본문 column(16px / 400 / #171717 / line-height 1.6). reading-column max-width 720px / margin 0 auto. segment hover 시 본문 column에 bg rgba(0,0,0,0.04) overlay (`@media (hover: hover)`). selected/playing은 좌측 2px charcoal vertical bar 추가 + 본문 weight 500.

#### Modal — confirmation
가운데 정렬 modal. backdrop rgba(0,0,0,0.5). modal container bg #ffffff / border 1px solid rgba(0,0,0,0.08) / radius 8px / max-width 360px / no shadow. header padding 24px / 18px-600 title. body padding 24px / 14px-400 / #4d4d4d. footer padding 16px 24px / 우측 정렬 / primary md button + secondary md button. 진입 opacity+scale 150ms ease-out.

#### Status pill — info variant
info pill badge. radius 9999px / padding 2px 8px / bg #ebf5ff / text #0068d6 / font 12px-500 / line-height 1.4. inline 사용. (예: "전사 중", "요약 생성 중")

#### Empty state
중앙 정렬 empty state. icon/일러스트 사용 안 함. heading 16px-600 #171717("녹음을 시작해 보세요") + 8px gap + description 14px-400 #4d4d4d / line-height 1.5 / max-width 320px("마이크로 녹음하거나 오디오 파일을 불러올 수 있어요") + 16px gap + small ghost button("녹음 시작") 또는 link("파일 불러오기").

### Iteration Guide

1. **Color**: 모든 회색이 grayscale 토큰에서 왔는가? Blue를 link/focus 외 자리에 쓰지 않았는가?
2. **Typography**: Pretendard Variable 사용? letter-spacing이 사이즈 클수록 negative? 한국어 본문은 letter-spacing 0?
3. **Component**: 4 button variants × 3 sizes 안에서 사용? card grid 안 썼는가? destructive button을 modal 밖에 두지 않았는가?
4. **Spacing**: 모든 padding/margin/gap이 `--space-*` 토큰에서 왔는가?
5. **Depth**: shadow를 toggle knob과 focus outline 외 자리에 쓰지 않았는가? gradient 안 썼는가?
6. **Layout**: sidebar + main fluid 구조? reading column max-width 720px? z-index 토큰 사용?
7. **Tone**: 이 화면을 24" 모니터에서 매일 8시간 봤을 때 calm한가? 시각적 노이즈가 없는가?

### Authoring Rules for Screen Prompts

- 화면 프롬프트는 이 design system을 **확장**한다. 시스템과 충돌하는 결정은 design system 자체를 수정하는 것이 우선.
- 새 컴포넌트가 필요하면, 먼저 4 button variant + list row + card + modal로 표현 가능한지 확인. 정말 필요한 경우만 새 컴포넌트 정의를 design system에 추가.
- 토큰에 없는 값(예: `padding: 13px`)이 필요하면 토큰을 추가한다. 임의 값 하드코딩 금지.
