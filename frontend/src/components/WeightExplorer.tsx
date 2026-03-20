import { useState, useMemo } from 'react'
import { SlidersHorizontal, AlertTriangle, RefreshCw, Trophy, ArrowRight } from 'lucide-react'
import type { ScoredSupplier } from '../types'
import { Tooltip } from './Tooltip'

interface Props {
  scored: ScoredSupplier[]
  category: string
}

const DEFAULT_WEIGHTS: Record<string, Record<string, number>> = {
  hardware:   { price: 0.40, delivery: 0.25, compliance: 0.20, esg: 0.15 },
  software:   { price: 0.30, delivery: 0.10, compliance: 0.35, esg: 0.25 },
  services:   { price: 0.25, delivery: 0.30, compliance: 0.25, esg: 0.20 },
  facilities: { price: 0.35, delivery: 0.25, compliance: 0.20, esg: 0.20 },
  default:    { price: 0.35, delivery: 0.25, compliance: 0.25, esg: 0.15 },
}

const WEIGHT_LABELS: Record<string, string> = {
  price: 'Price',
  delivery: 'Delivery speed',
  compliance: 'Compliance tier',
  esg: 'ESG / Sustainability',
}

const WEIGHT_COLORS: Record<string, string> = {
  price: 'accent-green-500',
  delivery: 'accent-blue-500',
  compliance: 'accent-purple-500',
  esg: 'accent-emerald-500',
}

function rerank(
  suppliers: ScoredSupplier[],
  weights: Record<string, number>,
): { id: string; name: string; score: number; rank: number }[] {
  const scored = suppliers.map((s) => {
    const bd = s.score_breakdown
    const raw =
      weights.price      * ((bd.price_score ?? 50) / 100) +
      weights.delivery    * ((bd.delivery_score ?? 50) / 100) +
      weights.compliance  * ((bd.compliance_score ?? 50) / 100) +
      weights.esg         * ((bd.esg_score_normalized ?? 50) / 100)
    return { id: s.id, name: s.name, score: Math.round(raw * 1000) / 10 }
  })
  scored.sort((a, b) => b.score - a.score)
  return scored.map((s, i) => ({ ...s, rank: i + 1 }))
}

export function WeightExplorer({ scored, category }: Props) {
  const baseWeights = DEFAULT_WEIGHTS[category] ?? DEFAULT_WEIGHTS.default
  const [weights, setWeights] = useState<Record<string, number>>({ ...baseWeights })
  const [open, setOpen] = useState(false)

  // Original ranking (immutable)
  const originalRanking = useMemo(() => rerank(scored, baseWeights), [scored, baseWeights])
  const originalWinner = originalRanking[0]

  // Current ranking based on slider weights
  const currentRanking = useMemo(() => {
    // Normalize weights to sum to 1
    const total = Object.values(weights).reduce((a, b) => a + b, 0)
    const normalized = Object.fromEntries(
      Object.entries(weights).map(([k, v]) => [k, v / (total || 1)])
    )
    return rerank(scored, normalized)
  }, [scored, weights])

  const currentWinner = currentRanking[0]
  const rankingFlipped = currentWinner?.id !== originalWinner?.id

  function handleWeight(key: string, value: number) {
    setWeights((prev) => ({ ...prev, [key]: value }))
  }

  function resetWeights() {
    setWeights({ ...baseWeights })
  }

  if (scored.length < 2) return null

  return (
    <div className="card border border-slate-700/50">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between text-left group"
      >
        <div className="flex items-center gap-2">
          <SlidersHorizontal className="w-4 h-4 text-brand-500" />
          <h3 className="text-sm font-semibold text-slate-300 uppercase tracking-wider">
            What-If Weight Explorer
          </h3>
          <Tooltip
            content={
              <span>
                <strong>Contestable audit trail</strong> — drag the sliders to see how changing
                criterion importance affects the supplier ranking. If the ranking flips easily,
                the decision may need human review.
                <br /><br />
                <span className="text-slate-500">
                  This is a sensitivity exploration tool. The actual decision uses the original
                  weights and is stored immutably in the audit record.
                </span>
              </span>
            }
            icon
          />
        </div>
        <span className="text-xs text-slate-500 group-hover:text-slate-300 transition-colors">
          {open ? 'Collapse' : 'Explore'}
        </span>
      </button>

      {open && (
        <div className="mt-5 space-y-5">
          {/* Sliders */}
          <div className="space-y-4">
            {Object.entries(weights).map(([key, value]) => {
              const base = baseWeights[key] ?? 0.25
              const changed = Math.abs(value - base) > 0.01
              const pct = Math.round(value * 100)
              const total = Object.values(weights).reduce((a, b) => a + b, 0)
              const normalizedPct = total > 0 ? Math.round((value / total) * 100) : 0

              return (
                <div key={key}>
                  <div className="flex items-center justify-between mb-1.5">
                    <label className="text-sm text-slate-300 font-medium flex items-center gap-2">
                      {WEIGHT_LABELS[key] ?? key}
                      {changed && (
                        <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-950/50 text-amber-400 border border-amber-800/40">
                          modified
                        </span>
                      )}
                    </label>
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-slate-500 tabular-nums w-8 text-right">
                        {normalizedPct}%
                      </span>
                    </div>
                  </div>
                  <input
                    type="range"
                    min={0}
                    max={80}
                    value={pct}
                    onChange={(e) => handleWeight(key, Number(e.target.value) / 100)}
                    className={`w-full h-1.5 rounded-full appearance-none cursor-pointer
                      bg-slate-700 ${WEIGHT_COLORS[key] ?? 'accent-slate-500'}
                      [&::-webkit-slider-thumb]:appearance-none
                      [&::-webkit-slider-thumb]:w-4 [&::-webkit-slider-thumb]:h-4
                      [&::-webkit-slider-thumb]:rounded-full
                      [&::-webkit-slider-thumb]:bg-white
                      [&::-webkit-slider-thumb]:shadow-md
                      [&::-webkit-slider-thumb]:cursor-grab
                      [&::-webkit-slider-thumb]:active:cursor-grabbing`}
                  />
                </div>
              )
            })}
          </div>

          {/* Reset button */}
          <button
            onClick={resetWeights}
            className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-slate-200 transition-colors"
          >
            <RefreshCw className="w-3 h-3" />
            Reset to {category} defaults
          </button>

          {/* Ranking flip alert */}
          {rankingFlipped && (
            <div className="flex items-start gap-3 p-3 rounded-lg bg-amber-950/30 border border-amber-800/40">
              <AlertTriangle className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
              <div>
                <div className="text-sm font-semibold text-amber-400">Ranking flipped!</div>
                <div className="text-xs text-amber-300/70 mt-1">
                  With these weights, <strong className="text-amber-300">{currentWinner.name}</strong> overtakes{' '}
                  <strong className="text-amber-300">{originalWinner.name}</strong>.
                  This suggests the original ranking is sensitive to weight assumptions.
                </div>
              </div>
            </div>
          )}

          {/* Side-by-side comparison */}
          <div className="grid grid-cols-2 gap-3">
            {/* Original */}
            <div>
              <div className="text-xs text-slate-500 uppercase tracking-wider mb-2 font-medium">
                Original ranking
              </div>
              <div className="space-y-1.5">
                {originalRanking.slice(0, 5).map((s) => (
                  <RankRow
                    key={s.id}
                    rank={s.rank}
                    name={s.name}
                    score={s.score}
                    isWinner={s.rank === 1}
                    highlight={false}
                  />
                ))}
              </div>
            </div>

            {/* Modified */}
            <div>
              <div className="text-xs uppercase tracking-wider mb-2 font-medium flex items-center gap-1.5"
                   style={{ color: rankingFlipped ? '#fbbf24' : '#94a3b8' }}>
                <ArrowRight className="w-3 h-3" />
                {rankingFlipped ? 'New ranking' : 'Same ranking'}
              </div>
              <div className="space-y-1.5">
                {currentRanking.slice(0, 5).map((s) => {
                  const originalRank = originalRanking.find((o) => o.id === s.id)?.rank ?? 0
                  const moved = originalRank !== s.rank
                  return (
                    <RankRow
                      key={s.id}
                      rank={s.rank}
                      name={s.name}
                      score={s.score}
                      isWinner={s.rank === 1}
                      highlight={moved}
                    />
                  )
                })}
              </div>
            </div>
          </div>

          {/* Counterfactual summary */}
          {rankingFlipped && (
            <div className="text-xs text-slate-400 bg-slate-800/50 rounded-lg p-3 space-y-1">
              <div className="text-slate-300 font-medium mb-1">Counterfactual explanation</div>
              {Object.entries(weights).map(([key, val]) => {
                const base = baseWeights[key] ?? 0.25
                const delta = val - base
                if (Math.abs(delta) < 0.02) return null
                const dir = delta > 0 ? 'increased' : 'decreased'
                return (
                  <div key={key}>
                    If <strong className="text-slate-200">{WEIGHT_LABELS[key]}</strong> importance is{' '}
                    {dir} by {Math.abs(Math.round(delta * 100))}pp →{' '}
                    <strong className="text-amber-400">{currentWinner.name}</strong> wins
                  </div>
                )
              }).filter(Boolean)}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function RankRow({
  rank, name, score, isWinner, highlight,
}: {
  rank: number; name: string; score: number; isWinner: boolean; highlight: boolean
}) {
  return (
    <div
      className={`flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-sm transition-all ${
        isWinner
          ? 'bg-green-950/30 border border-green-800/40'
          : highlight
          ? 'bg-amber-950/20 border border-amber-800/30'
          : 'bg-slate-800/30 border border-transparent'
      }`}
    >
      <span className="w-5 text-center shrink-0">
        {isWinner ? (
          <Trophy className="w-3.5 h-3.5 text-yellow-400 inline" />
        ) : (
          <span className="text-xs text-slate-500">{rank}</span>
        )}
      </span>
      <span className={`flex-1 truncate text-xs ${isWinner ? 'text-green-300 font-medium' : 'text-slate-300'}`}>
        {name}
      </span>
      <span className={`text-xs tabular-nums ${isWinner ? 'text-green-400 font-semibold' : 'text-slate-400'}`}>
        {score}
      </span>
    </div>
  )
}
