import express from 'express'
import { readFile } from 'fs/promises'

function greet(name) {
  return `Hello, ${name}`
}

async function fetchData(url) {
  const res = await fetch(url)
  return res.json()
}

const processItem = (item) => {
  return item.value
}

class EventEmitter {
  emit(event) {
    console.log(event)
  }

  on(event, handler) {
    this.handlers = this.handlers || {}
  }
}

export default function createApp() {
  return express()
}
