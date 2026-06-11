/** Mission phase order and display labels — must match backend PHASE_ORDER. */

export const MISSION_PHASES = [
  'intake',
  'repo_scan',
  'planning',
  'research',
  'context_building',
  'analysis',
  'patching',
  'static_checks',
  'testing',
  'review',
  'security_scan',
  'final_report',
] as const

export type MissionPhase = (typeof MISSION_PHASES)[number]

export const PHASE_LABELS: Record<MissionPhase, string> = {
  intake: 'Intake',
  repo_scan: 'Cartographer',
  planning: 'Planning',
  research: 'Research',
  context_building: 'Context',
  analysis: 'Scribe',
  patching: 'Blacksmith',
  static_checks: 'Checks',
  testing: 'Trial Master',
  review: 'Inspector',
  security_scan: 'Warden',
  final_report: 'Report',
}

export const PHASE_GLYPHS: Record<MissionPhase, string> = {
  intake: '◈',
  repo_scan: '◎',
  planning: '◆',
  research: '◇',
  context_building: '▣',
  analysis: '✎',
  patching: '⚒',
  static_checks: '⌗',
  testing: '⚡',
  review: '◉',
  security_scan: '⛨',
  final_report: '▤',
}
