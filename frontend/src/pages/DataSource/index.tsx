/** 数据源接入管理页：新增 / 测试 / 结构预览 / 删除 / CSV 上传。 */

import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import {
  App,
  Button,
  Card,
  Descriptions,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  Space,
  Table,
  Tag,
  Upload,
} from 'antd'
import {
  ApiOutlined,
  CloudOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  PlusOutlined,
  TableOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons'
import type { ColumnsType } from 'antd/es/table'

import {
  createDataSource,
  deleteDataSource,
  getDataSourceSchema,
  getDataSourceTables,
  listDataSources,
  renameDataSource,
  testDataSource,
  uploadCsv,
} from '../../api/dataSource'
import { T } from '../../config/theme'
import type { DataSourceItem, DataSourceTestResult } from '../../types/api'

const TYPE_META: Record<string, { label: string; icon: ReactNode; color: string }> = {
  mysql: { label: 'MySQL 关系库', icon: <DatabaseOutlined />, color: 'blue' },
  postgres: { label: 'PostgreSQL', icon: <DatabaseOutlined />, color: 'cyan' },
  olap: { label: 'OLAP 数仓', icon: <CloudOutlined />, color: 'purple' },
  file: { label: '文件型 CSV', icon: <FileTextOutlined />, color: 'green' },
  stream: { label: '实时流 Kafka', icon: <ThunderboltOutlined />, color: 'orange' },
  api: { label: '外部接口 API', icon: <ApiOutlined />, color: 'magenta' },
}

const TYPE_OPTIONS = Object.entries(TYPE_META).map(([value, m]) => ({
  value,
  label: `${m.label}`,
}))

interface CreateForm {
  name: string
  type: string
  host?: string
  port?: number
  database?: string
  username?: string
  password?: string
  engine_url?: string
  vendor?: string
  bootstrap_servers?: string
  topic?: string
  format?: string
  url?: string
  method?: string
  auth_type?: string
  api_key?: string
}

export default function DataSource() {
  const { message } = App.useApp()
  const [list, setList] = useState<DataSourceItem[]>([])
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [uploadFile, setUploadFile] = useState<File | null>(null)
  const [testResult, setTestResult] = useState<DataSourceTestResult | null>(null)
  const [form] = Form.useForm<CreateForm>()
  const dsType = Form.useWatch('type', form)

  // 结构预览
  const [schemaOpen, setSchemaOpen] = useState(false)
  const [schemaDs, setSchemaDs] = useState<DataSourceItem | null>(null)
  const [tables, setTables] = useState<string[]>([])
  const [curTable, setCurTable] = useState<string>()
  const [schema, setSchema] = useState<{ columns?: { name: string; type: string }[]; row_count?: number | null } | null>(null)

  // 重命名
  const [renameOpen, setRenameOpen] = useState(false)
  const [renameTarget, setRenameTarget] = useState<DataSourceItem | null>(null)
  const [renameName, setRenameName] = useState('')

  const refresh = async () => {
    setLoading(true)
    try {
      setList(await listDataSources())
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const configFromForm = (v: CreateForm): Record<string, unknown> => {
    switch (v.type) {
      case 'mysql':
      case 'postgres':
        return {
          host: v.host,
          port: Number(v.port) || (v.type === 'postgres' ? 5432 : 3306),
          database: v.database,
          username: v.username,
          password: v.password,
        }
      case 'olap':
        return { engine_url: v.engine_url, vendor: v.vendor }
      case 'stream':
        return { bootstrap_servers: v.bootstrap_servers, topic: v.topic, format: v.format }
      case 'api':
        return {
          url: v.url,
          method: v.method,
          auth_type: v.auth_type,
          api_key: v.api_key,
        }
      default:
        return {}
    }
  }

  const onFinish = async (v: CreateForm) => {
    if (v.type === 'file') {
      if (!uploadFile) {
        message.warning('请先选择 CSV 文件')
        return
      }
      setSubmitting(true)
      try {
        const res = await uploadCsv(v.name, uploadFile)
        const summary = `已注册文件数据源「${res.data_source.name}」：${res.row_count} 行、${res.columns.length} 列`
        setTestResult({
          ok: true,
          message: summary,
          tables: [],
          columns: res.columns,
          schema_preview: { columns: res.columns, sample_rows: res.sample_rows },
        })
        message.success(summary)
        setOpen(false)
        form.resetFields()
        setUploadFile(null)
        await refresh()
      } catch (e) {
        message.error((e as Error).message)
      } finally {
        setSubmitting(false)
      }
      return
    }

    setSubmitting(true)
    try {
      const ds = await createDataSource({ name: v.name, type: v.type, config: configFromForm(v) })
      const res = await testDataSource(ds.id)
      setTestResult(res)
      message[res.ok ? 'success' : 'error'](res.message)
      setOpen(false)
      form.resetFields()
      await refresh()
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSubmitting(false)
    }
  }

  const onTest = async (id: number) => {
    try {
      const res = await testDataSource(id)
      message[res.ok ? 'success' : 'error'](res.message)
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const openSchema = async (ds: DataSourceItem) => {
    setSchemaDs(ds)
    setSchemaOpen(true)
    setSchema(null)
    setTables([])
    setCurTable(undefined)
    try {
      if (['mysql', 'postgres', 'olap'].includes(ds.type)) {
        const t = await getDataSourceTables(ds.id)
        setTables(t)
        if (t.length) await loadSchema(ds.id, t[0])
      } else {
        await loadSchema(ds.id)
      }
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const loadSchema = async (id: number, table?: string) => {
    const s = (await getDataSourceSchema(id, table)) as { columns?: { name: string; type: string }[]; row_count?: number | null }
    setCurTable(table)
    setSchema(s)
  }

  const columns: ColumnsType<DataSourceItem> = useMemo(
    () => [
      { title: '名称', dataIndex: 'name', render: (v) => <b>{v}</b> },
      {
        title: '类型',
        dataIndex: 'type',
        width: 150,
        render: (t: string) => (
          <Tag color={TYPE_META[t]?.color} icon={TYPE_META[t]?.icon}>
            {TYPE_META[t]?.label ?? t}
          </Tag>
        ),
      },
      {
        title: '状态',
        dataIndex: 'status',
        width: 110,
        render: (s: string) =>
          s === 'connected' ? <Tag color="success">已连接</Tag> : s === 'error' ? <Tag color="error">连接失败</Tag> : <Tag>未测试</Tag>,
      },
      { title: '创建人', dataIndex: 'created_by', width: 100 },
      { title: '创建时间', dataIndex: 'created_at', width: 170, render: (v: string) => new Date(v).toLocaleString() },
      {
        title: '操作',
        key: 'action',
        width: 250,
        render: (_, row) => (
          <Space>
            <Button size="small" onClick={() => onTest(row.id)}>
              测试
            </Button>
            <Button size="small" icon={<TableOutlined />} onClick={() => openSchema(row)}>
              结构
            </Button>
            <Button
              size="small"
              onClick={() => {
                setRenameTarget(row)
                setRenameName(row.name)
                setRenameOpen(true)
              }}
            >
              重命名
            </Button>
            <Popconfirm title="确认删除该数据源？" onConfirm={async () => { try { await deleteDataSource(row.id); message.success('已删除'); await refresh() } catch (e) { message.error((e as Error).message) } }}>
              <Button size="small" danger>
                删除
              </Button>
            </Popconfirm>
          </Space>
        ),
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  )

  const renderConfigForm = () => {
    switch (dsType) {
      case 'mysql':
      case 'postgres':
        return (
          <>
            <Form.Item name="host" label="主机" rules={[{ required: true }]} initialValue="127.0.0.1">
              <Input placeholder="127.0.0.1" />
            </Form.Item>
            <Form.Item name="port" label="端口" initialValue={dsType === 'postgres' ? 5432 : 3306}>
              <Input type="number" />
            </Form.Item>
            <Form.Item name="database" label="数据库名" rules={[{ required: true }]}>
              <Input placeholder="如 analysis_biz" />
            </Form.Item>
            <Form.Item name="username" label="用户名" rules={[{ required: true }]}>
              <Input placeholder="账号" />
            </Form.Item>
            <Form.Item name="password" label="密码" rules={[{ required: true }]}>
              <Input.Password placeholder="密码" />
            </Form.Item>
          </>
        )
      case 'olap':
        return (
          <>
            <Form.Item name="engine_url" label="SQLAlchemy 连接串" rules={[{ required: true }]} extra="如 mysql+pymysql://user:pwd@host:port/db">
              <Input placeholder="engine_url" />
            </Form.Item>
            <Form.Item name="vendor" label="厂商" initialValue="mysql">
              <Select
                options={[
                  { value: 'mysql', label: 'MySQL / Doris / StarRocks' },
                  { value: 'postgres', label: 'PostgreSQL / Greenplum' },
                  { value: 'clickhouse', label: 'ClickHouse（需装驱动）' },
                ]}
              />
            </Form.Item>
          </>
        )
      case 'stream':
        return (
          <>
            <Form.Item name="bootstrap_servers" label="Bootstrap Servers" rules={[{ required: true }]} initialValue="127.0.0.1:9092">
              <Input />
            </Form.Item>
            <Form.Item name="topic" label="Topic">
              <Input />
            </Form.Item>
            <Form.Item name="format" label="消息格式" initialValue="json">
              <Select options={[{ value: 'json', label: 'JSON' }, { value: 'csv', label: 'CSV' }]} />
            </Form.Item>
          </>
        )
      case 'api':
        return (
          <>
            <Form.Item name="url" label="接口 URL" rules={[{ required: true }]}>
              <Input placeholder="https://api.example.com/v1/sales" />
            </Form.Item>
            <Form.Item name="method" label="请求方法" initialValue="GET">
              <Select options={[{ value: 'GET', label: 'GET' }, { value: 'POST', label: 'POST' }]} />
            </Form.Item>
            <Form.Item name="auth_type" label="鉴权方式" initialValue="none">
              <Select
                options={[
                  { value: 'none', label: '无' },
                  { value: 'bearer', label: 'Bearer Token' },
                  { value: 'basic', label: 'Basic Auth' },
                ]}
              />
            </Form.Item>
            <Form.Item name="api_key" label="API Key">
              <Input.Password placeholder="token / basic 串" />
            </Form.Item>
          </>
        )
      case 'file':
        return (
          <Form.Item label="CSV 文件" required>
            <Upload.Dragger
              accept=".csv,text/csv"
              maxCount={1}
              beforeUpload={(f) => {
                setUploadFile(f)
                return false
              }}
              onRemove={() => setUploadFile(null)}
            >
              <p className="ant-upload-drag-icon">
                <FileTextOutlined />
              </p>
              <p className="ant-upload-text">点击或拖拽 CSV 文件到此处</p>
              <p className="ant-upload-hint">上传后自动解析列结构并注册为文件型数据源</p>
            </Upload.Dragger>
          </Form.Item>
        )
      default:
        return null
    }
  }

  return (
    <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
      <Card
        title="数据源接入"
        extra={
          <Button type="primary" icon={<PlusOutlined />} onClick={() => { setOpen(true); setTestResult(null); setUploadFile(null) }}>
            新增数据源
          </Button>
        }
      >
        <Table rowKey="id" loading={loading} columns={columns} dataSource={list} pagination={false} />
      </Card>

      <Modal
        title="新增数据源"
        open={open}
        onCancel={() => setOpen(false)}
        onOk={() => form.submit()}
        okText="接入"
        confirmLoading={submitting}
        width={560}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={onFinish} initialValues={{ type: 'mysql', port: 3306 }}>
          <Form.Item name="name" label="数据源名称" rules={[{ required: true, message: '请输入名称' }]}>
            <Input placeholder="如：本机 MySQL 业务库" />
          </Form.Item>
          <Form.Item name="type" label="数据源类型" rules={[{ required: true }]}>
            <Select options={TYPE_OPTIONS} />
          </Form.Item>
          {renderConfigForm()}
          {testResult && (
            <Descriptions
              size="small"
              column={1}
              bordered
              style={{ marginTop: 8 }}
              items={[
                { key: 'ok', label: '连接结果', children: <Tag color={testResult.ok ? 'success' : 'error'}>{testResult.ok ? '成功' : '失败'}</Tag> },
                { key: 'msg', label: '信息', children: testResult.message },
                ...(testResult.tables.length
                  ? [{ key: 'tables', label: '表清单', children: testResult.tables.join('、') }]
                  : []),
              ]}
            />
          )}
        </Form>
      </Modal>

      <Modal
        title={`结构预览 · ${schemaDs?.name ?? ''}`}
        open={schemaOpen}
        onCancel={() => setSchemaOpen(false)}
        footer={null}
        width={640}
      >
        {tables.length > 0 && (
          <div style={{ marginBottom: 12 }}>
            <Space>
              <span>选择表：</span>
              <Select
                style={{ width: 280 }}
                value={curTable}
                options={tables.map((t) => ({ value: t, label: t }))}
                onChange={(t) => loadSchema(schemaDs!.id, t)}
              />
            </Space>
          </div>
        )}
        {schema ? (
          <>
            <div style={{ marginBottom: 8, color: T.textFaint, fontSize: 12 }}>
              表：{schemaDs?.type === 'file' ? (schemaDs.config as { file_name?: string }).file_name : curTable} · 行数：{schema.row_count ?? '—'}
            </div>
            <Table
              size="small"
              rowKey="name"
              pagination={false}
              dataSource={schema.columns ?? []}
              columns={[
                { title: '字段', dataIndex: 'name' },
                { title: '类型', dataIndex: 'type' },
              ]}
            />
          </>
        ) : (
          <div style={{ color: T.textFaint, textAlign: 'center', padding: 24 }}>加载结构中…</div>
        )}
      </Modal>

      <Modal
        title="重命名数据源"
        open={renameOpen}
        onCancel={() => setRenameOpen(false)}
        onOk={async () => {
          if (!renameTarget || !renameName.trim()) return
          try {
            await renameDataSource(renameTarget.id, renameName.trim())
            message.success('已重命名')
            setRenameOpen(false)
            await refresh()
          } catch (e) {
            message.error((e as Error).message)
          }
        }}
        okText="保存"
        width={400}
      >
        <Input value={renameName} onChange={(e) => setRenameName(e.target.value)} placeholder="支持中文与英文，如 月度销售CSV" maxLength={100} />
      </Modal>
    </Space>
  )
}
