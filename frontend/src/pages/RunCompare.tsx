import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { fetchRuns, compareRuns, Run, CompareOut } from '../lib/api'

const STATUS_COLOR: Record<string, string> = {
  pass: 'bg-green-100 text-green-700',
  fail: 'bg-red-100 text-red-700',
  error: 'bg-orange-100 text-orange-700',
  skip: 'bg-gray-100 text-gray-500',
  pending: 'bg-yellow-50 text-yellow-600',
}

const statusBadge = (s: string | null) => {
  if (!s) return <span className="text-xs text-gray-300">-</span>
  return (
    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${STATUS_COLOR[s] ?? 'bg-gray-100 text-gray-500'}`}>
      {s}
    </span>
  )
}

export default function RunCompare() {
  const navigate = useNavigate()
  const [runA, setRunA] = useState('')
  const [runB, setRunB] = useState('')

  const { data: runs = [] } = useQuery({
    queryKey: ['runs'],
    queryFn: () => fetchRuns(),
  })

  const { data: comparison, isLoading } = useQuery<CompareOut>({
    queryKey: ['compare', runA, runB],
    queryFn: () => compareRuns(runA, runB),
    enabled: !!runA && !!runB && runA !== runB,
  })

  const doneRuns = runs.filter(r => r.status === 'done' || r.status === 'error' || r.status === 'cancelled')

  const runLabel = (r: Run) =>
    `${r.suite_name ?? r.suite_id.slice(0, 8)} — ${r.provider}/${r.model} — ${new Date(r.created_at).toLocaleDateString()} (${r.passed}P/${r.failed}F)`

  return (
    <div>
      <button className="text-sm text-blue-600 hover:underline mb-4 block" onClick={() => navigate('/runs')}>
        &larr; Back to runs
      </button>

      <h1 className="text-xl font-bold mb-4">Run Comparison</h1>

      {/* Selectors */}
      <div className="grid grid-cols-2 gap-4 mb-6">
        <div>
          <label className="text-sm text-gray-500 mb-1 block">Run A (baseline)</label>
          <select
            className="w-full border rounded px-3 py-2 text-sm"
            value={runA}
            onChange={e => setRunA(e.target.value)}
          >
            <option value="">Select run...</option>
            {doneRuns.map(r => (
              <option key={r.id} value={r.id}>{runLabel(r)}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="text-sm text-gray-500 mb-1 block">Run B (new)</label>
          <select
            className="w-full border rounded px-3 py-2 text-sm"
            value={runB}
            onChange={e => setRunB(e.target.value)}
          >
            <option value="">Select run...</option>
            {doneRuns.map(r => (
              <option key={r.id} value={r.id}>{runLabel(r)}</option>
            ))}
          </select>
        </div>
      </div>

      {runA && runB && runA === runB && (
        <p className="text-sm text-orange-600 mb-4">Please select two different runs.</p>
      )}

      {isLoading && <p className="text-sm text-gray-400">Loading comparison...</p>}

      {comparison && (
        <>
          {/* Summary */}
          <div className="flex gap-4 mb-4 text-sm">
            <div className="px-4 py-2 bg-green-50 border border-green-200 rounded-lg">
              <span className="text-green-700 font-bold text-lg">{comparison.summary.improved}</span>
              <span className="text-green-600 ml-1">improved</span>
            </div>
            <div className="px-4 py-2 bg-red-50 border border-red-200 rounded-lg">
              <span className="text-red-700 font-bold text-lg">{comparison.summary.regressed}</span>
              <span className="text-red-600 ml-1">regressed</span>
            </div>
            <div className="px-4 py-2 bg-gray-50 border border-gray-200 rounded-lg">
              <span className="text-gray-700 font-bold text-lg">{comparison.summary.unchanged}</span>
              <span className="text-gray-500 ml-1">unchanged</span>
            </div>
          </div>

          {/* Run meta */}
          <div className="grid grid-cols-2 gap-4 mb-4 text-xs text-gray-500">
            <div className="bg-white border rounded-lg px-3 py-2">
              <span className="font-medium text-gray-700">A:</span> {comparison.run_a.provider}/{comparison.run_a.model} — {comparison.run_a.passed}P/{comparison.run_a.failed}F/{comparison.run_a.total}T
            </div>
            <div className="bg-white border rounded-lg px-3 py-2">
              <span className="font-medium text-gray-700">B:</span> {comparison.run_b.provider}/{comparison.run_b.model} — {comparison.run_b.passed}P/{comparison.run_b.failed}F/{comparison.run_b.total}T
            </div>
          </div>

          {/* Comparison table */}
          <div className="bg-white border rounded-lg overflow-hidden shadow-sm">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-gray-50 border-b text-left text-xs text-gray-500 uppercase tracking-wide">
                  <th className="px-4 py-2">Test Case</th>
                  <th className="px-4 py-2 text-center w-20">Run A</th>
                  <th className="px-4 py-2 text-center w-10"></th>
                  <th className="px-4 py-2 text-center w-20">Run B</th>
                  <th className="px-4 py-2 text-center w-20">Steps</th>
                  <th className="px-4 py-2">Change</th>
                </tr>
              </thead>
              <tbody>
                {comparison.cases.map((c, i) => {
                  let change = ''
                  let changeColor = 'text-gray-400'
                  if (c.status_a && c.status_b) {
                    if (c.status_a !== 'pass' && c.status_b === 'pass') {
                      change = 'improved'
                      changeColor = 'text-green-600 font-medium'
                    } else if (c.status_a === 'pass' && c.status_b !== 'pass') {
                      change = 'regressed'
                      changeColor = 'text-red-600 font-medium'
                    } else if (c.status_a === c.status_b) {
                      change = 'same'
                    } else {
                      change = 'changed'
                      changeColor = 'text-yellow-600'
                    }
                  }
                  return (
                    <tr key={c.case_id} className={`border-t hover:bg-gray-50 ${i % 2 === 0 ? '' : 'bg-gray-25'}`}>
                      <td className="px-4 py-2">
                        <div className="text-xs text-gray-400 font-mono truncate">{c.path}</div>
                        <div className="text-xs text-gray-600 truncate">{c.expected}</div>
                      </td>
                      <td className="px-4 py-2 text-center">{statusBadge(c.status_a)}</td>
                      <td className="px-4 py-2 text-center text-gray-300">&rarr;</td>
                      <td className="px-4 py-2 text-center">{statusBadge(c.status_b)}</td>
                      <td className="px-4 py-2 text-center text-xs text-gray-400">
                        {c.steps_a > 0 && <span>{c.steps_a}</span>}
                        {c.steps_a > 0 && c.steps_b > 0 && <span> / </span>}
                        {c.steps_b > 0 && <span>{c.steps_b}</span>}
                      </td>
                      <td className={`px-4 py-2 text-xs ${changeColor}`}>{change}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}
