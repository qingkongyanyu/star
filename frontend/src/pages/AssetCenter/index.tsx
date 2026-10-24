/** 资产仓库：资产列表 + 分页 + 下载 + 预览。 */

import { useCallback, useEffect, useState } from 'react'
import { App, Button, Card, Space, Table, Tag } from 'antd'
import { DownloadOutlined, EyeOutlined } from '@ant-design/icons'

import { downloadAssetFile, listAssets } from '../../api/asset'
import AssetPreviewModal from '../../components/AssetPreviewModal'
import { TYPE_LABEL } from '../../config/labels'
import type { AssetOut } from '../../types/api'

export default function AssetCenter() {
  const { message } = App.useApp()
  const [items, setItems] = useState<AssetOut[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)

  // 依赖显式参数 + useCallback：避免 effect 依赖每次渲染都变的函数（也避免翻页被重置）
  const load = useCallback(
    async (p: number) => {
      setLoading(true)
      try {
        const resp = await listAssets({ page: p, page_size: 20 })
        setItems(resp.items)
        setTotal(resp.total)
        setPage(p)
      } catch (e) {
        message.error((e as Error).message)
      } finally {
        setLoading(false)
      }
    },
    [message],
  )

  useEffect(() => {
    load(1).catch(() => undefined)
  }, [load])

  const download = async (row: AssetOut) => {
    try {
      await downloadAssetFile(row.asset_id, `${TYPE_LABEL[row.type] || row.type} · ${row.title}`, row.latest_version ?? 1)
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const [previewAssetId, setPreviewAssetId] = useState<string | null>(null)
  const [previewTitle, setPreviewTitle] = useState('')

  const preview = (row: AssetOut) => {
    setPreviewAssetId(row.asset_id)
    setPreviewTitle(`${TYPE_LABEL[row.type] || row.type} · ${row.title || row.asset_id}`)
  }

  const columns = [
    { title: '资产 ID', dataIndex: 'asset_id', key: 'asset_id' },
    {
      title: '类型',
      dataIndex: 'type',
      key: 'type',
      render: (t: string) => <Tag color="blue">{TYPE_LABEL[t] || t}</Tag>,
    },
    { title: '标题', dataIndex: 'title', key: 'title' },
    { title: '产出 Agent', dataIndex: 'producer_agent', key: 'producer_agent' },
    { title: '版本', dataIndex: 'latest_version', key: 'latest_version' },
    { title: '状态', dataIndex: 'status', key: 'status' },
    {
      title: '操作',
      key: 'action',
      width: 150,
      render: (_: unknown, row: AssetOut) => (
        <Space>
          <Button size="small" icon={<EyeOutlined />} disabled={!row.latest_version} onClick={() => preview(row)}>
            预览
          </Button>
          <Button size="small" icon={<DownloadOutlined />} disabled={!row.latest_version} onClick={() => download(row)}>
            下载
          </Button>
        </Space>
      ),
    },
  ]

  return (
    <Card title="资产仓库">
      <Table
        rowKey="asset_id"
        loading={loading}
        dataSource={items}
        columns={columns}
        pagination={{ current: page, total, pageSize: 20, onChange: load }}
      />
      <AssetPreviewModal
        assetId={previewAssetId ?? ''}
        title={previewTitle}
        open={previewAssetId !== null}
        onClose={() => setPreviewAssetId(null)}
        onError={(msg) => message.error(msg)}
      />
    </Card>
  )
}
