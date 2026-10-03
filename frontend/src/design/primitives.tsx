import {
  forwardRef,
  useId,
  useRef,
  type ButtonHTMLAttributes,
  type HTMLAttributes,
  type InputHTMLAttributes,
  type KeyboardEvent,
  type ReactNode,
  type SelectHTMLAttributes,
  type TextareaHTMLAttributes,
} from 'react'
import type { Tone } from '../lib/eventCopy'
import { statusLabel, statusTone } from '../lib/eventCopy'
import { Icon, type IconName } from './icons'

const cx = (...parts: (string | false | null | undefined)[]) => parts.filter(Boolean).join(' ')

/* ---------------------------------------------------------------- Button */
type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger'

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: 'sm' | 'md'
  loading?: boolean
  icon?: IconName
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'secondary', size = 'md', loading, icon, children, className, disabled, type = 'button', ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={cx('ui-btn', `ui-btn--${variant}`, size === 'sm' && 'ui-btn--sm', className)}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading ? <span className="ui-spinner" aria-hidden="true" /> : icon ? <Icon name={icon} size={16} /> : null}
      {children}
    </button>
  )
})

interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, 'children'> {
  icon: IconName
  /** Accessible name; also shown as a native tooltip. */
  label: string
  size?: 'sm' | 'md'
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { icon, label, className, size = 'md', type = 'button', ...rest },
  ref,
) {
  return (
    <button ref={ref} type={type} aria-label={label} title={label} className={cx('ui-iconbtn', size === 'sm' && 'ui-iconbtn--sm', className)} {...rest}>
      <Icon name={icon} size={size === 'sm' ? 16 : 18} />
    </button>
  )
})

/* ----------------------------------------------------------------- Forms */
interface FieldProps {
  label: string
  hint?: ReactNode
  error?: string | null
  /** Receives the props to spread on the control so label, hint and error are all wired up. */
  children: (controlProps: { id: string; 'aria-describedby'?: string; 'aria-invalid'?: boolean }) => ReactNode
  className?: string
  optional?: boolean
}

export function Field({ label, hint, error, children, className, optional }: FieldProps) {
  const id = useId()
  const describedBy = [hint ? `${id}-hint` : '', error ? `${id}-err` : ''].filter(Boolean).join(' ') || undefined
  return (
    <div className={cx('ui-field', className)}>
      <label htmlFor={id} className="ui-field__label">
        {label}
        {optional && <span className="ui-field__optional"> optional</span>}
      </label>
      {children({ id, 'aria-describedby': describedBy, 'aria-invalid': error ? true : undefined })}
      {hint && !error && (
        <p id={`${id}-hint`} className="ui-field__hint">
          {hint}
        </p>
      )}
      {error && (
        <p id={`${id}-err`} className="ui-field__error" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}

export const Input = forwardRef<HTMLInputElement, InputHTMLAttributes<HTMLInputElement>>(function Input({ className, ...rest }, ref) {
  return <input ref={ref} className={cx('ui-input', className)} {...rest} />
})

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function Textarea(
  { className, ...rest },
  ref,
) {
  return <textarea ref={ref} className={cx('ui-input ui-textarea', className)} {...rest} />
})

export const Select = forwardRef<HTMLSelectElement, SelectHTMLAttributes<HTMLSelectElement>>(function Select({ className, children, ...rest }, ref) {
  return (
    <select ref={ref} className={cx('ui-input ui-select', className)} {...rest}>
      {children}
    </select>
  )
})

interface SwitchProps {
  checked: boolean
  onChange: (checked: boolean) => void
  label: string
  description?: string
  disabled?: boolean
}

export function Switch({ checked, onChange, label, description, disabled }: SwitchProps) {
  const id = useId()
  return (
    <div className="ui-switch-row">
      <button
        type="button"
        role="switch"
        id={id}
        aria-checked={checked}
        aria-describedby={description ? `${id}-d` : undefined}
        disabled={disabled}
        className="ui-switch"
        onClick={() => onChange(!checked)}
      >
        <span className="ui-switch__thumb" />
      </button>
      <label htmlFor={id} className="ui-switch__label">
        {label}
        {description && (
          <span id={`${id}-d`} className="ui-switch__desc">
            {description}
          </span>
        )}
      </label>
    </div>
  )
}

/* -------------------------------------------------------------- Feedback */
export function Badge({ tone = 'neutral', children, className, title }: { tone?: Tone; children: ReactNode; className?: string; title?: string }) {
  return (
    <span className={cx('ui-badge', `ui-badge--${tone}`, className)} title={title}>
      {children}
    </span>
  )
}

export function StatusDot({ tone, pulse }: { tone: Tone; pulse?: boolean }) {
  return <span className={cx('ui-dot', `ui-dot--${tone}`, pulse && 'ui-dot--pulse')} aria-hidden="true" />
}

/** Dot plus a text label, so status never relies on color alone. */
export function StatusIndicator({ status, label }: { status: string; label?: string }) {
  const tone = statusTone(status)
  const pulse = status === 'running' || status === 'waiting_approval' || status === 'cancel_requested'
  return (
    <span className="ui-status">
      <StatusDot tone={tone} pulse={pulse} />
      <span>{label ?? statusLabel(status)}</span>
    </span>
  )
}

export function Skeleton({ width, height = 14, className }: { width?: number | string; height?: number | string; className?: string }) {
  return <span className={cx('ui-skeleton', className)} style={{ width, height }} aria-hidden="true" />
}

interface ProgressBarProps {
  /** 0..1; omit for an indeterminate bar */
  value?: number
  label: string
  tone?: Tone
  size?: 'sm' | 'md'
}

export function ProgressBar({ value, label, tone = 'info', size = 'md' }: ProgressBarProps) {
  const pct = value === undefined ? undefined : Math.round(Math.max(0, Math.min(1, value)) * 100)
  return (
    <div
      className={cx('ui-progress', `ui-progress--${tone}`, size === 'sm' && 'ui-progress--sm', pct === undefined && 'ui-progress--indeterminate')}
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct}
    >
      <span className="ui-progress__bar" style={pct === undefined ? undefined : { width: `${pct}%` }} />
    </div>
  )
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="ui-kbd">{children}</kbd>
}

export function EmptyState({ title, children, action, icon }: { title: string; children?: ReactNode; action?: ReactNode; icon?: IconName }) {
  return (
    <div className="ui-empty">
      {icon && (
        <span className="ui-empty__icon">
          <Icon name={icon} size={22} />
        </span>
      )}
      <h3 className="ui-empty__title">{title}</h3>
      {children && <div className="ui-empty__body">{children}</div>}
      {action && <div className="ui-empty__action">{action}</div>}
    </div>
  )
}

/* ---------------------------------------------------------------- Layout */
export function Card({ children, className, as: Tag = 'section', padded = true, ...rest }: HTMLAttributes<HTMLElement> & { as?: 'section' | 'div' | 'article'; padded?: boolean }) {
  return (
    <Tag className={cx('ui-card', padded && 'ui-card--padded', className)} {...rest}>
      {children}
    </Tag>
  )
}

export function CardHeader({ title, action, subtitle, id }: { title: ReactNode; action?: ReactNode; subtitle?: ReactNode; id?: string }) {
  return (
    <div className="ui-card__header">
      <div className="ui-card__heading">
        <h2 className="ui-card__title" id={id}>
          {title}
        </h2>
        {subtitle && <p className="ui-card__subtitle">{subtitle}</p>}
      </div>
      {action && <div className="ui-card__action">{action}</div>}
    </div>
  )
}

interface MetricCardProps {
  label: string
  value: ReactNode
  hint?: ReactNode
  tone?: Tone
  children?: ReactNode
}

export function MetricCard({ label, value, hint, tone = 'neutral', children }: MetricCardProps) {
  return (
    <div className="ui-metric">
      <div className="ui-metric__label">{label}</div>
      <div className={cx('ui-metric__value', tone !== 'neutral' && `ui-text--${tone}`)}>{value}</div>
      {hint && <div className="ui-metric__hint">{hint}</div>}
      {children}
    </div>
  )
}

/* ------------------------------------------------------------------ Tabs */
export interface TabItem {
  id: string
  label: string
  count?: number
}

interface TabsProps {
  tabs: TabItem[]
  value: string
  onChange: (id: string) => void
  label: string
  /** Prefix that ties tab buttons to panels (`${idPrefix}-tab-x` / `${idPrefix}-panel-x`). */
  idPrefix: string
}

export function Tabs({ tabs, value, onChange, label, idPrefix }: TabsProps) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({})
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const i = tabs.findIndex(t => t.id === value)
    let next = i
    if (e.key === 'ArrowRight') next = (i + 1) % tabs.length
    else if (e.key === 'ArrowLeft') next = (i - 1 + tabs.length) % tabs.length
    else if (e.key === 'Home') next = 0
    else if (e.key === 'End') next = tabs.length - 1
    else return
    e.preventDefault()
    const id = tabs[next].id
    onChange(id)
    refs.current[id]?.focus()
  }
  return (
    <div className="ui-tabs" role="tablist" aria-label={label} onKeyDown={onKey}>
      {tabs.map(t => (
        <button
          key={t.id}
          ref={el => {
            refs.current[t.id] = el
          }}
          type="button"
          role="tab"
          id={`${idPrefix}-tab-${t.id}`}
          aria-selected={t.id === value}
          aria-controls={`${idPrefix}-panel-${t.id}`}
          tabIndex={t.id === value ? 0 : -1}
          className="ui-tab"
          onClick={() => onChange(t.id)}
        >
          {t.label}
          {t.count !== undefined && t.count > 0 && <span className="ui-tab__count">{t.count}</span>}
        </button>
      ))}
    </div>
  )
}

export function TabPanel({ idPrefix, id, active, children }: { idPrefix: string; id: string; active: boolean; children: ReactNode }) {
  return (
    <div role="tabpanel" id={`${idPrefix}-panel-${id}`} aria-labelledby={`${idPrefix}-tab-${id}`} hidden={!active} tabIndex={0} className="ui-tabpanel">
      {active ? children : null}
    </div>
  )
}

/** A segmented control for small either/or choices (unified vs split, filters). */
export function Segmented<T extends string>({ options, value, onChange, label }: { options: { id: T; label: string }[]; value: T; onChange: (v: T) => void; label: string }) {
  return (
    <div className="ui-segmented" role="group" aria-label={label}>
      {options.map(o => (
        <button key={o.id} type="button" className="ui-segmented__btn" aria-pressed={o.id === value} onClick={() => onChange(o.id)}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

export { cx }
