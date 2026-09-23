// One poller for service status, shared by every consumer.
//
// The header and the landing page both need this. Polling independently meant
// two loops hitting /ready on every page, and neither backed off when the
// service was unreachable - so an outage produced a steady stream of failing
// requests from every open tab.
//
// This keeps a single interval alive while at least one component is mounted,
// slows down once the answer stops changing, and pauses entirely when the tab
// is hidden.

import { useEffect, useState } from 'react'
import { getReady } from './api.js'

const FAST_MS = 5000     // while starting up, the answer changes soon
const SLOW_MS = 45000    // once settled, this is just a heartbeat

let current = null
let timer = null
const listeners = new Set()

function publish(next) {
  current = next
  listeners.forEach((fn) => fn(next))
}

function schedule() {
  clearTimeout(timer)
  if (!listeners.size) return
  // Only poll fast while the answer is still expected to change.
  const wait = current?.ready ? SLOW_MS : FAST_MS
  timer = setTimeout(run, wait)
}

async function run() {
  if (document.visibilityState === 'hidden') return schedule()
  publish(await getReady())
  schedule()
}

function subscribe(fn) {
  listeners.add(fn)
  if (current) fn(current)
  if (listeners.size === 1) run()
  return () => {
    listeners.delete(fn)
    if (!listeners.size) clearTimeout(timer)
  }
}

if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && listeners.size) run()
  })
}

export function useServiceStatus() {
  const [status, setStatus] = useState(current)
  useEffect(() => subscribe(setStatus), [])
  return status || { ready: false, status: 'checking' }
}
