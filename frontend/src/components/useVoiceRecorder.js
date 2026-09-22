import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Records one clip from the microphone.
 *
 * Owns the whole MediaRecorder lifecycle so the chat panel only sees
 * `recording` and a finished Blob. Three things here are load-bearing:
 *
 * - The mic tracks are stopped when a recording ends AND on unmount. Otherwise
 *   the browser's red "recording" indicator stays on after the student is done.
 * - `startingRef` blocks a second start while the permission prompt is open, so
 *   a double-click cannot open two streams.
 * - On unmount `onstop` is detached before stopping, so a transcript can never
 *   arrive for a panel that no longer exists.
 * - `mountedRef` covers the gap while the permission prompt is open: a panel
 *   closed before the student answers must not start recording afterwards.
 */
export function useVoiceRecorder({ maxSeconds = 30, onRecorded }) {
  const supported =
    typeof window !== 'undefined' &&
    typeof window.MediaRecorder !== 'undefined' &&
    Boolean(navigator.mediaDevices?.getUserMedia)

  const [recording, setRecording] = useState(false)
  const [error, setError] = useState(null)
  const recorderRef = useRef(null)
  const streamRef = useRef(null)
  const timerRef = useRef(null)
  const startingRef = useRef(false)
  const mountedRef = useRef(true)
  const onRecordedRef = useRef(onRecorded)

  useEffect(() => {
    onRecordedRef.current = onRecorded
  }, [onRecorded])

  const release = useCallback(() => {
    clearTimeout(timerRef.current)
    timerRef.current = null
    streamRef.current?.getTracks().forEach((track) => track.stop())
    streamRef.current = null
    recorderRef.current = null
  }, [])

  const stop = useCallback(() => {
    const recorder = recorderRef.current
    if (recorder && recorder.state !== 'inactive') recorder.stop()
  }, [])

  const start = useCallback(async () => {
    if (!supported || recorderRef.current || startingRef.current) return
    startingRef.current = true
    setError(null)
    let stream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      startingRef.current = false
      setError('Microphone access was blocked. You can still type your question.')
      return
    }

    startingRef.current = false
    if (!mountedRef.current) {
      stream.getTracks().forEach((track) => track.stop())
      return
    }
    streamRef.current = stream

    try {
      const recorder = new MediaRecorder(stream)
      const chunks = []
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunks.push(event.data)
      }
      recorder.onstop = () => {
        const type = recorder.mimeType || 'audio/webm'
        release()
        setRecording(false)
        const blob = new Blob(chunks, { type })
        if (blob.size > 0) onRecordedRef.current?.(blob)
        else setError("Didn't catch that — try again.")
      }
      recorderRef.current = recorder
      recorder.start()
    } catch {
      release()
      setError('Recording could not start in this browser. You can still type your question.')
      return
    }
    setRecording(true)
    timerRef.current = setTimeout(stop, maxSeconds * 1000)
  }, [supported, maxSeconds, release, stop])

  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      const recorder = recorderRef.current
      if (recorder) {
        recorder.onstop = null
        if (recorder.state !== 'inactive') recorder.stop()
      }
      release()
    }
  }, [release])

  return { supported, recording, error, start, stop }
}
