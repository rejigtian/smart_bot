import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api, fetchDevices } from '../lib/api'

interface Element {
  index: number
  text: string
  className: string
  resourceId: string
  cx: number
  cy: number
}

interface Step {
  action: string
  args: Record<string, unknown>
  description: string
}

interface SnapshotData {
  screenshot_b64: string
  ui_text: string
  elements: Element[]
}

interface ActionData extends SnapshotData {
  result: string
  description: string
}

const BTN = 'px-3 py-1.5 text-xs rounded border font-medium disabled:opacity-40 disabled:cursor-not-allowed transition-colors'
const BTN_GRAY = `${BTN} border-gray-300 text-gray-700 hover:bg-gray-100`
const BTN_BLUE = `${BTN} border-blue-500 bg-blue-600 text-white hover:bg-blue-700`
const BTN_RED  = `${BTN} border-red-400 text-red-600 hover:bg-red-50`

export default function Recorder() {
  const navigate = useNavigate()

  const { data: allDevices = [] } = useQuery({ queryKey: ['devices'], queryFn: fetchDevices, refetchInterval: 5000 })
  const onlineDevices = allDevices.filter(d => d.status === 'online')

  const [deviceId, setDeviceId] = useState('')
  const [recording, setRecording] = useState(false)
  const [screenshot, setScreenshot] = useState('')
  const [elements, setElements] = useState<Element[]>([])
  const [steps, setSteps] = useState<Step[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  // Text input action state
  const [inputText, setInputText] = useState('')
  const [inputClear, setInputClear] = useState(false)

  // Save form
  const [suiteName, setSuiteName] = useState('')
  const [expected, setExpected] = useState('')
  const [saved, setSaved] = useState<{ suiteId: string } | null>(null)

  // ── Device selection helpers ────────────────────────────────────────────────
  const selectedDevice = allDevices.find(d => d.id === deviceId)

  const applySnapshot = (data: SnapshotData) => {
    setScreenshot(data.screenshot_b64)
    setElements(data.elements)
  }

  // ── API calls ───────────────────────────────────────────────────────────────
  const fetchSnapshot = async () => {
    setLoading(true)
    setError('')
    try {
      const { data } = await api.get<SnapshotData>(`/recorder/snapshot?device_id=${deviceId}`)
      applySnapshot(data)
    } catch (e: unknown) {
      setError((e as Error).message ?? 'Snapshot failed')
    } finally {
      setLoading(false)
    }
  }

  const doAction = async (action: string, args: Record<string, unknown>) => {
    setLoading(true)
    setError('')
    try {
      const { data } = await api.post<ActionData>('/recorder/action', { device_id: deviceId, action, args })
      applySnapshot(data)
      setSteps(prev => [...prev, { action, args, description: data.description }])
    } catch (e: unknown) {
      setError((e as Error).message ?? 'Action failed')
    } finally {
      setLoading(false)
    }
  }

  const startRecording = async () => {
    if (!deviceId) return
    setSteps([])
    setSaved(null)
    setError('')
    setLoading(true)
    try {
      const { data } = await api.get<SnapshotData>(`/recorder/snapshot?device_id=${deviceId}`)
      applySnapshot(data)
      setRecording(true)
    } catch (e: unknown) {
      setError((e as Error).message ?? 'Failed to connect to device')
    } finally {
      setLoading(false)
    }
  }

  const stopRecording = () => {
    setRecording(false)
  }

  const resetRecording = () => {
    setRecording(false)
    setScreenshot('')
    setElements([])
    setSteps([])
    setSaved(null)
    setError('')
    setSuiteName('')
    setExpected('')
  }

  const saveRecording = async () => {
    if (!suiteName.trim() || !expected.trim() || steps.length === 0) return
    setLoading(true)
    setError('')
    try {
      const { data } = await api.post<{ suite_id: string }>('/recorder/save', {
        device_id: deviceId,
        suite_name: suiteName.trim(),
        expected: expected.trim(),
        steps,
      })
      setSaved({ suiteId: data.suite_id })
    } catch (e: unknown) {
      setError((e as Error).message ?? 'Save failed')
    } finally {
      setLoading(false)
    }
  }

  // ── Render ──────────────────────────────────────────────────────────────────
  return (
    <div>
      <h1 className="text-2xl font-bold mb-6">录制测试用例</h1>

      {/* Device selector (always visible) */}
      {!recording && (
        <div className="bg-white border rounded-lg p-4 shadow-sm mb-6 flex items-end gap-4 flex-wrap">
          <div className="flex-1 min-w-[200px]">
            <label className="block text-sm font-medium mb-1">选择设备</label>
            {onlineDevices.length === 0 ? (
              <p className="text-sm text-gray-400">暂无在线设备</p>
            ) : (
              <select
                className="w-full border rounded px-3 py-1.5 text-sm"
                value={deviceId}
                onChange={e => setDeviceId(e.target.value)}
              >
                <option value="">— 请选择 —</option>
                {onlineDevices.map(d => (
                  <option key={d.id} value={d.id}>{d.name} ({d.id.slice(0, 8)}…)</option>
                ))}
              </select>
            )}
          </div>
          <button
            className={BTN_BLUE}
            disabled={!deviceId || loading}
            onClick={startRecording}
          >
            {loading ? '连接中…' : '▶ 开始录制'}
          </button>
          <p className="w-full text-xs text-gray-400">
            录制模式：选择设备后，在网页上操作即可控制手机并同步记录步骤，完成后保存为可重复运行的测试用例。
          </p>
        </div>
      )}

      {error && (
        <div className="mb-4 p-3 bg-red-50 border border-red-200 rounded text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Recording UI */}
      {recording && (
        <>
          {/* Control bar */}
          <div className="flex items-center gap-3 mb-4">
            <span className="text-sm font-medium">
              <span className="inline-block w-2 h-2 rounded-full bg-red-500 animate-pulse mr-1.5" />
              录制中 — {selectedDevice?.name ?? deviceId}
            </span>
            <button className={BTN_GRAY} disabled={loading} onClick={fetchSnapshot}>
              {loading ? '…' : '⟳ 刷新截图'}
            </button>
            <button className={BTN_RED} onClick={stopRecording}>
              ⏹ 停止录制
            </button>
            <span className="ml-auto text-sm text-gray-400">{steps.length} 步已录制</span>
          </div>

          {/* Main 2-column layout */}
          <div className="grid grid-cols-[1fr_340px] gap-5 mb-6">
            {/* Left: screenshot + action bar */}
            <div>
              {screenshot ? (
                <img
                  src={`data:image/png;base64,${screenshot}`}
                  alt="screen"
                  className="w-full rounded-lg border shadow-sm mb-3"
                />
              ) : (
                <div className="h-64 bg-gray-100 rounded-lg flex items-center justify-center text-gray-400 text-sm mb-3">
                  截图加载中…
                </div>
              )}

              {/* Action bar */}
              <div className="bg-white border rounded-lg p-3 shadow-sm space-y-2">
                {/* Scroll */}
                <div>
                  <div className="text-xs text-gray-400 mb-1.5 font-medium">滚动</div>
                  <div className="flex gap-2 flex-wrap">
                    {(['down', 'up', 'left', 'right'] as const).map(dir => {
                      const labels: Record<string, string> = { down: '↓ 向下', up: '↑ 向上', left: '← 向左', right: '→ 向右' }
                      return (
                        <button key={dir} className={BTN_GRAY} disabled={loading}
                          onClick={() => doAction('scroll', { direction: dir, distance: 'medium' })}>
                          {labels[dir]}
                        </button>
                      )
                    })}
                  </div>
                </div>

                {/* System keys */}
                <div>
                  <div className="text-xs text-gray-400 mb-1.5 font-medium">系统操作</div>
                  <div className="flex gap-2 flex-wrap">
                    {[
                      { action: 'back', label: '↩ 返回' },
                      { action: 'home', label: '⌂ 主页' },
                      { action: 'recent', label: '⊞ 最近' },
                    ].map(({ action, label }) => (
                      <button key={action} className={BTN_GRAY} disabled={loading}
                        onClick={() => doAction('global_action', { action })}>
                        {label}
                      </button>
                    ))}
                  </div>
                </div>

                {/* Text input */}
                <div>
                  <div className="text-xs text-gray-400 mb-1.5 font-medium">输入文本</div>
                  <div className="flex gap-2 items-center">
                    <input
                      type="text"
                      className="flex-1 border rounded px-2 py-1 text-sm"
                      placeholder="输入内容后点击「输入」"
                      value={inputText}
                      onChange={e => setInputText(e.target.value)}
                      onKeyDown={e => {
                        if (e.key === 'Enter' && inputText) {
                          doAction('input_text', { text: inputText, clear: inputClear })
                          setInputText('')
                        }
                      }}
                    />
                    <label className="flex items-center gap-1 text-xs text-gray-500 cursor-pointer select-none">
                      <input type="checkbox" checked={inputClear} onChange={e => setInputClear(e.target.checked)} />
                      清空
                    </label>
                    <button
                      className={BTN_BLUE}
                      disabled={!inputText || loading}
                      onClick={() => {
                        doAction('input_text', { text: inputText, clear: inputClear })
                        setInputText('')
                      }}
                    >
                      输入
                    </button>
                  </div>
                </div>
              </div>
            </div>

            {/* Right: element list */}
            <div>
              <div className="text-xs font-medium text-gray-500 uppercase tracking-wide mb-2">
                UI 元素 ({elements.length})
              </div>
              <div className="bg-white border rounded-lg shadow-sm overflow-y-auto" style={{ maxHeight: '70vh' }}>
                {elements.length === 0 && (
                  <p className="text-sm text-gray-400 px-4 py-6 text-center">暂无可交互元素</p>
                )}
                {elements.map((el, i) => (
                  <button
                    key={el.index}
                    disabled={loading}
                    onClick={() => doAction('tap_element', { index: el.index })}
                    className={`w-full text-left px-3 py-2 hover:bg-blue-50 disabled:opacity-50 transition-colors ${
                      i > 0 ? 'border-t' : ''
                    }`}
                  >
                    <div className="flex items-start gap-2">
                      <span className="shrink-0 mt-0.5 text-xs font-mono bg-gray-100 text-gray-500 rounded px-1">
                        {el.index}
                      </span>
                      <div className="min-w-0">
                        <div className="text-sm truncate">
                          {el.text || <span className="text-gray-400 italic">{el.className || 'element'}</span>}
                        </div>
                        {el.resourceId && (
                          <div className="text-xs text-gray-400 truncate">{el.resourceId}</div>
                        )}
                      </div>
                    </div>
                  </button>
                ))}
              </div>
            </div>
          </div>
        </>
      )}

      {/* Recorded steps */}
      {steps.length > 0 && (
        <div className="bg-white border rounded-lg shadow-sm p-4 mb-5">
          <div className="text-sm font-semibold mb-2">已录制步骤（{steps.length}）</div>
          <ol className="space-y-1">
            {steps.map((s, i) => (
              <li key={i} className="flex items-start gap-2 text-sm">
                <span className="shrink-0 font-mono text-xs text-gray-400 mt-0.5 w-5 text-right">{i + 1}.</span>
                <span className="text-gray-700">{s.description}</span>
              </li>
            ))}
          </ol>
        </div>
      )}

      {/* Save form (shown when stopped with steps, or while recording) */}
      {(steps.length > 0) && !saved && (
        <div className="bg-white border rounded-lg shadow-sm p-4 space-y-3">
          <div className="text-sm font-semibold">保存为测试用例</div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">套件名称</label>
              <input
                type="text"
                className="w-full border rounded px-3 py-1.5 text-sm"
                placeholder="例：登录流程测试"
                value={suiteName}
                onChange={e => setSuiteName(e.target.value)}
              />
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">期望结果</label>
              <input
                type="text"
                className="w-full border rounded px-3 py-1.5 text-sm"
                placeholder="例：成功进入主页并显示欢迎语"
                value={expected}
                onChange={e => setExpected(e.target.value)}
              />
            </div>
          </div>
          <div className="flex gap-3">
            <button
              className={BTN_BLUE}
              disabled={!suiteName.trim() || !expected.trim() || steps.length === 0 || loading}
              onClick={saveRecording}
            >
              {loading ? '保存中…' : '✓ 保存为测试用例'}
            </button>
            <button className={BTN_GRAY} onClick={resetRecording}>
              重置
            </button>
          </div>
        </div>
      )}

      {/* Success state */}
      {saved && (
        <div className="bg-green-50 border border-green-200 rounded-lg p-4 flex items-center gap-4">
          <span className="text-green-700 font-medium">✓ 测试用例已保存</span>
          <button
            className="text-sm text-blue-600 hover:underline"
            onClick={() => navigate(`/suites/${saved.suiteId}`)}
          >
            查看套件 →
          </button>
          <button className={`${BTN_GRAY} ml-auto`} onClick={resetRecording}>
            继续录制
          </button>
        </div>
      )}
    </div>
  )
}
