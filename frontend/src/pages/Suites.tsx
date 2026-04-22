import { useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { fetchSuites, uploadSuite, deleteSuite } from '../lib/api'

export default function Suites() {
  const qc = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)

  const { data: suites = [], isLoading } = useQuery({
    queryKey: ['suites'],
    queryFn: fetchSuites,
  })

  const uploadMut = useMutation({
    mutationFn: uploadSuite,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['suites'] }),
    onError: (e: unknown) => {
      const msg = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail || String(e)
      alert(`Upload failed: ${msg}`)
    },
  })

  const deleteMut = useMutation({
    mutationFn: deleteSuite,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['suites'] }),
  })

  const handleFile = (file: File) => uploadMut.mutate(file)

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold">Test Suites</h1>
        <div>
          <input
            ref={fileRef}
            type="file"
            accept=".xmind,.md,.markdown"
            className="hidden"
            onChange={e => { if (e.target.files?.[0]) handleFile(e.target.files[0]) }}
          />
          <button
            className="bg-blue-600 text-white px-4 py-1.5 rounded text-sm hover:bg-blue-700 disabled:opacity-50"
            onClick={() => fileRef.current?.click()}
            disabled={uploadMut.isPending}
          >
            {uploadMut.isPending ? 'Uploading…' : '+ Upload .xmind / .md'}
          </button>
        </div>
      </div>

      {isLoading && <p className="text-gray-500">Loading…</p>}

      {suites.length === 0 && !isLoading && (
        <div className="text-center py-16 text-gray-400">
          <p className="text-lg">No test suites yet.</p>
          <p className="text-sm mt-1">Upload an XMind or Markdown file to get started.</p>
        </div>
      )}

      <div className="grid gap-3">
        {suites.map(s => (
          <div key={s.id} className="bg-white border rounded-lg p-4 flex items-center gap-4 shadow-sm">
            <div className="flex-1 min-w-0">
              <Link
                to={`/suites/${s.id}`}
                className="font-medium text-blue-600 hover:underline truncate block"
              >
                {s.name}
              </Link>
              <div className="text-xs text-gray-400 mt-0.5">
                {s.source_format} · {s.case_count} cases · {new Date(s.created_at).toLocaleString()}
              </div>
            </div>
            <Link
              to={`/suites/${s.id}`}
              className="text-sm px-3 py-1.5 bg-blue-600 text-white rounded hover:bg-blue-700"
            >
              Run
            </Link>
            <button
              className="text-sm px-3 py-1.5 border border-red-200 text-red-600 rounded hover:bg-red-50"
              onClick={() => {
                if (confirm(`Delete suite "${s.name}"?`)) deleteMut.mutate(s.id)
              }}
            >
              Delete
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
