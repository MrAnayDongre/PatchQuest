import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { ThemeProvider } from './theme/ThemeProvider'
import './styles/themes.css'
import './styles/globals.css'
import './styles/lumina.css'
import './styles/arcade-theme.css'
import './styles/layout.css'
import './styles/games.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeProvider>
      <App />
    </ThemeProvider>
  </React.StrictMode>,
)
