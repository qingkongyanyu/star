/** 任务看板：下发任务 + 任务进度流程图（可关闭）+ 产出文档下载 + 任务列表。 */

import { useCallback, useEffect, useState } from 'react'
import { App, Button, Card, Empty, Input, List, Select, Space, Tag, Typography } from 'antd'
import { CloseOutlined, DownloadOutlined, EyeOutlined } from '@ant-design/icons'

import { downloadAssetFile } from '../../api/asset'
import { listDataSourceOptions } from '../../api/dataSource'
import { createTask, getTaskDag, listTasks } from '../../api/task'
import AssetPreviewModal from '../../components/AssetPreviewModal'
import DagGraph from '../../components/DagGraph'
import { DOC_LABEL, STATUS_COLOR, TERMINAL_STATUS } from '../../config/labels'
import { useWsEvents } from '../../stores/wsEvents'
import { useNavigate } from 'react-router-dom'
import type { DAGOut, DAGNode, TaskOut } from '../../types/api'
import type { DataSourceOption } from '../../api/dataSource'

export default function TaskBoard() {
  const { message } = App.useApp()
  const navigate = useNavigate()
  const [requirement, setRequirement] = useState('')
  const [tasks, setTasks] = useState<TaskOut[]>([])
  const [dag, setDag] = useState<DAGOut | null>(null)
  const [polling, setPolling] = useState(false)
  const [dataSources, setDataSources] = useState<DataSourceOption[]>([])
  const [dsId, setDsId] = useState<number>()

  const refreshTasks = async () => {
    const resp = await listTasks(1, 10)
    setTasks(resp.items)
  }

  useEffect(() => {
    refreshTasks().catch(() => undefined)
    listDataSourceOptions()
      .then(setDataSources)
      .catch(() => undefined)
  }, [])

  const submit = async () => {
    if (!requirement.trim()) {
      message.warning('请输入需求')
      return
    }
    if (!dsId) {
      message.warning('请先选择一个数据源（任务必须基于真实数据源）')
      return
    }
    try {
      const created = await createTask(requirement.trim(), dsId)
      setDag(created)
      setRequirement('')
      setPolling(true)
      await refreshTasks()
      message.success(`任务已创建：${created.task_id}`)
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  // 拉取最新 DAG；终态则停止轮询
  const refreshDag = useCallback(async (taskId: string) => {
    try {
      const latest = await getTaskDag(taskId)
      setDag(latest)
      if ((TERMINAL_STATUS as readonly string[]).includes(latest.status)) {
        setPolling(false)
        await refreshTasks()
      }
    } catch {
      setPolling(false)
    }
  }, [])

  // 兜底轮询（5s；WS 断线仍能推进）
  useEffect(() => {
    if (!polling || !dag) return
    const timer = setInterval(() => refreshDag(dag.task_id), 5000)
    return () => clearInterval(timer)
  }, [polling, dag, refreshDag])

  // WS 事件即时刷新（任务相关事件到达 → 立即拉 DAG）
  const lastEvent = useWsEvents((s) => s.lastEvent)
  useEffect(() => {
    if (!polling || !dag || !lastEvent) return
    const relevant = ['task.created', 'artifact.published', 'dq.blocked', 'task.completed']
    if (relevant.includes(lastEvent.type) && lastEvent.data.task_id === dag.task_id) {
      refreshDag(dag.task_id)
    }
  }, [lastEvent, polling, dag, refreshDag])

  const openDag = async (t: TaskOut) => {
    try {
      const d = await getTaskDag(t.task_id)
      setDag(d)
      setPolling(!(TERMINAL_STATUS as readonly string[]).includes(d.status))
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const closeDag = () => {
    setDag(null)
    setPolling(false)
  }

  const downloadAsset = async (assetId: string, label = '') => {
    try {
      await downloadAssetFile(assetId, label)
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const [previewAssetId, setPreviewAssetId] = useState<string | null>(null)
  const [previewTitle, setPreviewTitle] = useState('')

  const previewAsset = (assetId: string, label: string) => {
    setPreviewAssetId(assetId)
    setPreviewTitle(label)
  }

  const docNodes = (dag?.nodes ?? []).filter(
    (n: DAGNode) => DOC_LABEL[n.agent_type] && n.status === 'SUCCESS' && n.output_asset_id,
  )

  return (
    <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
      <Card title="下发任务">
        <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
          <Space.Compact style={{ width: '100%' }}>
            <Select
              allowClear
              placeholder="选择数据源（可选）"
              style={{ width: 260 }}
              value={dsId}
              onChange={setDsId}
              options={dataSources.map((ds) => ({
                value: ds.id,
                label: `${ds.name}（${ds.type.toUpperCase()}）`,
              }))}
            />
            <Input
              value={requirement}
              onChange={(e) => setRequirement(e.target.value)}
              placeholder="输入分析需求，如：分析近30天GMV走势并输出报告"
              onPressEnter={submit}
            />
            <Button type="primary" loading={polling} onClick={submit}>
              下发
            </Button>
          </Space.Compact>
          {dataSources.length > 0 && (
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              已接入 {dataSources.length} 个数据源，选择后 Agent 将基于真实数据探查与取数
            </Typography.Text>
          )}
        </Space>
      </Card>

      {/* 任务进度流程图：始终可见，可关闭 */}
      <Card
        title={
          <Space>
            <span>任务进度流程图</span>
            {dag && (
              <Space size={6}>
                <Tag color="geekblue">{dag.task_id}</Tag>
                <Tag color={STATUS_COLOR[dag.status] ?? 'default'}>{dag.status}</Tag>
              </Space>
            )}
          </Space>
        }
        extra={
          dag ? (
            <Button type="text" icon={<CloseOutlined />} onClick={closeDag} title="关闭流程图" />
          ) : undefined
        }
      >
        {dag ? (
          <Space orientation="vertical" size="small" style={{ width: '100%' }}>
            <Typography.Text type="secondary">
              节点 {dag.nodes.length} · 边 {dag.edges.length}
              {polling ? ' · 执行中，自动刷新' : ' · 已终态'}
            </Typography.Text>
            <DagGraph dag={dag} />
            {dag.status === 'SUCCESS' && docNodes.length > 0 && (
              <div>
                <Typography.Text type="secondary" style={{ marginRight: 8 }}>
                  产出文档：
                </Typography.Text>
                <Space>
                  {docNodes.map((n) => (
                    <Space key={n.node_id} size={6}>
                      <Button size="small" type="primary" ghost icon={<EyeOutlined />} onClick={() => previewAsset(n.output_asset_id!, DOC_LABEL[n.agent_type] ?? n.agent_type)}>
                        {DOC_LABEL[n.agent_type] ?? n.agent_type} · 预览
                      </Button>
                      <Button size="small" icon={<DownloadOutlined />} onClick={() => downloadAsset(n.output_asset_id!, DOC_LABEL[n.agent_type] ?? n.agent_type)}>
                        下载
                      </Button>
                    </Space>
                  ))}
                </Space>
              </div>
            )}
          </Space>
        ) : (
          <Empty description="下发新任务，或点击任务列表「查看 DAG」查看执行流程" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        )}
      </Card>

      <Card title="任务列表">
        {tasks.length === 0 ? (
          <Empty />
        ) : (
          <List
            dataSource={tasks}
            renderItem={(t) => (
              <List.Item
                actions={[
                  <Button key="ws" size="small" type="primary" ghost onClick={() => navigate(`/task/${t.task_id}`)}>
                    工作台
                  </Button>,
                  <Button key="dag" size="small" onClick={() => openDag(t)}>
                    查看 DAG
                  </Button>,
                ]}
              >
                <List.Item.Meta
                  title={
                    <Space>
                      {t.task_id}
                      <Tag color={STATUS_COLOR[t.status] ?? 'default'}>{t.status}</Tag>
                    </Space>
                  }
                  description={`发起人：${t.requester} · ${new Date(t.create_time).toLocaleString()}`}
                />
              </List.Item>
            )}
          />
        )}
      </Card>

      <AssetPreviewModal
        assetId={previewAssetId ?? ''}
        title={previewTitle}
        open={previewAssetId !== null}
        onClose={() => setPreviewAssetId(null)}
        onError={(msg) => message.error(msg)}
      />
    </Space>
  )
}
