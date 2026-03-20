import { ShieldCheck, ShieldAlert, ShieldX } from 'lucide-react'
import type { AIS } from '../types'
import clsx from 'clsx'

interface Props {
  ais: AIS
  large?: boolean
}

export function AISBadge({ ais, large = false }: Props) {
  const config = ({
    'Audit-Ready':             { icon: ShieldCheck, color: 'text-green-400',  bg: 'bg-green-950 border-green-800' },
    'Review Recommended':      { icon: ShieldAlert, color: 'text-amber-400',  bg: 'bg-amber-950 border-amber-800' },
    'Manual Review Required':  { icon: ShieldX,     color: 'text-red-400',    bg: 'bg-red-950   border-red-800'   },
    'Incomplete':              { icon: ShieldAlert, color: 'text-slate-400',  bg: 'bg-slate-800 border-slate-700' },
  } as Record<string, { icon: typeof ShieldCheck; color: string; bg: string }>)[ais.grade]
    ?? { icon: ShieldAlert, color: 'text-slate-400', bg: 'bg-slate-800 border-slate-700' }

  const Icon = config.icon

  if (large) {
    return (
      <div className={clsx('rounded-2xl border p-6 flex flex-col items-center gap-3', config.bg)}>
        <Icon className={clsx('w-10 h-10', config.color)} />
        <div className={clsx('text-5xl font-black', config.color)}>{ais.score}</div>
        <div className="text-xs text-slate-400 font-medium tracking-widest uppercase">Audit Intelligence Score</div>
        <div className={clsx('text-sm font-semibold', config.color)}>{ais.grade}</div>
        {ais.eu_ai_act_article_13_compliant && (
          <div className="text-xs bg-green-900/50 text-green-300 border border-green-700 px-3 py-1 rounded-full">
            EU AI Act Art.13 Compliant
          </div>
        )}
        {/* Component breakdown */}
        <div className="w-full mt-2 space-y-1.5">
          {Object.entries(ais.components).map(([key, val]) => {
            const max = key === 'completeness' || key === 'decision_traceability' ? 25
                      : key === 'rule_coverage' || key === 'contestability' ? 20
                      : 10
            return (
              <div key={key} className="flex items-center gap-2">
                <div className="text-xs text-slate-400 w-44 truncate capitalize">
                  {key.replace(/_/g, ' ')}
                </div>
                <div className="flex-1 h-1.5 bg-slate-800 rounded-full overflow-hidden">
                  <div
                    className={clsx('h-full rounded-full', config.color.replace('text-', 'bg-'))}
                    style={{ width: `${(val / max) * 100}%` }}
                  />
                </div>
                <div className="text-xs text-slate-400 w-10 text-right">{val}/{max}</div>
              </div>
            )
          })}
        </div>
        {ais.flags && ais.flags.length > 0 && (
          <div className="w-full mt-2 space-y-1">
            {ais.flags.map((flag, i) => (
              <div key={i} className="text-xs text-amber-400 bg-amber-950/50 border border-amber-800/50 rounded px-2 py-1">
                ⚠ {flag}
              </div>
            ))}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className={clsx('inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border text-sm font-semibold', config.bg, config.color)}>
      <Icon className="w-4 h-4" />
      <span>AIS {ais.score}</span>
      <span className="text-xs font-normal opacity-75">— {ais.grade}</span>
    </div>
  )
}
