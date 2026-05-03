// HTTP + SSE client for the FastAPI backend.
// - Base URL is same-origin (prod: StaticFiles served by FastAPI; dev: Vite proxy).
// - `api.request` is a thin JSON fetch wrapper.
// - `api.sse` opens an EventSource and wires named events defined in plan §4.2
//   (`progress`, `complete`, `error`, `prompt_ready`, `ai_waiting`).

const JSON_HEADERS = { 'content-type': 'application/json' }

async function parseError(res) {
  const ct = res.headers.get('content-type') || ''
  if (ct.includes('application/json')) {
    try {
      const body = await res.json()
      const message =
        body?.error || body?.detail || body?.message || res.statusText
      const err = new Error(message)
      err.status = res.status
      err.body = body
      return err
    } catch {
      // fallthrough
    }
  }
  const err = new Error(res.statusText || `HTTP ${res.status}`)
  err.status = res.status
  return err
}

async function request(path, init = {}) {
  const res = await fetch(path, init)
  if (!res.ok) throw await parseError(res)
  if (res.status === 204) return null
  const lenHeader = res.headers.get('content-length')
  if (lenHeader === '0') return null
  const ct = res.headers.get('content-type') || ''
  if (ct.includes('application/json')) return res.json()
  return res.text()
}

function buildUrl(path, params) {
  if (!params) return path
  const usp = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null) continue
    usp.append(k, String(v))
  }
  const qs = usp.toString()
  return qs ? `${path}?${qs}` : path
}

// SSE helper.
// Returns a disposer function. Call it to close the EventSource.
// handlers: { progress, complete, error, prompt_ready, ai_waiting, open }
function sse(path, handlers = {}) {
  const es = new EventSource(path)
  const bind = (name, fn) => {
    if (typeof fn === 'function') es.addEventListener(name, fn)
  }
  if (handlers.open) es.addEventListener('open', handlers.open)
  bind('progress', handlers.progress)
  bind('complete', handlers.complete)
  bind('error', handlers.error)
  bind('prompt_ready', handlers.prompt_ready)
  bind('ai_waiting', handlers.ai_waiting)
  // Low-level transport errors (network, server close) surface on onerror.
  es.onerror = (evt) => {
    if (handlers.transportError) handlers.transportError(evt)
  }
  return () => es.close()
}

// POST-initiated SSE helper.
// EventSource only supports GET; the backend's /transcribe, /summarize,
// /models/download endpoints return text/event-stream from a POST. This parses
// the stream body manually and dispatches named events to `handlers`.
// Returns a disposer function that aborts the in-flight request.
function postSse(path, body, handlers = {}) {
  const controller = new AbortController()
  const call = (name, ...args) => {
    const fn = handlers[name]
    if (typeof fn === 'function') fn(...args)
  }

  ;(async () => {
    let res
    try {
      res = await fetch(path, {
        method: 'POST',
        headers: body === undefined ? {} : JSON_HEADERS,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      })
    } catch (err) {
      if (err?.name !== 'AbortError') call('transportError', err)
      return
    }
    if (!res.ok || !res.body) {
      const err = await parseError(res)
      call('transportError', err)
      return
    }
    call('open')
    const reader = res.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buf = ''
    // eslint-disable-next-line no-constant-condition
    while (true) {
      let chunk
      try {
        chunk = await reader.read()
      } catch (err) {
        if (err?.name !== 'AbortError') call('transportError', err)
        return
      }
      if (chunk.done) return
      buf += decoder.decode(chunk.value, { stream: true })
      // SSE separates events with a blank line (`\n\n`).
      let sep = buf.indexOf('\n\n')
      while (sep !== -1) {
        const raw = buf.slice(0, sep)
        buf = buf.slice(sep + 2)
        sep = buf.indexOf('\n\n')
        // Parse `event:` + `data:` lines; skip comments (`:` prefix).
        let eventName = 'message'
        const dataLines = []
        for (const line of raw.split('\n')) {
          if (!line || line.startsWith(':')) continue
          if (line.startsWith('event:')) eventName = line.slice(6).trim()
          else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
        }
        if (dataLines.length === 0) continue
        let payload
        try {
          payload = JSON.parse(dataLines.join('\n'))
        } catch {
          payload = { raw: dataLines.join('\n') }
        }
        const evt = { data: dataLines.join('\n'), payload }
        call(eventName, evt)
      }
    }
  })()

  return () => controller.abort()
}

// -------- endpoint helpers --------

// Plan §4.2 GET /api/system/info
const getSystemInfo = () => request('/api/system/info')

// Plan §4.2 Notes
const getNote = (id) => request(`/api/notes/${encodeURIComponent(id)}`)
const deleteNote = (id, { deleteAudio = false } = {}) =>
  request(
    buildUrl(`/api/notes/${encodeURIComponent(id)}`, {
      deleteAudio: deleteAudio || undefined,
    }),
    { method: 'DELETE' },
  )

// Download note transcript as .md file (Wave 1 endpoint)
const downloadNote = (id) => {
  const a = document.createElement('a')
  a.href = `/api/notes/${encodeURIComponent(id)}/download`
  a.download = ''
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}

// Plan §4.2 Recordings (AC-7 / Phase F).
const createRecording = (body = {}) =>
  request('/api/recordings', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
  })
const postRecordingChunk = (id, chunk, seq) => {
  const fd = new FormData()
  fd.append('chunk', chunk, `chunk-${seq}.webm`)
  fd.append('seq', String(seq))
  return request(`/api/recordings/${encodeURIComponent(id)}/chunk`, {
    method: 'POST',
    body: fd,
  })
}
// finalizeRecording supports two calling conventions:
//   Legacy (no handlers):  finalizeRecording(id, body) → Promise<response>
//   SSE-aware (handlers):  finalizeRecording(id, body, handlers) → disposer fn
// lnv.14 will swap Recording.jsx to the SSE-aware form; until then both work.
const finalizeRecording = (id, body = {}, handlers = undefined) => {
  if (handlers !== undefined) {
    return postSse(
      `/api/recordings/${encodeURIComponent(id)}/finalize`,
      body,
      handlers,
    )
  }
  return request(`/api/recordings/${encodeURIComponent(id)}/finalize`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(body),
  })
}

// Transcript SSE helper for real-time transcription display (Phase 3).
// Opens a GET EventSource to the per-recording transcript-stream endpoint.
// handlers: { onChunk?(data), onEnd?(data), onGroqError?(data), onError?(e) }
// Returns a dispose function: () => es.close()
export function transcriptStream(recordingId, handlers = {}) {
  const es = new EventSource(
    `/api/recordings/${encodeURIComponent(recordingId)}/transcript-stream`,
  )

  const parseEvent = (ev) => {
    try { return JSON.parse(ev.data) } catch { return ev.data }
  }

  es.addEventListener('chunk_transcribed', (ev) => {
    if (typeof handlers.onChunk === 'function') handlers.onChunk(parseEvent(ev))
  })
  es.addEventListener('stream_end', (ev) => {
    if (typeof handlers.onEnd === 'function') handlers.onEnd(parseEvent(ev))
  })
  es.addEventListener('groq_error', (ev) => {
    if (typeof handlers.onGroqError === 'function') handlers.onGroqError(parseEvent(ev))
  })
  es.onerror = (e) => {
    if (typeof handlers.onError === 'function') handlers.onError(e)
  }
  return () => es.close()
}

export const api = {
  request,
  sse,
  postSse,
  getSystemInfo,
  getNote,
  deleteNote,
  downloadNote,
  createRecording,
  postRecordingChunk,
  finalizeRecording,
}

export default api
