/** 秋季主题色板（主平台暖色调）。登录页保持独立不动。 */

export const T = {
  // 布局背景（暖米色渐变）
  layoutBg: 'linear-gradient(160deg, #faf4ea 0%, #f4ead7 55%, #efe1c8 100%)',
  // 面板 / 卡片
  panel: '#fffdf8',
  panelSoft: '#faf2e3',
  panelBorder: '1px solid #eadbbf',
  shadow: '0 6px 22px rgba(120, 82, 30, 0.10)',
  // 文本（暖棕）
  text: '#3d2c1d',
  textSoft: 'rgba(61, 44, 29, 0.72)',
  textFaint: 'rgba(122, 96, 60, 0.45)',
  // 主色 / 强调（秋季金橙）
  primary: '#d97706',
  primaryDeep: '#b45309',
  accent: '#ea580c',
  // 语义色（浅底用深一档）
  gold: '#b45309',
  green: '#15803d',
  blue: '#1d4ed8',
  purple: '#7e22ce',
  red: '#dc2626',
  // 侧边栏（深秋暖棕渐变）
  sidebarBg: 'linear-gradient(180deg, #5b1a03 0%, #7c2d12 55%, #92400e 100%)',
  sidebarText: 'rgba(255, 244, 226, 0.95)',
  sidebarSub: 'rgba(255, 224, 178, 0.65)',
  // 大屏面板（暖白玻璃）
  screenPanel: 'rgba(255, 253, 248, 0.86)',
  screenBorder: '1px solid rgba(180, 130, 60, 0.22)',
  screenShadow: '0 10px 30px rgba(120, 82, 30, 0.12)',
} as const
