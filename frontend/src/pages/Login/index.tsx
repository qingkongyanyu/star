/** 登录页：星枢 StarPivot · 秋季山湖实景 + 枫叶漂浮 + 左简介 / 右登录。
 *
 * 结构：
 * - 背景层：login-bg.jpg（微模糊 + 暖色罩层 + 右上光晕）；
 * - 动效层：MapleLeaves（三层景深枫叶，纯 CSS，尊重 reduced-motion）；
 * - 内容层：左侧品牌/能力/Agent 头像/数据指标，右侧奶油玻璃登录卡；
 * - 登录卡：账号 / 密码 / 验证码 / 记住账号 / 错误内联提示 / CapsLock 提示。
 *
 * 登录页使用独立浅色 antd 主题，不污染主平台秋季主题。
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { KeyboardEvent } from 'react'
import { App, Button, Checkbox, ConfigProvider, Form, Input, theme } from 'antd'
import type { ThemeConfig } from 'antd'
import {
  ApartmentOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
  FileTextOutlined,
  LockOutlined,
  ReloadOutlined,
  SafetyOutlined,
  UserOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'

import { getCaptcha, type CaptchaResult } from '../../api/auth'
import MapleLeaves from '../../components/MapleLeaves'
import { AGENTS } from '../../config/agents'
import { ASSETS } from '../../config/assets'
import { useAuthStore } from '../../stores/authStore'
import './index.css'

interface LoginForm {
  username: string
  password: string
  captcha: string
  remember?: boolean
}

const REMEMBER_KEY = 'sp-login-username'

const FEATURES = [
  { icon: <ApartmentOutlined />, title: '意图驱动 · 多 Agent 组队', desc: '识别分析意图后按需编排，任务路径不再千篇一律' },
  { icon: <DatabaseOutlined />, title: '6 类数据源 · 真实取数', desc: 'MySQL / PostgreSQL / OLAP / CSV / Kafka / API' },
  { icon: <ExperimentOutlined />, title: '指标目录 · 口径可追溯', desc: '按真实字段与需求生成指标，算不出来的诚实标注' },
  { icon: <FileTextOutlined />, title: '图文表一体报告', desc: '多图型可视化 + 数据表 + 业务解读，结论可复核' },
]

const STATS = [
  { value: '12', label: '协同 Agent' },
  { value: '6', label: '数据源类型' },
  { value: '12+', label: '图表类型' },
  { value: '100%', label: '真实取数' },
]

const loginTheme: ThemeConfig = {
  algorithm: theme.defaultAlgorithm,
  token: {
    colorPrimary: '#b45309',
    colorText: '#3d2c1d',
    colorTextPlaceholder: '#b09a7a',
    colorBorder: '#e6d3b3',
    colorBgContainer: '#fffdf8',
    borderRadius: 10,
    controlHeight: 44,
    fontSize: 14,
  },
  components: {
    Input: {
      activeBorderColor: '#d97706',
      hoverBorderColor: '#e0a45c',
      activeShadow: '0 0 0 3px rgba(217,119,6,0.12)',
    },
    Checkbox: { colorPrimary: '#b45309' },
    Button: { fontWeight: 600 },
  },
}

function readRememberedUser(): string {
  try {
    return localStorage.getItem(REMEMBER_KEY) ?? ''
  } catch {
    return ''
  }
}

export default function Login() {
  const navigate = useNavigate()
  const login = useAuthStore((s) => s.login)
  const { message } = App.useApp()
  const [form] = Form.useForm<LoginForm>()
  const remembered = useRef(readRememberedUser())
  const [captcha, setCaptcha] = useState<CaptchaResult | null>(null)
  const [captchaError, setCaptchaError] = useState('')
  const [refreshing, setRefreshing] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [shaking, setShaking] = useState(false)
  const [capsLock, setCapsLock] = useState(false)
  const shakeTimer = useRef<number | null>(null)

  const refreshCaptcha = useCallback(async () => {
    setRefreshing(true)
    setCaptchaError('')
    try {
      setCaptcha(await getCaptcha())
    } catch (e) {
      setCaptchaError((e as Error).message || '验证码加载失败，请稍后重试')
    } finally {
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    refreshCaptcha()
    return () => {
      if (shakeTimer.current !== null) window.clearTimeout(shakeTimer.current)
    }
  }, [refreshCaptcha])

  const triggerShake = () => {
    setShaking(false)
    window.requestAnimationFrame(() => setShaking(true))
    if (shakeTimer.current !== null) window.clearTimeout(shakeTimer.current)
    shakeTimer.current = window.setTimeout(() => setShaking(false), 520)
  }

  const handleCapsLock = (event: KeyboardEvent<HTMLInputElement>) => {
    if (typeof event.getModifierState === 'function') {
      setCapsLock(event.getModifierState('CapsLock'))
    }
  }

  const onFinish = async (values: LoginForm) => {
    setError('')
    setLoading(true)
    try {
      await login(values.username.trim(), values.password, captcha?.captcha_id ?? '', values.captcha.trim())
      try {
        if (values.remember) localStorage.setItem(REMEMBER_KEY, values.username.trim())
        else localStorage.removeItem(REMEMBER_KEY)
      } catch {
        // localStorage 不可用（隐私模式）不影响登录
      }
      navigate('/task-board', { replace: true })
    } catch (e) {
      const msg = (e as Error).message || '登录失败，请检查账号密码与验证码'
      setError(msg)
      message.error(msg)
      form.setFieldValue('captcha', '')
      await refreshCaptcha()
      triggerShake()
    } finally {
      setLoading(false)
    }
  }

  const captchaSvgUrl = captcha ? `data:image/svg+xml;utf8,${encodeURIComponent(captcha.svg)}` : ''

  return (
    <div className="sp-login">
      {/* 背景与罩层 */}
      <div className="sp-login__bg" aria-hidden />
      <div className="sp-login__veil" aria-hidden />
      <div className="sp-login__glow" aria-hidden />
      <MapleLeaves />

      <div className="sp-login__content">
        {/* 左侧：系统简介 */}
        <section className="sp-intro" aria-label="平台简介">
          <div className="sp-brand">
            <img className="sp-brand__logo" src={ASSETS.logo} alt="星枢 StarPivot" />
            <div>
              <div className="sp-brand__name">星枢 StarPivot</div>
              <div className="sp-brand__sub">STARPIVOT · DATA INTELLIGENCE</div>
            </div>
          </div>

          <div className="sp-eyebrow">12 AGENT 智能分析矩阵</div>
          <h1 className="sp-title">
            让数据自己
            <br />
            <em>讲故事</em>
          </h1>
          <p className="sp-subtitle">
            从一句业务需求到可交付的分析：意图识别、指标口径、真实取数、多图型可视化与图文表报告，
            全链路由 Agent 协同完成，关键数字可逐项复核。
          </p>

          <div className="sp-features">
            {FEATURES.map((item) => (
              <div className="sp-feature" key={item.title}>
                <div className="sp-feature__icon">{item.icon}</div>
                <div>
                  <div className="sp-feature__title">{item.title}</div>
                  <div className="sp-feature__desc">{item.desc}</div>
                </div>
              </div>
            ))}
          </div>

          <div className="sp-agents">
            <span className="sp-agents__label">协同 Agent</span>
            <div className="sp-agents__row">
              {AGENTS.map((agent) => (
                <span className="sp-agent" key={agent.capability} title={agent.name}>
                  <img src={agent.avatar} alt={agent.name} />
                </span>
              ))}
            </div>
          </div>

          <div className="sp-stats">
            {STATS.map((stat) => (
              <div key={stat.label}>
                <div className="sp-stat__value">{stat.value}</div>
                <div className="sp-stat__label">{stat.label}</div>
              </div>
            ))}
          </div>
        </section>

        {/* 右侧：登录卡 */}
        <section className="sp-login__panel" aria-label="登录">
          <ConfigProvider theme={loginTheme}>
            <div className={`sp-card${shaking ? ' sp-shake' : ''}`}>
              <img className="sp-card__logo" src={ASSETS.logo} alt="星枢 logo" />
              <h2 className="sp-card__title">欢迎回来</h2>
              <div className="sp-card__sub">登录以继续你的数据分析工作</div>

              {error ? (
                <div className="sp-error" role="alert">
                  {error}
                </div>
              ) : null}

              <Form
                form={form}
                layout="vertical"
                size="large"
                requiredMark={false}
                colon={false}
                initialValues={{ username: remembered.current, remember: Boolean(remembered.current) }}
                onFinish={onFinish}
              >
                <Form.Item
                  label={<span className="sp-label">账号</span>}
                  name="username"
                  rules={[{ required: true, message: '请输入账号' }]}
                >
                  <Input
                    prefix={<UserOutlined style={{ color: '#b09a7a' }} />}
                    placeholder="请输入账号"
                    autoComplete="username"
                    autoFocus
                    allowClear
                  />
                </Form.Item>

                <Form.Item
                  label={<span className="sp-label">密码</span>}
                  name="password"
                  rules={[{ required: true, message: '请输入密码' }]}
                >
                  <Input.Password
                    prefix={<LockOutlined style={{ color: '#b09a7a' }} />}
                    placeholder="请输入密码"
                    autoComplete="current-password"
                    onKeyDown={handleCapsLock}
                    onKeyUp={handleCapsLock}
                  />
                </Form.Item>
                {capsLock ? <div className="sp-caps">⚠ Caps Lock 已开启</div> : null}

                <Form.Item label={<span className="sp-label">验证码</span>} required style={{ marginTop: 14 }}>
                  <div className="sp-captcha">
                    <Form.Item
                      name="captcha"
                      noStyle
                      rules={[{ required: true, message: '请输入验证码' }]}
                    >
                      <Input
                        prefix={<SafetyOutlined style={{ color: '#b09a7a' }} />}
                        placeholder="输入右侧 4 位验证码"
                        maxLength={4}
                        autoComplete="off"
                      />
                    </Form.Item>
                    <div
                      className="sp-captcha__box"
                      role="button"
                      tabIndex={0}
                      aria-label="刷新验证码"
                      title="看不清？点击刷新"
                      onClick={() => {
                        if (!refreshing) refreshCaptcha()
                      }}
                      onKeyDown={(event) => {
                        if ((event.key === 'Enter' || event.key === ' ') && !refreshing) refreshCaptcha()
                      }}
                    >
                      {captchaSvgUrl ? (
                        <img src={captchaSvgUrl} alt="验证码图形" />
                      ) : (
                        <ReloadOutlined spin={refreshing} style={{ color: '#9a6a2f' }} />
                      )}
                      <span className="sp-captcha__mask">
                        <ReloadOutlined /> 点击刷新
                      </span>
                    </div>
                  </div>
                  {captchaError ? <div className="sp-caps">{captchaError}</div> : null}
                </Form.Item>

                <div className="sp-row">
                  <Form.Item name="remember" valuePropName="checked" noStyle>
                    <Checkbox>记住账号</Checkbox>
                  </Form.Item>
                  <Button type="link" className="sp-link" onClick={refreshCaptcha} disabled={refreshing}>
                    看不清？换一张
                  </Button>
                </div>

                <Button htmlType="submit" block loading={loading} className="sp-submit">
                  登 录
                </Button>
              </Form>

              <div className="sp-devhint">账号由系统管理员统一分配，如需开通请联系管理员</div>
            </div>
          </ConfigProvider>
        </section>
      </div>

      <footer className="sp-login__footer">© 2026 星枢 StarPivot · 企业级智能数据分析中台</footer>
    </div>
  )
}
