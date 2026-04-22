import { useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { fetchRuns, startRun, Run } from '../lib/api'

const STATUS_COLOR: Record<string, string> = {
  pending:   'bg-yellow-50 text-yellow-600',
  running:   'bg-blue-100 text-blue-700',
  done:      'bg-green-100 text-green-700',
  error:     'bg-orange-100 text-orange-700',
  cancelled: 'bg-purple-100 text-purple-700',
}

const TERMINAL = new Set(['done', 'error', 'cancelled'])

function statusBadge(s: string) {
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${STATUS_COLOR[s] ?? 'bg-gray-100 text-gray-500'}`}>
      {s}
    </span>
  )
}

function RunRow({ run }: { run: Run }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const isActive = !TERMINAL.has(run.status)

  const rerunMut = useMutation({
    mutationFn: () => startRun({
      suite_id: run.suite_id,
      device_id: run.device_id,
      provider: run.provider,
      model: run.model,
    }),
    onSuccess: (newRun) => {
      queryClient.invalidateQueries({ queryKey: ['runs'] })
      navigate(`/runs/${newRun.id}`)
    },
  })

  return (
    <div className="border-b last:border-b-0">
      <div
        className="w-full text-left px-4 py-3 hover:bg-gray-50 transition-colors cursor-pointer"
        onClick={() => navigate(`/runs/${run.id}`)}
      >
        <div className="flex items-center gap-3 flex-wrap">
          {statusBadge(run.status)}
          {isActive && <span className="text-blue-500 animate-pulse text-xs">●</span>}
          <span className="font-medium text-sm truncate max-w-xs">
            {(run as any).suite_name ?? run.suite_id.slice(0, 8)}
          </span>
          <span className="text-xs text-gray-500">{run.provider}/{run.model}</span>
          <span className="text-xs text-gray-400 ml-auto">
            {new Date(run.created_at).toLocaleString()}
          </span>
          {TERMINAL.has(run.status) && (
            <button
              className="ml-2 px-2 py-0.5 text-xs border border-gray-300 rounded hover:bg-gray-100 disabled:opacity-50 text-gray-600"
              disabled={rerunMut.isPending}
              onClick={(e) => { e.stopPropagation(); rerunMut.mutate() }}
            >
              {rerunMut.isPending ? '…' : '↩ Re-run'}
            </button>
          )}
        </div>
        <div className="flex gap-3 mt-1 text-xs">
          <span className="text-green-600">{run.passed} pass</span>
          <span className="text-red-600">{run.failed} fail</span>
          {run.errored > 0 && <span className="text-orange-600">{run.errored} error</span>}
          {run.skipped > 0 && <span className="text-gray-500">{run.skipped} skip</span>}
          <span className="text-gray-400">/ {run.total} total</span>
          {run.total_tokens > 0 && <span className="text-purple-600">{(run.total_tokens / 1000).toFixed(1)}k tok</span>}
        </div>
      </div>
    </div>
  )
}

export default function Runs() {
  const { data: runs = [], isLoading } = useQuery({
    queryKey: ['runs'],
    queryFn: () => fetchRuns(),
    // Keep polling as long as any run is still active
    refetchInterval: (query) => {
      const list = query.state.data ?? []
      return list.some((r: Run) => !TERMINAL.has(r.status)) ? 3000 : false
    },
  })

  if (isLoading) return <p className="text-gray-400 text-sm">Loading…</p>

  return (
    <div className="max-w-3xl">
      <h1 className="text-2xl font-bold mb-6">Runs</h1>
      {runs.length === 0 ? (
        <p className="text-gray-400 text-sm">No runs yet. Start one from a test suite.</p>
      ) : (
        <div className="bg-white border rounded-lg shadow-sm overflow-hidden">
          {runs.map(r => <RunRow key={r.id} run={r} />)}
        </div>
      )}
    </div>
  )
}
