import { CheckCircle2, AlertTriangle, XCircle, HelpCircle } from 'lucide-react'
import type { DecisionType } from '../types'
import clsx from 'clsx'

interface Props { type: DecisionType | undefined; size?: 'sm' | 'lg' }

export function DecisionBadge({ type, size = 'sm' }: Props) {
  const config = ({
    approved:              { icon: CheckCircle2,  label: 'APPROVED',    cls: 'badge-approved'  },
    escalated:             { icon: AlertTriangle, label: 'ESCALATED',   cls: 'badge-escalated' },
    rejected:              { icon: XCircle,       label: 'REJECTED',    cls: 'badge-rejected'  },
    clarification_needed:  { icon: HelpCircle,    label: 'CLARIFICATION', cls: 'badge-escalated' },
  } as Record<string, { icon: typeof CheckCircle2; label: string; cls: string }>)[type ?? 'escalated']
  ?? { icon: AlertTriangle, label: 'ESCALATED', cls: 'badge-escalated' }

  const Icon = config.icon
  return (
    <span className={clsx(config.cls, size === 'lg' && 'text-base px-4 py-2')}>
      <Icon className={clsx(size === 'lg' ? 'w-5 h-5' : 'w-3.5 h-3.5')} />
      {config.label}
    </span>
  )
}
