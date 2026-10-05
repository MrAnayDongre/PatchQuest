import {
  cloneElement,
  createContext,
  isValidElement,
  useCallback,
  useContext,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactElement,
  type ReactNode,
} from 'react'
import { createPortal } from 'react-dom'
import type { Tone } from '../lib/eventCopy'
import { Icon } from './icons'
import { IconButton, cx } from './primitives'

const FOCUSABLE = 'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])'

function focusables(root: HTMLElement): HTMLElement[] {
  return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(el => !el.hasAttribute('hidden') && el.getAttribute('aria-hidden') !== 'true')
}

let openModals = 0

interface ModalProps {
  open: boolean
  onClose: () => void
  title: string
  description?: string
  children: ReactNode
  footer?: ReactNode
  size?: 'sm' | 'md' | 'lg'
  /** dialog: centred (bottom sheet on phones). left/bottom: edge-anchored sheet. */
  placement?: 'center' | 'left' | 'bottom'
  hideTitle?: boolean
  /** Element to focus on open instead of the first focusable. */
  initialFocus?: () => HTMLElement | null
  className?: string
}

/** Accessible modal: labelled, focus is trapped and restored, Escape and backdrop click close it. */
export function Modal({ open, onClose, title, description, children, footer, size = 'md', placement = 'center', hideTitle, initialFocus, className }: ModalProps) {
  const ref = useRef<HTMLDivElement>(null)
  const titleId = useId()
  const descId = useId()
  const onCloseRef = useRef(onClose)
  onCloseRef.current = onClose

  useEffect(() => {
    if (!open) return
    const previous = document.activeElement as HTMLElement | null
    openModals++
    document.body.style.overflow = 'hidden'
    const node = ref.current
    if (node) {
      const target = initialFocus?.() ?? focusables(node).find(el => !el.hasAttribute('data-close')) ?? node
      target.focus()
    }
    return () => {
      openModals--
      if (openModals === 0) document.body.style.overflow = ''
      previous?.focus?.()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  if (!open) return null

  const onKeyDown = (e: ReactKeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape') {
      e.stopPropagation()
      onCloseRef.current()
      return
    }
    if (e.key !== 'Tab' || !ref.current) return
    const items = focusables(ref.current)
    if (!items.length) {
      e.preventDefault()
      return
    }
    const first = items[0]
    const last = items[items.length - 1]
    const active = document.activeElement
    if (e.shiftKey && (active === first || active === ref.current)) {
      e.preventDefault()
      last.focus()
    } else if (!e.shiftKey && active === last) {
      e.preventDefault()
      first.focus()
    }
  }

  return createPortal(
    <div className="ui-overlay" onMouseDown={e => e.target === e.currentTarget && onClose()}>
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={description ? descId : undefined}
        tabIndex={-1}
        className={cx('ui-modal', `ui-modal--${size}`, `ui-modal--${placement}`, className)}
        onKeyDown={onKeyDown}
      >
        <header className="ui-modal__header">
          <h2 id={titleId} className={cx('ui-modal__title', hideTitle && 'ui-sr-only')}>
            {title}
          </h2>
          <IconButton icon="x" label="Close" size="sm" onClick={onClose} data-close />
        </header>
        {description && (
          <p id={descId} className="ui-modal__desc">
            {description}
          </p>
        )}
        <div className="ui-modal__body">{children}</div>
        {footer && <footer className="ui-modal__footer">{footer}</footer>}
      </div>
    </div>,
    document.body,
  )
}

export function Dialog(props: Omit<ModalProps, 'placement'>) {
  return <Modal {...props} placement="center" />
}

/** Edge-anchored sheet: slides in from the left (navigation) or up from the bottom (review on a phone). */
export function Drawer(props: Omit<ModalProps, 'placement'> & { side?: 'left' | 'bottom' }) {
  const { side = 'left', ...rest } = props
  return <Modal {...rest} placement={side} />
}

/* --------------------------------------------------------------- Tooltip */
export function Tooltip({ label, children }: { label: string; children: ReactElement<{ 'aria-describedby'?: string }> }) {
  const [visible, setVisible] = useState(false)
  const id = useId()
  const timer = useRef<number | undefined>(undefined)
  const show = () => {
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setVisible(true), 350)
  }
  const hide = () => {
    window.clearTimeout(timer.current)
    setVisible(false)
  }
  useEffect(() => () => window.clearTimeout(timer.current), [])
  if (!isValidElement(children)) return children
  return (
    <span className="ui-tooltip-wrap" onMouseEnter={show} onMouseLeave={hide} onFocus={show} onBlur={hide} onKeyDown={e => e.key === 'Escape' && hide()}>
      {cloneElement(children, { 'aria-describedby': visible ? id : undefined })}
      {visible && (
        <span role="tooltip" id={id} className="ui-tooltip">
          {label}
        </span>
      )}
    </span>
  )
}

/* ----------------------------------------------------------------- Toast */
export interface ToastInput {
  title: string
  message?: string
  tone?: Tone
  action?: { label: string; onClick: () => void }
}
interface ToastItem extends ToastInput {
  id: number
}

const ToastContext = createContext<{ push: (t: ToastInput) => void } | null>(null)

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const next = useRef(1)
  const dismiss = useCallback((id: number) => setItems(list => list.filter(t => t.id !== id)), [])
  const push = useCallback(
    (t: ToastInput) => {
      const id = next.current++
      setItems(list => [...list.slice(-3), { ...t, id }])
      window.setTimeout(() => dismiss(id), t.tone === 'danger' ? 12000 : 6000)
    },
    [dismiss],
  )
  const value = useMemo(() => ({ push }), [push])
  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="ui-toasts">
        {items.map(t => (
          <div key={t.id} className={cx('ui-toast', `ui-toast--${t.tone ?? 'neutral'}`)} role={t.tone === 'danger' ? 'alert' : 'status'}>
            <div className="ui-toast__text">
              <strong>{t.title}</strong>
              {t.message && <span>{t.message}</span>}
            </div>
            {t.action && (
              <button
                type="button"
                className="ui-toast__action"
                onClick={() => {
                  t.action?.onClick()
                  dismiss(t.id)
                }}
              >
                {t.action.label}
              </button>
            )}
            <IconButton icon="x" label="Dismiss notification" size="sm" onClick={() => dismiss(t.id)} />
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast(): (t: ToastInput) => void {
  const ctx = useContext(ToastContext)
  return ctx ? ctx.push : () => {}
}

/* -------------------------------------------------------- Inline alerts */
export function Callout({ tone = 'info', title, children, action }: { tone?: Tone; title?: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className={cx('ui-callout', `ui-callout--${tone}`)} role={tone === 'danger' ? 'alert' : undefined}>
      <Icon name={tone === 'success' ? 'check' : 'warning'} size={18} />
      <div className="ui-callout__body">
        {title && <strong className="ui-callout__title">{title}</strong>}
        {children && <div>{children}</div>}
      </div>
      {action && <div className="ui-callout__action">{action}</div>}
    </div>
  )
}
