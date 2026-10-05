import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import { installAuthFetch } from './api/auth'
import { ThemeProvider } from './theme/ThemeProvider'
import './design/tokens.css'
import './design/base.css'
import './design/components.css'
import './app/shell.css'

installAuthFetch()

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <ThemeProvider>
      <App />
    </ThemeProvider>
  </React.StrictMode>,
)
