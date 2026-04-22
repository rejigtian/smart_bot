import { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery, useMutation } from '@tanstack/react-query'
import { fetchDevices, fetchSettings, quickRun } from '../lib/api'

const PROVIDERS = ['openai', 'anthropic', 'google', 'zhipuai', 'groq', 'ollama']

const DEFAULT_MODELS: Record<string, string> = {
  openai: 'gpt-4o',
  anthropic: 'claude-sonnet-4-6',
  google: 'gemini-1.5-pro',
  zhipuai: 'glm-4v',
  groq: 'llama-3.1-70b-versatile',
  ollama: 'llama3',
}

export default function QuickRun() {
  const navigate = useNavigate()

  const [goal, setGoal] = useState('')
  const [expected, setExpected] = useState('')
  const [deviceId, setDeviceId] = useState('')
  const [provider, setProvider] = useState('openai')
  const [model, setModel] = useState('gpt-4o')
  const [maxSteps, setMaxSteps] = useState(20)
  const settingsInitialized = useRef(false)

  const { data: devices = [] } = useQuery({ queryKey: ['devices'], queryFn: fetchDevices, refetchInterval: 5000 })
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: fetchSettings })

  const onlineDevices = devices.filter(d => d.status === 'online')

  // Apply saved defaults only once on first load
  useEffect(() => {
    if (settings && !settingsInitialized.current) {
      settingsInitialized.current = true
      if (settings.default_provider) setProvider(settings.default_provider)
      if (settings.default_model) setModel(settings.default_model)
    }
  }, [settings])

  function handleProviderChange(p: string) {
    setProvider(p)
    // Use saved default only if this is the saved default provider; otherwise use per-provider default
    if (settings && p === settings.default_provider && settings.default_model) {
      setModel(settings.default_model)
    } else {
      setModel(DEFAULT_MODELS[p] || '')
    }
  }

  const runMut = useMutation({
    mutationFn: () => quickRun({
      goal,
      expected: expected || '任务完成',
      device_id: deviceId,
      provider,
      model,
      max_steps: maxSteps,
    }),
    onSuccess: run => navigate(`/runs/${run.id}`),
    onError: (e: unknown) => {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || String(e)
      alert(`启动失败: ${msg}`)
    },
  })

  const canSubmit = goal.trim().length > 0 && deviceId.length > 0 && !runMut.isPending

  return (
    <div className="max-w-2xl">
      <h1 className="text-2xl font-bold mb-6">快速任务</h1>

      <div className="bg-white border rounded-lg p-6 shadow-sm space-y-4">
        <div>
          <label className="block text-sm font-medium mb-1">
            任务描述 <span className="text-red-500">*</span>
          </label>
          <textarea
            rows={4}
            className="w-full border rounded px-3 py-2 text-sm resize-none focus:outline-none focus:ring-2 focus:ring-blue-400"
            placeholder="描述你想让 Agent 完成的任务，例如：打开设置页面，检查 Wi-Fi 是否已开启"
            value={goal}
            onChange={e => setGoal(e.target.value)}
          />
        </div>

        <div>
          <label className="block text-sm font-medium mb-1">
            预期结果 <span className="text-gray-400 font-normal">（可选，不填则默认"任务完成"）</span>
          </label>
          <input
            type="text"
            className="w-full border rounded px-3 py-1.5 text-sm"
            placeholder="例如：Wi-Fi 开关显示为开启状态"
            value={expected}
            onChange={e => setExpected(e.target.value)}
          />
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium mb-1">设备</label>
            {onlineDevices.length === 0 ? (
              <p className="text-sm text-red-500">无在线设备</p>
            ) : (
              <select
                className="w-full border rounded px-2 py-1.5 text-sm"
                value={deviceId}
                onChange={e => setDeviceId(e.target.value)}
              >
                <option value="">— 选择设备 —</option>
                {onlineDevices.map(d => (
                  <option key={d.id} value={d.id}>{d.name}</option>
                ))}
              </select>
            )}
          </div>

          <div>
            <label className="block text-sm font-medium mb-1">最大步数</label>
            <input
              type="number"
              className="w-full border rounded px-2 py-1.5 text-sm"
              value={maxSteps}
              onChange={e => setMaxSteps(parseInt(e.target.value) || 20)}
              min={5}
              max={100}
            />
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium mb-1">Provider</label>
            <select
              className="w-full border rounded px-2 py-1.5 text-sm"
              value={provider}
              onChange={e => handleProviderChange(e.target.value)}
            >
              {PROVIDERS.map(p => <option key={p}>{p}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-sm font-medium mb-1">Model</label>
            <input
              type="text"
              className="w-full border rounded px-2 py-1.5 text-sm font-mono"
              value={model}
              onChange={e => setModel(e.target.value)}
            />
          </div>
        </div>

        <button
          className="w-full bg-blue-600 text-white py-2 rounded font-medium hover:bg-blue-700 disabled:opacity-50 mt-2"
          disabled={!canSubmit}
          onClick={() => runMut.mutate()}
        >
          {runMut.isPending ? '启动中…' : '▶ 开始任务'}
        </button>
      </div>
    </div>
  )
}
