import type { SVGProps } from 'react'

export type IconName =
  | 'home' | 'runs' | 'engine' | 'settings' | 'extras' | 'plus' | 'search' | 'check' | 'x' | 'chevron-right' | 'chevron-down'
  | 'play' | 'stop' | 'fork' | 'replay' | 'sun' | 'moon' | 'menu' | 'warning' | 'shield' | 'clock' | 'copy' | 'command'
  | 'arrow-right' | 'refresh' | 'file' | 'pause' | 'edit' | 'external' | 'dot'

const PATHS: Record<IconName, string> = {
  home: 'M3 10.5 12 3l9 7.5M5 9.5V20h5v-6h4v6h5V9.5',
  runs: 'M4 6h16M4 12h16M4 18h10',
  engine: 'M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3M7 6h10a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1ZM10 10h4v4h-4z',
  settings: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z',
  extras: 'M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM17 14v6M14 17h6',
  plus: 'M12 5v14M5 12h14',
  search: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14ZM20 20l-4-4',
  check: 'm5 12.5 4.5 4.5L19 7.5',
  x: 'M6 6l12 12M18 6 6 18',
  'chevron-right': 'm9 6 6 6-6 6',
  'chevron-down': 'm6 9 6 6 6-6',
  play: 'M7 5v14l12-7z',
  stop: 'M7 7h10v10H7z',
  fork: 'M7 4v6a3 3 0 0 0 3 3h4a3 3 0 0 0 3-3V4M7 20v-7M17 20v-4',
  replay: 'M4 12a8 8 0 1 0 3-6.2M4 4v4h4',
  sun: 'M12 16a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4',
  moon: 'M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5Z',
  menu: 'M4 7h16M4 12h16M4 17h16',
  warning: 'M12 4 2.5 20h19L12 4ZM12 10v5M12 17.5v.5',
  shield: 'M12 3 5 6v6c0 4.5 3 7.5 7 9 4-1.5 7-4.5 7-9V6l-7-3Z',
  clock: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM12 7v5l3 2',
  copy: 'M9 9h10v10H9zM5 15V5h10',
  command: 'M9 6a3 3 0 1 0-3 3h12a3 3 0 1 0-3-3v12a3 3 0 1 0 3-3H6a3 3 0 1 0 3 3V6Z',
  'arrow-right': 'M5 12h14M13 6l6 6-6 6',
  refresh: 'M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5',
  file: 'M7 3h7l5 5v13H7zM14 3v5h5',
  pause: 'M8 6v12M16 6v12',
  edit: 'M4 20h4L19 9l-4-4L4 16v4ZM13 7l4 4',
  external: 'M14 4h6v6M20 4l-9 9M18 14v5H5V6h5',
  dot: 'M12 12m-3 0a3 3 0 1 0 6 0 3 3 0 1 0-6 0',
}

interface IconProps extends Omit<SVGProps<SVGSVGElement>, 'name'> {
  name: IconName
  size?: number
}

export function Icon({ name, size = 18, ...rest }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      <path d={PATHS[name]} />
    </svg>
  )
}
