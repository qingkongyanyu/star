/** 全局错误边界：捕获渲染/生命周期异常，展示友好回退而非白屏。 */

import { Component } from 'react'
import type { ErrorInfo, ReactNode } from 'react'
import { Button, Result } from 'antd'

interface ErrorBoundaryProps {
  children: ReactNode
}

interface ErrorBoundaryState {
  error: Error | null
}

export default class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { error: null }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // 生产环境可上报监控平台；此处仅打印便于排查
    console.error('[ErrorBoundary]', error, info.componentStack)
  }

  render() {
    if (this.state.error) {
      return (
        <Result
          status="error"
          title="页面加载出错"
          subTitle={this.state.error.message || '发生了未知错误，请重试'}
          extra={
            <Button type="primary" onClick={() => this.setState({ error: null })}>
              重新加载
            </Button>
          }
        />
      )
    }
    return this.props.children
  }
}
