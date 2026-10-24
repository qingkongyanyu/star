/** 由秋季色板派生 antd 主题令牌（ConfigProvider 单一来源）。
 *
 * 主平台所有颜色收敛到 config/theme.ts 的 T 色板，避免在 main.tsx 里硬编码。
 */

import type { ThemeConfig } from 'antd'
import { T } from './theme'

export const antdTheme: ThemeConfig = {
  token: {
    colorPrimary: T.primary,
    colorInfo: T.primary,
    colorLink: T.primaryDeep,
    colorSuccess: T.green,
    colorWarning: T.accent,
    colorError: T.red,
    colorText: T.text,
    colorTextSecondary: '#8a7256',
    colorTextTertiary: '#b09a7a',
    colorBorder: '#e6d7bd',
    colorBorderSecondary: '#efdfc6',
    colorBgLayout: '#f7f0e2',
    colorBgContainer: T.panel,
    colorBgElevated: T.panel,
    borderRadius: 10,
    fontSize: 14,
  },
  components: {
    Layout: {
      bodyBg: '#f7f0e2',
      siderBg: '#7c2d12',
      headerBg: 'rgba(255, 253, 248, 0.9)',
    },
    Card: {
      colorBgContainer: T.panel,
      colorBorderSecondary: '#eadbbf',
      boxShadowTertiary: '0 6px 22px rgba(120, 82, 30, 0.08)',
    },
    Menu: {
      darkItemBg: 'transparent',
      darkItemColor: 'rgba(255, 236, 210, 0.75)',
      darkItemHoverColor: '#fff',
      darkItemSelectedBg: 'rgba(234, 88, 12, 0.35)',
      darkItemSelectedColor: '#fff',
      itemBorderRadius: 8,
      itemMarginInline: 8,
    },
    Table: {
      headerBg: '#f6ecda',
      headerColor: '#5a4328',
      rowHoverBg: '#fdf6e8',
    },
    Modal: { contentBg: T.panel, headerBg: T.panel },
  },
}
