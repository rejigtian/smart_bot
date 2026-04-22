import { useState, useEffect, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  fetchSuite, fetchCases, fetchDevices, fetchSettings, fetchTrends, startRun,
  addCase, updateCase, deleteCase, TestCase,
} from '../lib/api'

const PROVIDERS = ['openai', 'anthropic', 'google', 'zhipuai', 'groq', 'ollama']

// ── Inline editable case row ────────────────────────────────────────────────

function CaseRow({
  c, suiteId, index, total,
}: {
  c: TestCase; suiteId: string; index: number; total: number
}) {
  const qc = useQueryClient()
  const [editing, setEditing] = useState(false)
  const [path, setPath] = useState(c.path)
  const [expected, setExpected] = useState(c.expected)

  const saveMut = useMutation({
    mutationFn: () => updateCase(suiteId, c.id, { path, expected }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['cases', suiteId] }); setEditing(false) },
  })

  const delMut = useMutation({
    mutationFn: () => deleteCase(suiteId, c.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['cases', suiteId] }),
  })

  if (editing) {
    return (
      <div className={`px-4 py-3 bg-blue-50 ${index > 0 ? 'border-t' : ''}`}>
        <div className="text-xs text-gray-500 mb-1">路径 / Path</div>
        <input
          className="w-full border rounded px-2 py-1 text-xs mb-2 font-mono"
          value={path}
          onChange={e => setPath(e.target.value)}
        />
        <div className="text-xs text-gray-500 mb-1">预期结果 / Expected</div>
        <input
          className="w-full border rounded px-2 py-1 text-sm mb-3"
          value={expected}
          onChange={e => setExpected(e.target.value)}
        />
        <div className="flex gap-2">
          <button
            className="px-3 py-1 bg-blue-600 text-white text-xs rounded hover:bg-blue-700 disabled:opacity-50"
            disabled={saveMut.isPending}
            onClick={() => saveMut.mutate()}
          >
            {saveMut.isPending ? '保存中…' : '保存'}
          </button>
          <button
            className="px-3 py-1 border text-xs rounded hover:bg-gray-100"
            onClick={() => { setPath(c.path); setExpected(c.expected); setEditing(false) }}
          >
            取消
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className={`px-4 py-3 flex items-start gap-2 group ${index > 0 ? 'border-t' : ''}`}>
      <div className="flex-1 min-w-0">
        <div className="text-xs text-gray-400 truncate">{c.path}</div>
        <div className="text-sm font-medium mt-0.5">{c.expected}</div>
      </div>
      <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0 mt-0.5">
        <button
          className="px-2 py-0.5 text-xs border rounded hover:bg-gray-100"
          onClick={() => setEditing(true)}
        >
          编辑
        </button>
        <button
          className="px-2 py-0.5 text-xs border border-red-200 text-red-600 rounded hover:bg-red-50 disabled:opacity-50"
          disabled={delMut.isPending || total <= 1}
          onClick={() => { if (confirm('删除这条用例？')) delMut.mutate() }}
        >
          删除
        </button>
      </div>
    </div>
  )
}

// ── Add case row ─────────────────────────────────────────────────────────────

function AddCaseRow({ suiteId }: { suiteId: string }) {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [path, setPath] = useState('')
  const [expected, setExpected] = useState('')

  const addMut = useMutation({
    mutationFn: () => addCase(suiteId, { path, expected }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['cases', suiteId] })
      qc.invalidateQueries({ queryKey: ['suite', suiteId] })
      setPath(''); setExpected(''); setOpen(false)
    },
  })

  if (!open) {
    return (
      <button
        className="w-full text-left px-4 py-2.5 text-sm text-blue-600 hover:bg-blue-50 border-t"
        onClick={() => setOpen(true)}
      >
        + 添加用例
      </button>
    )
  }

  return (
    <div className="px-4 py-3 bg-green-50 border-t">
      <div className="text-xs text-gray-500 mb-1">路径 / Path</div>
      <input
        className="w-full border rounded px-2 py-1 text-xs mb-2 font-mono"
        placeholder="模块 > 子功能 > 场景"
        value={path}
        onChange={e => setPath(e.target.value)}
      />
      <div className="text-xs text-gray-500 mb-1">预期结果 / Expected</div>
      <input
        className="w-full border rounded px-2 py-1 text-sm mb-3"
        placeholder="预期看到什么结果"
        value={expected}
        onChange={e => setExpected(e.target.value)}
      />
      <div className="flex gap-2">
        <button
          className="px-3 py-1 bg-green-600 text-white text-xs rounded hover:bg-green-700 disabled:opacity-50"
          disabled={!path.trim() || addMut.isPending}
          onClick={() => addMut.mutate()}
        >
          {addMut.isPending ? '添加中…' : '添加'}
        </button>
        <button
          className="px-3 py-1 border text-xs rounded hover:bg-gray-100"
          onClick={() => { setPath(''); setExpected(''); setOpen(false) }}
        >
          取消
        </button>
      </div>
    </div>
  )
}

// ── Main page ────────────────────────────────────────────────────────────────

export default function SuiteDetail() {
  const { suiteId } = useParams<{ suiteId: string }>()
  const navigate = useNavigate()

  const [deviceId, setDeviceId] = useState('')
  const [provider, setProvider] = useState('openai')
  const [model, setModel] = useState('gpt-4o')
  const [maxSteps, setMaxSteps] = useState(20)
  const settingsInitialized = useRef(false)

  const DEFAULT_MODELS: Record<string, string> = {
    openai: 'gpt-4o',
    anthropic: 'claude-sonnet-4-6',
    google: 'gemini-1.5-pro',
    zhipuai: 'glm-4v',
    groq: 'llama-3.1-70b-versatile',
    ollama: 'llama3',
  }

  const { data: suite } = useQuery({ queryKey: ['suite', suiteId], queryFn: () => fetchSuite(suiteId!) })
  const { data: cases = [] } = useQuery({ queryKey: ['cases', suiteId], queryFn: () => fetchCases(suiteId!) })
  const { data: devices = [] } = useQuery({ queryKey: ['devices'], queryFn: fetchDevices, refetchInterval: 5000 })
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: fetchSettings })

  // Only apply saved defaults once on first load; don't overwrite user's manual changes
  useEffect(() => {
    if (settings && !settingsInitialized.current) {
      settingsInitialized.current = true
      if (settings.default_provider) setProvider(settings.default_provider)
      if (settings.default_model) setModel(settings.default_model)
    }
  }, [settings])

  const { data: trends = [] } = useQuery({
    queryKey: ['trends', suiteId],
    queryFn: () => fetchTrends(suiteId!),
    enabled: !!suiteId,
  })

  const onlineDevices = devices.filter(d => d.status === 'online')

  function handleProviderChange(p: string) {
    setProvider(p)
    if (settings && p === settings.default_provider && settings.default_model) {
      setModel(settings.default_model)
    } else {
      setModel(DEFAULT_MODELS[p] || '')
    }
  }

  const runMut = useMutation({
    mutationFn: () => startRun({ suite_id: suiteId!, device_id: deviceId, provider, model, max_steps: maxSteps }),
    onSuccess: run => navigate(`/runs/${run.id}`),
    onError: (e: unknown) => {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || String(e)
      alert(`启动失败: ${msg}`)
    },
  })

  return (
    <div>
      <button className="text-sm text-blue-600 hover:underline mb-4 block" onClick={() => navigate('/suites')}>
        ← 返回套件列表
      </button>

      <div className="flex items-start gap-8">
        {/* Left: test case list */}
        <div className="flex-1 min-w-0">
          <h1 className="text-2xl font-bold mb-1">{suite?.name}</h1>
          <p className="text-sm text-gray-500 mb-4">{cases.length} 条用例 · 悬停行可编辑</p>

          <div className="bg-white border rounded-lg overflow-hidden shadow-sm">
            {cases.map((c, i) => (
              <CaseRow key={c.id} c={c} suiteId={suiteId!} index={i} total={cases.length} />
            ))}
            <AddCaseRow suiteId={suiteId!} />
          </div>

          {/* Pass rate trend chart */}
          {trends.length >= 2 && (
            <div className="mt-6">
              <h2 className="text-sm font-semibold text-gray-600 uppercase tracking-wide mb-2">Pass Rate Trend</h2>
              <div className="bg-white border rounded-lg p-4 shadow-sm">
                <svg viewBox={`0 0 ${Math.max(trends.length * 60, 300)} 120`} className="w-full h-32">
                  {/* Grid lines */}
                  {[0, 25, 50, 75, 100].map(v => (
                    <g key={v}>
                      <line x1="30" y1={100 - v} x2={trends.length * 60} y2={100 - v} stroke="#e5e7eb" strokeWidth="0.5" />
                      <text x="0" y={104 - v} fontSize="8" fill="#9ca3af">{v}%</text>
                    </g>
                  ))}
                  {/* Line + dots */}
                  <polyline
                    fill="none" stroke="#3b82f6" strokeWidth="2"
                    points={trends.map((t, i) => `${30 + i * 55},${100 - t.pass_rate}`).join(' ')}
                  />
                  {trends.map((t, i) => (
                    <g key={t.run_id}>
                      <circle
                        cx={30 + i * 55} cy={100 - t.pass_rate} r="4"
                        fill={t.pass_rate === 100 ? '#22c55e' : t.pass_rate >= 70 ? '#3b82f6' : '#ef4444'}
                      />
                      <text
                        x={30 + i * 55} y={95 - t.pass_rate}
                        fontSize="7" fill="#6b7280" textAnchor="middle"
                      >
                        {t.pass_rate.toFixed(0)}%
                      </text>
                      <text
                        x={30 + i * 55} y="115"
                        fontSize="6" fill="#9ca3af" textAnchor="middle"
                      >
                        {new Date(t.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}
                      </text>
                    </g>
                  ))}
                </svg>
              </div>
            </div>
          )}
        </div>

        {/* Right: run config */}
        <div className="w-72 flex-shrink-0">
          <div className="bg-white border rounded-lg p-5 shadow-sm">
            <h2 className="font-semibold mb-4">开始运行</h2>

            <label className="block text-sm font-medium mb-1">设备</label>
            {onlineDevices.length === 0 ? (
              <p className="text-sm text-red-500 mb-3">无在线设备，请先连接设备。</p>
            ) : (
              <select
                className="w-full border rounded px-2 py-1.5 text-sm mb-3"
                value={deviceId}
                onChange={e => setDeviceId(e.target.value)}
              >
                <option value="">— 选择设备 —</option>
                {onlineDevices.map(d => (
                  <option key={d.id} value={d.id}>{d.name}</option>
                ))}
              </select>
            )}

            <label className="block text-sm font-medium mb-1">Provider</label>
            <select
              className="w-full border rounded px-2 py-1.5 text-sm mb-3"
              value={provider}
              onChange={e => handleProviderChange(e.target.value)}
            >
              {PROVIDERS.map(p => <option key={p}>{p}</option>)}
            </select>

            <label className="block text-sm font-medium mb-1">Model</label>
            <input
              className="w-full border rounded px-2 py-1.5 text-sm mb-3 font-mono"
              value={model}
              onChange={e => setModel(e.target.value)}
            />

            <label className="block text-sm font-medium mb-1">每条用例最大步数</label>
            <input
              type="number"
              className="w-full border rounded px-2 py-1.5 text-sm mb-5"
              value={maxSteps}
              onChange={e => setMaxSteps(parseInt(e.target.value) || 20)}
              min={5}
              max={100}
            />

            <button
              className="w-full bg-blue-600 text-white py-2 rounded font-medium hover:bg-blue-700 disabled:opacity-50"
              disabled={!deviceId || runMut.isPending}
              onClick={() => runMut.mutate()}
            >
              {runMut.isPending ? '启动中…' : '▶ 开始运行'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
