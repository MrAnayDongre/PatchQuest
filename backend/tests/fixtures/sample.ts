import { useState } from 'react'
import type { Config } from './types'

export interface UserProfile {
  id: string
  name: string
}

export type Status = 'active' | 'inactive'

export enum Color {
  Red = 'red',
  Blue = 'blue',
}

export function processData(input: string): string {
  return input.trim()
}

export const fetchUser = async (id: string) => {
  return { id, name: 'test' }
}

export class DataService {
  process(data: string) {
    return data
  }
}

export default function MainComponent() {
  const [state, setState] = useState(0)
  return state
}
