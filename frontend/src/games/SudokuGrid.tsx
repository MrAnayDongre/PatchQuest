import { useCallback, useState } from 'react'
import GameShell from './GameShell'
import type { GameShellState } from './types'

const INITIAL_GRID = [
  [0, 3, 0, 0],
  [4, 0, 0, 2],
  [1, 0, 0, 3],
  [0, 0, 2, 0],
]

const SOLUTION = [
  [2, 3, 1, 4],
  [4, 1, 3, 2],
  [1, 2, 4, 3],
  [3, 4, 2, 1],
]

interface SudokuGridProps {
  onExit: () => void
}

export default function SudokuGrid({ onExit }: SudokuGridProps) {
  const [session, setSession] = useState(0)
  const [grid, setGrid] = useState(() => INITIAL_GRID.map(row => [...row]))
  const [selected, setSelected] = useState<[number, number] | null>(null)
  const [state, setState] = useState<GameShellState>('running')

  const resetGame = useCallback(() => {
    setGrid(INITIAL_GRID.map(row => [...row]))
    setSelected(null)
    setState('running')
    setSession(s => s + 1)
  }, [])

  const handleCellClick = (row: number, col: number) => {
    if (state !== 'running' || INITIAL_GRID[row][col] !== 0) return
    setSelected([row, col])
  }

  const handleInput = (value: number) => {
    if (!selected || state !== 'running') return
    const [row, col] = selected
    const newGrid = grid.map(r => [...r])
    newGrid[row][col] = value
    setGrid(newGrid)

    const isSolved = newGrid.every((r, ri) => r.every((cell, ci) => cell === SOLUTION[ri][ci]))
    if (isSolved) setState('won')
  }

  const filled = grid.flat().filter(c => c !== 0).length

  return (
    <GameShell
      key={session}
      title="Sudoku Grid"
      description="4×4 logic puzzle — fill every cell."
      controls="Click a cell, then pick a number."
      state={state}
      stats={[{ label: 'Filled', value: `${filled}/16` }]}
      onRestart={resetGame}
      onExit={onExit}
      resultTitle="Puzzle Complete!"
      resultMessage="Every cell is correct. Sharp thinking."
    >
      <div className="game-sudoku">
        <div className="game-sudoku__grid" role="grid" aria-label="4 by 4 sudoku grid">
          {grid.map((row, ri) =>
            row.map((cell, ci) => {
              const isFixed = INITIAL_GRID[ri][ci] !== 0
              const isSelected = selected?.[0] === ri && selected?.[1] === ci
              const isWrong = cell !== 0 && !isFixed && cell !== SOLUTION[ri][ci]
              const isCorrect = cell !== 0 && cell === SOLUTION[ri][ci]
              return (
                <button
                  key={`${session}-${ri}-${ci}`}
                  type="button"
                  role="gridcell"
                  aria-selected={isSelected}
                  onClick={() => handleCellClick(ri, ci)}
                  className={`game-sudoku__cell ${isFixed ? 'game-sudoku__cell--fixed' : ''} ${isSelected ? 'game-sudoku__cell--selected' : ''} ${isWrong ? 'game-sudoku__cell--wrong' : ''} ${isCorrect && !isFixed ? 'game-sudoku__cell--correct' : ''}`}
                  disabled={isFixed || state !== 'running'}
                >
                  {cell || ''}
                </button>
              )
            })
          )}
        </div>

        {state === 'running' && (
          <div className="game-sudoku__pad">
            {[1, 2, 3, 4].map(n => (
              <button key={n} type="button" className="game-btn game-btn--ghost" onClick={() => handleInput(n)}>
                {n}
              </button>
            ))}
            <button type="button" className="game-btn game-btn--danger" onClick={() => selected && handleInput(0)}>
              Clear
            </button>
          </div>
        )}
      </div>
    </GameShell>
  )
}
