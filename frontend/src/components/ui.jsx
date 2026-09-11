export function Card({ children, className }) {
  return (
    <div className={'rounded-2xl border border-slate-200 bg-white shadow-soft ' + (className || '')}>
      {children}
    </div>
  )
}

export function SectionTitle({ title, description, action }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h2 className="text-xl font-semibold text-slate-900">{title}</h2>
        {description ? <p className="mt-1 text-sm text-slate-500">{description}</p> : null}
      </div>
      {action}
    </div>
  )
}

export function Button({ children, variant, size, className, type, ...rest }) {
  const base =
    'inline-flex items-center justify-center gap-2 rounded-xl font-medium transition disabled:cursor-not-allowed disabled:opacity-60'
  const sizes = {
    sm: 'px-3 py-1.5 text-sm',
    md: 'px-4 py-2 text-sm',
    lg: 'px-5 py-2.5 text-base',
  }
  const variants = {
    primary: 'bg-brand-600 text-white hover:bg-brand-700 shadow-sm',
    secondary: 'bg-white text-slate-700 border border-slate-200 hover:bg-slate-50',
    ghost: 'text-slate-600 hover:bg-slate-100',
    danger: 'bg-rose-600 text-white hover:bg-rose-700',
    dark: 'bg-slate-900 text-white hover:bg-slate-800',
  }
  const classes = [base, sizes[size || 'md'], variants[variant || 'primary'], className || ''].join(' ')
  return (
    <button type={type || 'button'} className={classes} {...rest}>
      {children}
    </button>
  )
}

export function Field({ label, hint, children }) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium text-slate-700">{label}</span>
      {children}
      {hint ? <span className="mt-1 block text-xs text-slate-400">{hint}</span> : null}
    </label>
  )
}

const controlClass =
  'w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-800 outline-none transition placeholder:text-slate-400 focus:border-brand-400 focus:ring-2 focus:ring-brand-100'

export function Input({ className, ...rest }) {
  return <input className={controlClass + ' ' + (className || '')} {...rest} />
}

export function Textarea({ className, rows, ...rest }) {
  return <textarea rows={rows || 3} className={controlClass + ' ' + (className || '')} {...rest} />
}

export function Select({ className, children, ...rest }) {
  return (
    <select className={controlClass + ' ' + (className || '')} {...rest}>
      {children}
    </select>
  )
}

export function Badge({ children, tone }) {
  const tones = {
    brand: 'bg-brand-50 text-brand-700 border-brand-100',
    slate: 'bg-slate-100 text-slate-600 border-slate-200',
    green: 'bg-emerald-50 text-emerald-700 border-emerald-100',
    amber: 'bg-amber-50 text-amber-700 border-amber-100',
    rose: 'bg-rose-50 text-rose-700 border-rose-100',
  }
  return (
    <span
      className={
        'inline-flex items-center rounded-full border px-2.5 py-0.5 text-xs font-medium ' +
        (tones[tone || 'slate'] || tones.slate)
      }
    >
      {children}
    </span>
  )
}

export function Spinner({ label }) {
  return (
    <span className="inline-flex items-center gap-2 text-sm text-slate-500">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-brand-600" />
      {label ? <span>{label}</span> : null}
    </span>
  )
}

export function EmptyState({ icon, title, description, action }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-slate-300 bg-white/60 px-6 py-12 text-center">
      <div className="text-3xl">{icon || '✨'}</div>
      <div className="text-base font-medium text-slate-700">{title}</div>
      {description ? <p className="max-w-md text-sm text-slate-500">{description}</p> : null}
      {action}
    </div>
  )
}

export function Alert({ tone, children }) {
  if (!children) return null
  const tones = {
    error: 'border-rose-200 bg-rose-50 text-rose-700',
    info: 'border-brand-100 bg-brand-50 text-brand-700',
    warning: 'border-amber-200 bg-amber-50 text-amber-800',
    success: 'border-emerald-200 bg-emerald-50 text-emerald-700',
  }
  return (
    <div className={'rounded-xl border px-3 py-2 text-sm ' + (tones[tone || 'info'] || tones.info)}>{children}</div>
  )
}

export function StatCard({ label, value, hint, icon }) {
  return (
    <Card className="p-5">
      <div className="flex items-start justify-between">
        <div>
          <div className="text-sm text-slate-500">{label}</div>
          <div className="mt-2 text-3xl font-semibold text-slate-900">{value}</div>
          {hint ? <div className="mt-1 text-xs text-slate-400">{hint}</div> : null}
        </div>
        <div className="text-2xl">{icon}</div>
      </div>
    </Card>
  )
}
