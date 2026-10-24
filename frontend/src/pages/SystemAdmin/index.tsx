/** 系统管理：用户 / ACL / 知识库 / 审计 四个 Tab（docs/03-前端/02）。 */

import { useCallback, useEffect, useState } from 'react'
import { App, Button, Card, Form, Input, Modal, Popconfirm, Select, Space, Switch, Table, Tabs, Tag, Typography } from 'antd'

import {
  createAcl,
  createKb,
  createUser,
  deleteAcl,
  deleteKb,
  getAcl,
  listAudit,
  listKb,
  listUsers,
  updateAcl,
  updateKb,
  updateUser,
} from '../../api/admin'
import type { ACLRuleOut, AuditEntryOut, KBIndicatorOut, UserAdminOut } from '../../types/api'

const ROLE_OPTIONS = [
  { value: 'admin', label: '管理员' },
  { value: 'analyst', label: '分析师' },
  { value: 'readonly', label: '只读' },
]

function UsersTab() {
  const { message } = App.useApp()
  const [users, setUsers] = useState<UserAdminOut[]>([])
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm()

  const load = useCallback(async () => {
    const resp = await listUsers(1, 100)
    setUsers(resp.items)
  }, [])
  useEffect(() => {
    load().catch(() => undefined)
  }, [load])

  const onCreate = async () => {
    const v = await form.validateFields()
    try {
      await createUser(v)
      message.success('已创建用户')
      setOpen(false)
      form.resetFields()
      load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const onPatch = async (user: UserAdminOut, patch: { role?: string; is_active?: boolean }) => {
    try {
      await updateUser(user.id, patch)
      load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const columns = [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 60 },
    { title: '用户名', dataIndex: 'username', key: 'username' },
    {
      title: '角色',
      dataIndex: 'role',
      key: 'role',
      width: 130,
      render: (r: string, u: UserAdminOut) => (
        <Select value={r} options={ROLE_OPTIONS} size="small" onChange={(v) => onPatch(u, { role: v })} />
      ),
    },
    { title: '显示名', dataIndex: 'display_name', key: 'display_name' },
    {
      title: '启用',
      dataIndex: 'is_active',
      key: 'is_active',
      width: 80,
      render: (a: boolean, u: UserAdminOut) => (
        <Switch checked={a} size="small" onChange={(v) => onPatch(u, { is_active: v })} />
      ),
    },
    { title: '创建时间', dataIndex: 'created_at', key: 'created_at' },
  ]

  return (
    <Space orientation="vertical" style={{ width: '100%' }}>
      <Button type="primary" onClick={() => setOpen(true)}>
        新建用户
      </Button>
      <Table rowKey="id" dataSource={users} columns={columns} size="small" pagination={{ pageSize: 10 }} />
      <Modal title="新建用户" open={open} onOk={onCreate} onCancel={() => setOpen(false)}>
        <Form form={form} layout="vertical">
          <Form.Item name="username" label="用户名" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true }]}>
            <Input.Password />
          </Form.Item>
          <Form.Item name="role" label="角色" initialValue="analyst">
            <Select options={ROLE_OPTIONS} />
          </Form.Item>
          <Form.Item name="display_name" label="显示名">
            <Input />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  )
}

function AclTab() {
  const { message } = App.useApp()
  const [rules, setRules] = useState<ACLRuleOut[]>([])
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<ACLRuleOut | null>(null)
  const [form] = Form.useForm()

  const load = useCallback(async () => {
    const resp = await getAcl()
    setRules(resp)
  }, [])
  useEffect(() => {
    load().catch(() => undefined)
  }, [load])

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    setOpen(true)
  }

  const openEdit = (row: ACLRuleOut) => {
    setEditing(row)
    form.setFieldsValue({ role_or_user: row.role_or_user, table: row.table, action: row.action, allow: row.allow })
    setOpen(true)
  }

  const onSubmit = async () => {
    const v = await form.validateFields()
    try {
      if (editing) {
        await updateAcl(editing.id, v)
        message.success('已更新规则')
      } else {
        await createAcl(v)
        message.success('已新增规则')
      }
      setOpen(false)
      form.resetFields()
      load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  return (
    <Space orientation="vertical" style={{ width: '100%' }}>
      <Button type="primary" onClick={openCreate}>
        新增规则
      </Button>
      <Table rowKey="id" dataSource={rules} size="small" pagination={false}
        columns={[
          { title: '角色/用户', dataIndex: 'role_or_user', key: 'role_or_user', render: (v: string) => <Tag>{v}</Tag> },
          { title: '表', dataIndex: 'table', key: 'table', render: (v: string) => <Tag color="blue">{v}</Tag> },
          { title: '操作', dataIndex: 'action', key: 'action', width: 100 },
          { title: '允许', dataIndex: 'allow', key: 'allow', width: 90, render: (a: boolean) => (a ? <Tag color="success">允许</Tag> : <Tag color="error">拒绝</Tag>) },
          {
            title: '操作',
            key: 'ops',
            width: 120,
            render: (_: unknown, row: ACLRuleOut) => (
              <Space>
                <Button size="small" onClick={() => openEdit(row)}>
                  编辑
                </Button>
                <Popconfirm
                  title="确认删除该规则？"
                  onConfirm={async () => {
                    try {
                      await deleteAcl(row.id)
                      message.success('已删除')
                      load()
                    } catch (e) {
                      message.error((e as Error).message)
                    }
                  }}
                >
                  <Button size="small" danger>
                    删除
                  </Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        规则匹配：角色或用户 × 表（支持 * 通配）× 操作。拒绝优先于允许；无规则默认允许（只读连接）。
      </Typography.Text>
      <Modal title={editing ? '编辑 ACL 规则' : '新增 ACL 规则'} open={open} onOk={onSubmit} onCancel={() => setOpen(false)}>
        <Form form={form} layout="vertical">
          <Form.Item name="role_or_user" label="角色或用户" rules={[{ required: true }]} extra="如 analyst / data_01 / *（全部）">
            <Input placeholder="analyst" />
          </Form.Item>
          <Form.Item name="table" label="表" rules={[{ required: true }]} extra="如 orders / *（全部表）">
            <Input placeholder="orders" />
          </Form.Item>
          <Form.Item name="action" label="操作" rules={[{ required: true }]} initialValue="select">
            <Select options={['select', 'insert', 'update', 'delete'].map((a) => ({ value: a, label: a }))} />
          </Form.Item>
          <Form.Item name="allow" label="允许" valuePropName="checked" initialValue={true}>
            <Switch checkedChildren="允许" unCheckedChildren="拒绝" />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  )
}

function KbTab() {
  const { message } = App.useApp()
  const [items, setItems] = useState<KBIndicatorOut[]>([])
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<KBIndicatorOut | null>(null)
  const [form] = Form.useForm()

  const load = useCallback(async () => {
    const resp = await listKb(undefined, 1, 100)
    setItems(resp.items)
  }, [])
  useEffect(() => {
    load().catch(() => undefined)
  }, [load])

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    setOpen(true)
  }

  const openEdit = (row: KBIndicatorOut) => {
    setEditing(row)
    form.setFieldsValue({ ...row, tags: (row.tags ?? []).join(',') })
    setOpen(true)
  }

  const parseTags = (v: string | undefined) => (v ? v.split(/[,，]/).map((t) => t.trim()).filter(Boolean) : [])

  const onSubmit = async () => {
    const v = await form.validateFields()
    try {
      if (editing) {
        const { indicator_key: _key, ...rest } = v
        await updateKb(editing.id, { ...rest, tags: parseTags(v.tags) })
        message.success('已更新指标口径')
      } else {
        await createKb({ ...v, tags: parseTags(v.tags) })
        message.success('已新增指标')
      }
      setOpen(false)
      form.resetFields()
      load()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  return (
    <Space orientation="vertical" style={{ width: '100%' }}>
      <Button type="primary" onClick={openCreate}>
        新增指标
      </Button>
      <Table rowKey="id" dataSource={items} size="small" pagination={{ pageSize: 10 }}
        columns={[
          { title: '指标编码', dataIndex: 'indicator_key', key: 'indicator_key' },
          { title: '指标名称', dataIndex: 'metric_name', key: 'metric_name' },
          { title: '分类', dataIndex: 'category', key: 'category' },
          { title: '口径', dataIndex: 'formula', key: 'formula', ellipsis: true },
          { title: '描述', dataIndex: 'description', key: 'description', ellipsis: true },
          {
            title: '操作',
            key: 'action',
            width: 120,
            render: (_: unknown, row: KBIndicatorOut) => (
              <Space>
                <Button size="small" onClick={() => openEdit(row)}>
                  编辑
                </Button>
                <Popconfirm
                  title="确认删除该指标口径？"
                  onConfirm={async () => {
                    try {
                      await deleteKb(row.id)
                      message.success('已删除')
                      load()
                    } catch (e) {
                      message.error((e as Error).message)
                    }
                  }}
                >
                  <Button size="small" danger>
                    删除
                  </Button>
                </Popconfirm>
              </Space>
            ),
          },
        ]}
      />
      <Modal title={editing ? '编辑指标口径' : '新增指标'} open={open} onOk={onSubmit} onCancel={() => setOpen(false)}>
        <Form form={form} layout="vertical">
          {!editing && (
            <Form.Item name="indicator_key" label="指标编码" rules={[{ required: true }]} extra="唯一编码，如 sales_amount">
              <Input disabled={!!editing} />
            </Form.Item>
          )}
          <Form.Item name="metric_name" label="指标名称" rules={[{ required: true }]}>
            <Input placeholder="如 销售额 / 净销售额" />
          </Form.Item>
          <Form.Item name="category" label="分类">
            <Input placeholder="如 ecommerce / finance / marketing" />
          </Form.Item>
          <Form.Item name="formula" label="口径/公式" extra="Agent 定义指标时优先采用此口径">
            <Input.TextArea rows={2} placeholder="如 SUM(`amount`)" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="tags" label="标签（逗号分隔）">
            <Input placeholder="如 销售额,销售,amount" />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  )
}

function AuditTab() {
  const { message } = App.useApp()
  const [entries, setEntries] = useState<AuditEntryOut[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [action, setAction] = useState('')

  const load = useCallback(
    async (p: number, act: string) => {
      try {
        const resp = await listAudit({ action: act || undefined, page: p, page_size: 20 })
        setEntries(resp.items)
        setTotal(resp.total)
        setPage(p)
      } catch (e) {
        message.error((e as Error).message)
      }
    },
    [message],
  )
  useEffect(() => {
    load(1, '').catch(() => undefined)
  }, [load])

  return (
    <Space orientation="vertical" style={{ width: '100%' }}>
      <Space>
        <Input placeholder="按 action 筛选（如 task.created）" value={action} onChange={(e) => setAction(e.target.value)} allowClear />
        <Button onClick={() => load(1, action)}>查询</Button>
      </Space>
      <Table rowKey="id" dataSource={entries} size="small"
        pagination={{ current: page, total, pageSize: 20, onChange: (p) => load(p, action) }}
        columns={[
          { title: '时间', dataIndex: 'ts', key: 'ts', width: 160 },
          { title: '操作者', dataIndex: 'actor', key: 'actor', width: 120 },
          { title: '动作', dataIndex: 'action', key: 'action', width: 160, render: (a: string) => <Tag>{a}</Tag> },
          { title: '节点', dataIndex: 'node_id', key: 'node_id', width: 140 },
          { title: '资产', dataIndex: 'asset_id', key: 'asset_id', width: 120 },
          { title: '摘要', dataIndex: 'detail', key: 'detail', ellipsis: true, render: (d: unknown) => (d ? JSON.stringify(d) : '-') },
          { title: '校验和', dataIndex: 'checksum', key: 'checksum', ellipsis: true, width: 120 },
        ]}
      />
    </Space>
  )
}

export default function SystemAdmin() {
  return (
    <Card>
      <Tabs
        items={[
          { key: 'users', label: '用户角色', children: <UsersTab /> },
          { key: 'acl', label: 'ACL', children: <AclTab /> },
          { key: 'kb', label: '知识库', children: <KbTab /> },
          { key: 'audit', label: '审计日志', children: <AuditTab /> },
        ]}
      />
    </Card>
  )
}
