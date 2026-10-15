import { useCallback, useEffect, useRef, useState } from 'react'
import { getAccessToken, openLiveTranscription } from '../api'

const UNAVAILABLE = "Voice input isn't available right now — you can still type."
const MIC_BLOCKED = 'Microphone access was blocked. You can still type your question.'
const RECORDER_FAILED =
  'Recording could not start in this browser. You can still type your question.'
/** How often the recorder hands over audio. Small enough to feel live. */
const CHUNK_MS = 250

/**
 * Live voice input: mic -> backend socket -> Deepgram, text back as it is heard.
 *
 * `liveText` is the whole transcript so far and is replaced on every update.
 * `onDone({ text, confidence, clarifications })` runs at most once per session: when the
 * backend says `done`, or - if the connection fails after words were heard -
 * with those words, so nothing the student saw is lost.
 *
 * Carried over from the record-then-upload hook's review fixes:
 * - `mountedRef`: a panel closed while the permission prompt is open must not
 *   start recording afterwards.
 * - `startingRef`: a double click cannot open two sessions.
 * - Tracks are released on every ending, including unmount, so the browser's
 *   recording indicator always goes away.
 */
export function useLiveTranscription({ onDone }) {
  const supported =
    typeof window !== 'undefined' &&
    typeof window.MediaRecorder !== 'undefined' &&
    typeof window.WebSocket !== 'undefined' &&
    Boolean(navigator.mediaDevices?.getUserMedia)

  const [listening, setListening] = useState(false)
  const [liveText, setLiveText] = useState('')
  const [error, setError] = useState(null)
  const socketRef = useRef(null)
  const recorderRef = useRef(null)
  const streamRef = useRef(null)
  const liveTextRef = useRef('')
  const startingRef = useRef(false)
  const mountedRef = useRef(true)
  const onDoneRef = useRef(onDone)

  useEffect(() => {
    onDoneRef.current = onDone
  }, [onDone])

  const release = useCallback(() => {
    const recorder = recorderRef.current
    recorderRef.current = null
    if (recorder) {
      recorder.ondataavailable = null
      if (recorder.state !== 'inactive') recorder.stop()
    }
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    const socket = socketRef.current
    socketRef.current = null
    if (socket) {
      socket.onopen = null
      socket.onmessage = null
      socket.onclose = null
      socket.onerror = null
      if (socket.readyState === WebSocket.CONNECTING || socket.readyState === WebSocket.OPEN) {
        socket.close()
      }
    }
    liveTextRef.current = ''
    if (mountedRef.current) {
      setListening(false)
      setLiveText('')
    }
  }, [])

  /** End the session. `result` from `done`; otherwise keep any heard text. */
  const finish = useCallback(
    (result, message) => {
      const heard = liveTextRef.current
      release()
      if (!mountedRef.current) return
      if (message) setError(message)
      if (result) onDoneRef.current?.(result)
      else if (heard) onDoneRef.current?.({ text: heard, confidence: 0 })
    },
    [release],
  )

  const beginRecording = useCallback(
    (socket, stream) => {
      try {
        const recorder = new MediaRecorder(stream)
        recorder.ondataavailable = (event) => {
          if (event.data.size > 0 && socket.readyState === WebSocket.OPEN) socket.send(event.data)
        }
        recorderRef.current = recorder
        recorder.start(CHUNK_MS)
      } catch {
        finish(null, RECORDER_FAILED)
      }
    },
    [finish],
  )

  const start = useCallback(async () => {
    if (!supported || socketRef.current || startingRef.current) return
    startingRef.current = true
    setError(null)
    let stream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      startingRef.current = false
      setError(MIC_BLOCKED)
      return
    }
    startingRef.current = false
    if (!mountedRef.current) {
      stream.getTracks().forEach((track) => track.stop())
      return
    }
    streamRef.current = stream

    let socket
    try {
      socket = openLiveTranscription()
    } catch {
      finish(null, UNAVAILABLE)
      return
    }
    socketRef.current = socket
    setListening(true)

    socket.onopen = () => {
      socket.send(JSON.stringify({ type: 'start', token: getAccessToken() }))
    }
    socket.onmessage = (event) => {
      let message
      try {
        message = JSON.parse(event.data)
      } catch {
        return
      }
      if (message.type === 'ready') beginRecording(socket, stream)
      else if (message.type === 'transcript') {
        liveTextRef.current = message.text
        setLiveText(message.text)
      } else if (message.type === 'done') {
        finish(
          {
            text: message.text,
            confidence: message.confidence,
            clarifications: message.clarifications ?? [],
          },
          null,
        )
      } else if (message.type === 'error') finish(null, UNAVAILABLE)
    }
    // Closing without `done` - server restart, network drop, error.
    socket.onclose = () => finish(null, UNAVAILABLE)
  }, [supported, finish, beginRecording])

  const stop = useCallback(() => {
    const socket = socketRef.current
    if (!socket) return
    if (socket.readyState === WebSocket.OPEN && recorderRef.current) {
      socket.send(JSON.stringify({ type: 'stop' }))
    } else {
      finish(null, null) // not recording yet: cancel outright
    }
  }, [finish])

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      release()
    }
  }, [release])

  return { supported, listening, liveText, error, start, stop }
}
