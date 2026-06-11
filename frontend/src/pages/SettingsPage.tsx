import { useState, useEffect } from 'react'
import PixelPanel from '../components/PixelPanel'
import PixelButton from '../components/PixelButton'
import ThemeToggle from '../components/ThemeToggle'
import { getSettings, updateSettings } from '../api/client'

export default function SettingsPage() {
  const [settings, setSettings] = useState<Record<string, unknown>>({})
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    getSettings().then(setSettings).catch(() => {})
  }, [])

  const handleSave = async () => {
    await updateSettings(settings)
    setSaved(true)
    setTimeout(() => setSaved(false), 2000)
  }

  return (
    <div style={{ maxWidth: '800px', margin: '0 auto' }}>
      <h1 className="page-title">Config Station</h1>

      <PixelPanel title="Provider Settings">
        <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '0.5rem' }}>
          <p>Configure providers in your <code>config.yaml</code> file. Supported providers:</p>
          <ul style={{ marginTop: '0.5rem', paddingLeft: '1rem' }}>
            <li>Mock (default, no API key needed)</li>
            <li>OpenAI / OpenAI-compatible</li>
            <li>Anthropic</li>
            <li>Ollama (local)</li>
            <li>Groq</li>
            <li>OpenRouter</li>
          </ul>
          <p style={{ marginTop: '0.5rem', color: 'var(--accent-yellow)' }}>
            API keys are read from environment variables. Never stored in the database.
          </p>
        </div>
      </PixelPanel>

      <PixelPanel title="Safety Settings" className="" style={{ marginTop: '1rem' }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem', marginTop: '0.5rem', fontSize: '0.8rem' }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <input
              type="checkbox"
              checked={settings.allow_network === true}
              onChange={(e) => setSettings({ ...settings, allow_network: e.target.checked })}
            />
            Allow network access
          </label>
          <label style={{ display: 'flex', alignItems: 'center', gap: '0.5rem' }}>
            <input
              type="checkbox"
              checked={settings.allow_outside_repo === true}
              onChange={(e) => setSettings({ ...settings, allow_outside_repo: e.target.checked })}
            />
            Allow file access outside repo
          </label>
        </div>
      </PixelPanel>

      <PixelPanel title="Appearance" style={{ marginTop: '1rem' }}>
        <p style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginBottom: '0.75rem' }}>
          Choose dark mission-control mode or a premium light workstation theme.
        </p>
        <ThemeToggle />
      </PixelPanel>

      <PixelPanel title="Interface Settings" className="" style={{ marginTop: '1rem' }}>
        <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: '0.5rem' }}>
          <p>All games enabled by default. Disable specific games from the game selector during a run.</p>
        </div>
      </PixelPanel>

      <div style={{ marginTop: '1rem', display: 'flex', gap: '1rem', alignItems: 'center' }}>
        <PixelButton onClick={handleSave}>Save Settings</PixelButton>
        {saved && <span style={{ color: 'var(--accent-green)', fontSize: '0.85rem' }}>Saved!</span>}
      </div>
    </div>
  )
}
