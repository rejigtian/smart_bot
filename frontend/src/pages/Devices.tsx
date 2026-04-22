import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { fetchDevices, createDevice, deleteDevice, Device } from '../lib/api'

export default function Devices() {
  const qc = useQueryClient()
  const [newName, setNewName] = useState('')
  const [copiedId, setCopiedId] = useState<string | null>(null)

  const { data: devices = [], isLoading } = useQuery({
    queryKey: ['devices'],
    queryFn: fetchDevices,
    refetchInterval: 5000,
  })

  const createMut = useMutation({
    mutationFn: () => createDevice(newName || 'New Device'),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['devices'] }); setNewName('') },
  })

  const deleteMut = useMutation({
    mutationFn: deleteDevice,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['devices'] }),
  })

  const copyToken = (device: Device) => {
    navigator.clipboard.writeText(device.token)
    setCopiedId(device.id)
    setTimeout(() => setCopiedId(null), 2000)
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Devices</h1>
        <div className="flex gap-2">
          <input
            className="border rounded px-3 py-1.5 text-sm w-48"
            placeholder="Device name"
            value={newName}
            onChange={e => setNewName(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && createMut.mutate()}
          />
          <button
            className="bg-blue-600 text-white px-4 py-1.5 rounded text-sm hover:bg-blue-700 disabled:opacity-50"
            onClick={() => createMut.mutate()}
            disabled={createMut.isPending}
          >
            + Generate Token
          </button>
        </div>
      </div>

      {isLoading && <p className="text-gray-500">Loading…</p>}

      {devices.length === 0 && !isLoading && (
        <div className="text-center py-16 text-gray-400">
          <p className="text-lg">No devices yet.</p>
          <p className="text-sm mt-1">Generate a token, then configure Portal app with it.</p>
        </div>
      )}

      <div className="grid gap-4">
        {devices.map(d => (
          <div key={d.id} className="bg-white border rounded-lg p-4 flex items-center gap-4 shadow-sm">
            <span
              className={`w-3 h-3 rounded-full flex-shrink-0 ${
                d.status === 'online' ? 'bg-green-500' : 'bg-gray-300'
              }`}
            />
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-medium">{d.name}</span>
                <span
                  className={`text-xs px-1.5 py-0.5 rounded-full font-medium ${
                    d.status === 'online'
                      ? 'bg-green-100 text-green-700'
                      : 'bg-gray-100 text-gray-500'
                  }`}
                >
                  {d.status}
                </span>
              </div>
              <div className="text-xs text-gray-400 mt-0.5 font-mono truncate">
                ID: {d.id}
              </div>
              <div className="text-xs text-gray-400 font-mono truncate">
                Token: {d.token.slice(0, 20)}…
              </div>
            </div>
            <div className="flex gap-2">
              <button
                className="text-xs px-3 py-1.5 border rounded hover:bg-gray-50"
                onClick={() => copyToken(d)}
              >
                {copiedId === d.id ? '✓ Copied' : 'Copy Token'}
              </button>
              <button
                className="text-xs px-3 py-1.5 border border-red-200 text-red-600 rounded hover:bg-red-50"
                onClick={() => {
                  if (confirm(`Delete device "${d.name}"?`)) deleteMut.mutate(d.id)
                }}
              >
                Delete
              </button>
            </div>
          </div>
        ))}
      </div>

      {devices.length > 0 && (
        <div className="mt-6 p-4 bg-blue-50 rounded-lg text-sm text-blue-800">
          <strong>Portal setup:</strong> In the Portal app, go to Custom Connection and set the server URL to{' '}
          <code className="bg-blue-100 px-1 rounded">ws://YOUR_SERVER/v1/providers/join</code>{' '}
          and paste your device token.
        </div>
      )}
    </div>
  )
}
