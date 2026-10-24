/** 任务工作台（任务记忆库）：每个任务拥有独立的 看板/可视化大屏/资产/记忆 视图。
 *
 * - /task            → 任务选择（往期任务浏览器）
 * - /task/:taskId    → 该任务的工作区（顶部任务切换器 + 四个标签页）
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { App, Button, Card, Empty, List, Select, Space, Spin, Table, Tabs, Tag, Typography } from 'antd'
import { DownloadOutlined, EyeOutlined } from '@ant-design/icons'
import { useNavigate, useParams } from 'react-router-dom'

import { downloadAssetFile } from '../../api/asset'
import { getTaskAssets, getTaskDag, getTaskMemory, listTasks } from '../../api/task'
import AssetPreviewModal from '../../components/AssetPreviewModal'
import DagGraph from '../../components/DagGraph'
import { T } from '../../config/theme'
import { DOC_LABEL, STATUS_COLOR, TERMINAL_STATUS, TYPE_LABEL } from '../../config/labels'
import type { AssetOut, DAGOut, DAGNode, MemoryHitOut, TaskOut } from '../../types/api'
import VizScreen from './VizScreen'

function TaskPicker({ onPick }: { onPick: (id: string) => void }) {
  const { message } = App.useApp()
  const [tasks, setTasks] = useState<TaskOut[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    listTasks(1, 100)
      .then((r) => setTasks(r.items))
      .catch((e) => message.error((e as Error).message))
      .finally(() => setLoading(false))
  }, [message])

  return (
    <Card
      title="任务记忆库 · 选择任务"
      extra={<Typography.Text type="secondary" style={{ fontSize: 12 }}>共 {tasks.length} 个任务 · 点击进入该任务的完整工作区</Typography.Text>}
    >
      <Spin spinning={loading}>
        {tasks.length === 0 ? (
          <Empty description="暂无任务，去任务看板下发一个吧" />
        ) : (
          <List
            dataSource={tasks}
            renderItem={(t) => (
              <List.Item
                style={{ cursor: 'pointer' }}
                onClick={() => onPick(t.task_id)}
                actions={[<Button key="go" size="small" type="primary" ghost onClick={() => onPick(t.task_id)}>进入工作区</Button>]}
              >
                <List.Item.Meta
                  title={
                    <Space>
                      <b>{t.task_id}</b>
                      <Tag color={STATUS_COLOR[t.status] ?? 'default'}>{t.status}</Tag>
                      {t.task_type && <Tag>{t.task_type}</Tag>}
                    </Space>
                  }
                  description={`${t.requester || '—'} · ${new Date(t.create_time).toLocaleString()}`}
                />
              </List.Item>
            )}
          />
        )}
      </Spin>
    </Card>
  )
}

export default function TaskWorkspace() {
  const { taskId } = useParams()
  const navigate = useNavigate()
  const { message } = App.useApp()

  const [tasks, setTasks] = useState<TaskOut[]>([])
  const [dag, setDag] = useState<DAGOut | null>(null)
  const [assets, setAssets] = useState<AssetOut[]>([])
  const [memories, setMemories] = useState<MemoryHitOut[]>([])
  const [loading, setLoading] = useState(false)

  // 预览
  const [previewAssetId, setPreviewAssetId] = useState<string | null>(null)
  const [previewTitle, setPreviewTitle] = useState('')

  const loadSeqRef = useRef(0)
  const loadTask = useCallback(
    async (id: string) => {
      const seq = ++loadSeqRef.current
      setLoading(true)
      try {
        const [d, a, m] = await Promise.all([getTaskDag(id), getTaskAssets(id), getTaskMemory(id)])
        if (seq !== loadSeqRef.current) return // 已被更新的请求覆盖，丢弃旧响应
        setDag(d)
        setAssets(a)
        setMemories(m)
      } catch (e) {
        if (seq !== loadSeqRef.current) return
        message.error((e as Error).message)
      } finally {
        if (seq === loadSeqRef.current) setLoading(false)
      }
    },
    [message],
  )

  useEffect(() => {
    // page_size 后端上限 100，传 200 会 422 导致任务切换器永远为空
    listTasks(1, 100)
      .then((r) => setTasks(r.items))
      .catch(() => undefined)
  }, [])

  useEffect(() => {
    if (taskId) loadTask(taskId)
  }, [taskId, loadTask])

  const previewAsset = useCallback((assetId: string, label: string) => {
    setPreviewAssetId(assetId)
    setPreviewTitle(label)
  }, [])

  const downloadAsset = useCallback(async (assetId: string, label = '') => {
    try {
      await downloadAssetFile(assetId, label)
    } catch (e) {
      message.error((e as Error).message)
    }
  }, [message])

  const docNodes = useMemo(
    () => (dag?.nodes ?? []).filter((n: DAGNode) => DOC_LABEL[n.agent_type] && n.status === 'SUCCESS' && n.output_asset_id),
    [dag],
  )

  const currentTask = tasks.find((t) => t.task_id === taskId)

  // ===== 无任务 → 任务选择页 =====
  if (!taskId) {
    return <TaskPicker onPick={(id) => navigate(`/task/${id}`)} />
  }

  return (
    <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
      {/* 顶部：任务切换 + 信息 */}
      <Card>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flex: 1, minWidth: 280 }}>
            <Select
              style={{ width: 300 }}
              value={taskId}
              onChange={(id) => navigate(`/task/${id}`)}
              placeholder="切换任务（含往期任务）"
              options={tasks.map((t) => ({
                value: t.task_id,
                label: `${t.task_id} · ${STATUS_COLOR[t.status] === 'success' ? '成功' : t.status} · ${new Date(t.create_time).toLocaleString().slice(0, 16)}`,
              }))}
            />
            <div style={{ lineHeight: 1.3 }}>
              <Typography.Title level={5} style={{ margin: 0, color: T.text }}>
                任务工作台 · {taskId}
                <Tag color={STATUS_COLOR[dag?.status ?? currentTask?.status ?? ''] ?? 'default'} style={{ marginLeft: 8 }}>
                  {dag?.status ?? currentTask?.status ?? ''}
                </Tag>
              </Typography.Title>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                {currentTask?.requester ? `发起人 ${currentTask.requester} · ` : ''}
                {currentTask ? new Date(currentTask.create_time).toLocaleString() : ''}
              </Typography.Text>
            </div>
          </div>
          <Button onClick={() => navigate('/task')}>查看全部任务</Button>
        </div>
      </Card>

      <Spin spinning={loading}>
        <Tabs
          defaultActiveKey="viz"
          items={[
            {
              key: 'viz',
              label: '📊 数据可视化大屏',
              children: <VizScreen taskId={taskId} />,
            },
            {
              key: 'board',
              label: '🗂 任务看板（DAG）',
              children: dag ? (
                <Card>
                  <div style={{ marginBottom: 12, display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}>
                    <Typography.Text type="secondary">
                      节点 {dag.nodes.length} · 边 {dag.edges.length}
                      {(TERMINAL_STATUS as readonly string[]).includes(dag.status) ? ' · 已终态' : ' · 执行中'}
                    </Typography.Text>
                    {docNodes.length > 0 && (
                      <Space wrap>
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
                    )}
                  </div>
                  <DagGraph dag={dag} />
                </Card>
              ) : (
                <Card>
                  <Empty description="任务 DAG 加载失败" />
                </Card>
              ),
            },
            {
              key: 'assets',
              label: `📦 资产仓库（${assets.length}）`,
              children: (
                <Card title="该任务产出资产">
                  {assets.length === 0 ? (
                    <Empty description="该任务暂无资产产出" />
                  ) : (
                    <Table
                      rowKey="asset_id"
                      dataSource={assets}
                      pagination={false}
                      columns={[
                        { title: '资产 ID', dataIndex: 'asset_id' },
                        { title: '类型', dataIndex: 'type', render: (t: string) => <Tag color="blue">{TYPE_LABEL[t] || t}</Tag> },
                        { title: '标题', dataIndex: 'title' },
                        { title: '版本', dataIndex: 'latest_version', width: 80 },
                        {
                          title: '操作',
                          key: 'action',
                          width: 160,
                          render: (_: unknown, row: AssetOut) => (
                            <Space>
                              <Button size="small" icon={<EyeOutlined />} disabled={!row.latest_version} onClick={() => previewAsset(row.asset_id, `${TYPE_LABEL[row.type] || row.type} · ${row.title}`)}>
                                预览
                              </Button>
                              <Button size="small" icon={<DownloadOutlined />} disabled={!row.latest_version} onClick={() => downloadAsset(row.asset_id, `${TYPE_LABEL[row.type] || row.type} · ${row.title}`)}>
                                下载
                              </Button>
                            </Space>
                          ),
                        },
                      ]}
                    />
                  )}
                </Card>
              ),
            },
            {
              key: 'memory',
              label: `🧠 记忆检索（${memories.length}）`,
              children: (
                <Card title="该任务产生的 Agent 记忆">
                  {memories.length === 0 ? (
                    <Empty description="该任务暂无记忆条目" />
                  ) : (
                    <List
                      dataSource={memories}
                      renderItem={(m) => (
                        <List.Item>
                          <List.Item.Meta
                            title={
                              <Space>
                                <Tag color="geekblue">{m.agent_id}</Tag>
                                <Tag>{m.experience_type}</Tag>
                                {m.scope && <Tag color="default">{m.scope}</Tag>}
                              </Space>
                            }
                            description={
                              <div>
                                <div>{m.content}</div>
                                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                                  {new Date(m.created_ts).toLocaleString()}
                                </Typography.Text>
                              </div>
                            }
                          />
                        </List.Item>
                      )}
                    />
                  )}
                </Card>
              ),
            },
          ]}
        />
      </Spin>

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
