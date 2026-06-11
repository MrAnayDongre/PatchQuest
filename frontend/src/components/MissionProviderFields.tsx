import type { ProviderInfo, ProviderStatusInfo } from '../api/types'

interface MissionProviderFieldsProps {
  provider: string
  onProviderChange: (name: string) => void
  model: string
  onModelChange: (model: string) => void
  memoryMode: string
  onMemoryModeChange: (mode: string) => void
  runtimeMode: string
  onRuntimeModeChange: (mode: string) => void
  providers: ProviderInfo[]
  providerStatuses: ProviderStatusInfo[]
  showMemoryMode?: boolean
}

export default function MissionProviderFields({
  provider,
  onProviderChange,
  model,
  onModelChange,
  memoryMode,
  onMemoryModeChange,
  runtimeMode,
  onRuntimeModeChange,
  providers,
  providerStatuses,
  showMemoryMode = true,
}: MissionProviderFieldsProps) {
  const selectedProvider = providers.find(p => p.name === provider)
  const selectedStatus = providerStatuses.find(s => s.name === provider)
  const providerKeyMissing = selectedStatus && !selectedStatus.key_set && provider !== 'mock' && provider !== 'ollama'
  const modelDisabled = provider === 'mock'

  return (
    <>
      <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: '140px' }}>
          <label className="section-title" htmlFor="provider">LLM Provider</label>
          <select
            id="provider"
            value={provider}
            onChange={(e) => onProviderChange(e.target.value)}
            style={{ width: '100%', marginTop: '0.25rem' }}
          >
            {providers.length > 0 ? (
              providers.map(p => {
                const st = providerStatuses.find(s => s.name === p.name)
                const suffix = p.name !== 'mock' && st && !st.key_set ? ' (no key)' : ''
                const configured = p.name !== 'mock' && st?.key_set ? ' — key configured' : ''
                return (
                  <option key={p.name} value={p.name}>
                    {p.display_name}{suffix}{configured}
                  </option>
                )
              })
            ) : (
              <>
                <option value="mock">Mock Demo</option>
                <option value="nvidia">NVIDIA NIM / Build</option>
                <option value="openai">OpenAI</option>
                <option value="anthropic">Anthropic</option>
                <option value="groq">Groq</option>
                <option value="ollama">Ollama</option>
                <option value="openrouter">OpenRouter</option>
                <option value="openai_compatible">OpenAI-compatible / Custom</option>
              </>
            )}
          </select>
          {providerKeyMissing && (
            <div style={{ fontSize: '0.7rem', color: 'var(--accent-red)', marginTop: '0.25rem' }}>
              Set <code>{selectedProvider?.api_key_env}</code> env var to use this provider. Scheduled runs will fail if the key is missing.
            </div>
          )}
          {selectedStatus?.key_set && provider !== 'mock' && (
            <div style={{ fontSize: '0.7rem', color: 'var(--accent-green, #6b8)', marginTop: '0.25rem' }}>
              API key configured
            </div>
          )}
        </div>
        <div style={{ flex: 1, minWidth: '140px' }}>
          <label className="section-title" htmlFor="model">Model</label>
          {modelDisabled ? (
            <input
              id="model"
              type="text"
              value=""
              disabled
              placeholder="Not used for mock"
              style={{ width: '100%', marginTop: '0.25rem', opacity: 0.6 }}
            />
          ) : selectedProvider && selectedProvider.models.length > 0 ? (
            <select
              id="model"
              value={model}
              onChange={(e) => onModelChange(e.target.value)}
              style={{ width: '100%', marginTop: '0.25rem' }}
            >
              {selectedProvider.models.map(m => <option key={m} value={m}>{m}</option>)}
            </select>
          ) : (
            <input
              id="model"
              type="text"
              value={model}
              onChange={(e) => onModelChange(e.target.value)}
              placeholder="model name"
              style={{ width: '100%', marginTop: '0.25rem' }}
            />
          )}
        </div>
      </div>

      <div style={{ display: 'flex', gap: '1rem', flexWrap: 'wrap', alignItems: 'flex-end' }}>
        {showMemoryMode && (
          <div style={{ flex: 1, minWidth: '140px' }}>
            <label className="section-title" htmlFor="memory-mode">Memory Mode</label>
            <select
              id="memory-mode"
              value={memoryMode}
              onChange={(e) => onMemoryModeChange(e.target.value)}
              style={{ width: '100%', marginTop: '0.25rem' }}
            >
              <option value="repo">Repo</option>
              <option value="run">Run / None</option>
            </select>
          </div>
        )}
        <div style={{ flex: 1, minWidth: '140px' }}>
          <label className="section-title" htmlFor="runtime-mode">Runtime</label>
          <select
            id="runtime-mode"
            value={runtimeMode}
            onChange={(e) => onRuntimeModeChange(e.target.value)}
            style={{ width: '100%', marginTop: '0.25rem' }}
          >
            <option value="local">Local</option>
            <option value="docker">Docker</option>
          </select>
        </div>
      </div>
    </>
  )
}
