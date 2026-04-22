import { useEffect, useState } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { fetchSettings, saveSettings, Settings as SettingsData } from '../lib/api'

const PROVIDER_MODELS: Record<string, string[]> = {
  openai: ['gpt-4o', 'gpt-4o-mini', 'gpt-4-turbo', 'gpt-3.5-turbo'],
  anthropic: ['claude-opus-4-6', 'claude-sonnet-4-6', 'claude-haiku-4-5'],
  google: ['gemini-1.5-pro', 'gemini-1.5-flash', 'gemini-2.0-flash'],
  zhipuai: ['glm-4v', 'glm-4v-plus', 'glm-4-plus', 'glm-4-flash'],
  groq: ['llama-3.1-70b-versatile', 'llama-3.1-8b-instant', 'mixtral-8x7b-32768'],
  ollama: ['llama3', 'llama3.1', 'qwen2', 'mistral'],
}

type FieldDef = { key: keyof SettingsData; label: string; placeholder?: string; type?: string }

const FIELDS: FieldDef[] = [
  { key: 'openai_api_key', label: 'OpenAI API Key', placeholder: 'sk-…', type: 'password' },
  { key: 'anthropic_api_key', label: 'Anthropic API Key', placeholder: 'sk-ant-…', type: 'password' },
  { key: 'anthropic_base_url', label: 'Anthropic Base URL (LiteLLM proxy, optional)', placeholder: 'https://litellm.example.com' },
  { key: 'gemini_api_key', label: 'Google Gemini API Key', type: 'password' },
  { key: 'zhipu_api_key', label: 'ZhipuAI API Key', type: 'password' },
  { key: 'groq_api_key', label: 'Groq API Key', type: 'password' },
  { key: 'ollama_base_url', label: 'Ollama Base URL', placeholder: 'http://localhost:11434' },
]

export default function Settings() {
  const { data: remote, isLoading } = useQuery({ queryKey: ['settings'], queryFn: fetchSettings })
  const [form, setForm] = useState<Partial<SettingsData>>({})
  const [saved, setSaved] = useState(false)

  useEffect(() => { if (remote) setForm(remote) }, [remote])

  const saveMut = useMutation({
    mutationFn: () => saveSettings(form as SettingsData),
    onSuccess: () => { setSaved(true); setTimeout(() => setSaved(false), 2000) },
  })

  if (isLoading) return <p className="text-gray-500">Loading…</p>

  return (
    <div className="max-w-xl">
      <h1 className="text-2xl font-bold mb-6">Settings</h1>

      <div className="bg-white border rounded-lg p-6 shadow-sm space-y-4">
        {FIELDS.map(f => (
          <div key={f.key}>
            <label className="block text-sm font-medium mb-1">{f.label}</label>
            <input
              type={f.type || 'text'}
              className="w-full border rounded px-3 py-1.5 text-sm font-mono"
              placeholder={f.placeholder || ''}
              value={(form[f.key] as string) || ''}
              onChange={e => setForm(prev => ({ ...prev, [f.key]: e.target.value }))}
            />
          </div>
        ))}

        <div className="pt-2 border-t">
          <h3 className="text-sm font-medium mb-3">Default Model</h3>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">Provider</label>
              <select
                className="w-full border rounded px-3 py-1.5 text-sm"
                value={form.default_provider || 'openai'}
                onChange={e => {
                  const p = e.target.value
                  const models = PROVIDER_MODELS[p] || []
                  setForm(prev => ({
                    ...prev,
                    default_provider: p,
                    default_model: models[0] || '',
                  }))
                }}
              >
                {Object.keys(PROVIDER_MODELS).map(p => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Model</label>
              <input
                type="text"
                className="w-full border rounded px-3 py-1.5 text-sm font-mono"
                placeholder={PROVIDER_MODELS[form.default_provider || 'openai']?.[0] || ''}
                value={form.default_model || ''}
                onChange={e => setForm(prev => ({ ...prev, default_model: e.target.value }))}
              />
              <p className="text-xs text-gray-400 mt-1">
                可直接输入自定义 model 名，如 anthropic.novita.claude-sonnet-4-6
              </p>
            </div>
          </div>
        </div>

        <div className="pt-2 border-t">
          <h3 className="text-sm font-medium mb-1">
            Verification Model
            <span className="text-xs font-normal text-gray-400 ml-1">(optional)</span>
          </h3>
          <p className="text-xs text-gray-400 mb-3">
            用于 pass/fail 判断的专用模型，建议使用视觉更强的模型（如 gpt-4o）以减少误判。留空则与 Agent 使用同一模型。
          </p>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">Provider</label>
              <select
                className="w-full border rounded px-3 py-1.5 text-sm"
                value={form.verifier_provider || ''}
                onChange={e => setForm(prev => ({ ...prev, verifier_provider: e.target.value }))}
              >
                <option value="">— 与 Agent 相同 —</option>
                {Object.keys(PROVIDER_MODELS).map(p => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-xs text-gray-500 mb-1">Model</label>
              <input
                type="text"
                className="w-full border rounded px-3 py-1.5 text-sm font-mono"
                placeholder="e.g. gpt-4o"
                value={form.verifier_model || ''}
                onChange={e => setForm(prev => ({ ...prev, verifier_model: e.target.value }))}
              />
            </div>
          </div>
        </div>

        <div className="pt-2 border-t">
          <h3 className="text-sm font-medium mb-1">
            Webhook Notification
            <span className="text-xs font-normal text-gray-400 ml-1">(optional)</span>
          </h3>
          <p className="text-xs text-gray-400 mb-3">
            Run 完成后自动推送结果到飞书/钉钉/Slack。留空则不推送。
          </p>
          <div className="grid grid-cols-3 gap-3">
            <div>
              <label className="block text-xs text-gray-500 mb-1">Type</label>
              <select
                className="w-full border rounded px-3 py-1.5 text-sm"
                value={form.webhook_type || ''}
                onChange={e => setForm(prev => ({ ...prev, webhook_type: e.target.value }))}
              >
                <option value="">-- disabled --</option>
                <option value="feishu">Feishu / Lark</option>
                <option value="dingtalk">DingTalk</option>
                <option value="slack">Slack</option>
                <option value="custom">Custom POST</option>
              </select>
            </div>
            <div className="col-span-2">
              <label className="block text-xs text-gray-500 mb-1">Webhook URL</label>
              <input
                type="text"
                className="w-full border rounded px-3 py-1.5 text-sm font-mono"
                placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..."
                value={form.webhook_url || ''}
                onChange={e => setForm(prev => ({ ...prev, webhook_url: e.target.value }))}
              />
            </div>
          </div>
        </div>

        <button
          className="w-full bg-blue-600 text-white py-2 rounded font-medium hover:bg-blue-700 disabled:opacity-50"
          disabled={saveMut.isPending}
          onClick={() => saveMut.mutate()}
        >
          {saved ? '✓ Saved' : saveMut.isPending ? 'Saving…' : 'Save Settings'}
        </button>
      </div>
    </div>
  )
}
