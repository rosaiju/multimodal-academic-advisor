import { useCallback, useEffect, useRef, useState } from 'react'
import { askAdvisor } from '../api'
import { useLiveTranscription } from './useLiveTranscription'
import { useSpeaker } from './useSpeaker'

/** A short reply that confirms a read-back ("Did you mean COSC 241?"). */
const YES = /^\s*(yes|yeah|yep|yup|correct|right|exactly|uh huh)\b/i
/** Real words, not a cough or a stray "a": the bar for interrupting the advisor. */
const REAL_WORDS = /\w{2,}/

/**
 * A spoken conversation with the advisor.
 *
 * Each listening session ends on its own when the student stops talking (the
 * backend ends it on Deepgram's utterance end), so a turn is one session:
 * listening -> thinking (POST /advisor/chat, mode "live") -> speaking.
 *
 * handsFree=false: one turn per start() (push to talk).
 * handsFree=true:  listens again after every answer, and keeps listening WHILE
 *                  the advisor speaks, so real words interrupt it (barge-in).
 *
 * Answers come from the same advisor as typed chat; nothing here writes text a
 * student hears except the backend's checked answer.
 */
export function useVoiceConversation({ conversationId = 'live', handsFree = true } = {}) {
  const [state, setStateRaw] = useState('idle')
  const [turns, setTurns] = useState([])
  const stateRef = useRef('idle')
  const activeRef = useRef(false)
  const turnRef = useRef(0) // bumps on every new turn, barge-in and end: stale work checks it
  const pendingRef = useRef(null) // { question, clarification } awaiting "yes"
  const interruptedRef = useRef(false) // the student talked over the last reply
  const handsFreeRef = useRef(handsFree)
  const liveRef = useRef(null)
  const speaker = useSpeaker()
  const speakerRef = useRef(speaker)

  useEffect(() => {
    handsFreeRef.current = handsFree
    speakerRef.current = speaker
  })

  const setState = useCallback((next) => {
    stateRef.current = next
    setStateRaw(next)
  }, [])

  const listen = useCallback(() => {
    setState('listening')
    liveRef.current?.start()
  }, [setState])

  const handleUtterance = useCallback(
    async (text, confidence) => {
      const turn = ++turnRef.current
      let question = text
      const pending = pendingRef.current
      pendingRef.current = null
      if (pending && YES.test(text) && pending.clarification.candidates.length > 0) {
        const { spoken, candidates } = pending.clarification
        question = pending.question.replace(spoken, candidates[0])
        confidence = 1
      }

      setTurns((all) => [...all, { role: 'user', text }])
      setState('thinking')
      let reply
      try {
        const interrupted = interruptedRef.current
        interruptedRef.current = false
        reply = await askAdvisor({
          message: question,
          conversationId,
          mode: 'live',
          confidence,
          interrupted,
        })
      } catch (error) {
        reply = { answer: `I couldn't reach the advisor: ${error.message}`, source: 'error', links: [] }
      }
      if (turn !== turnRef.current || !activeRef.current) return

      setTurns((all) => [...all, { role: 'advisor', reply }])
      if (reply.clarification) pendingRef.current = { question, clarification: reply.clarification }

      setState('speaking')
      if (handsFreeRef.current) liveRef.current?.start() // listen during playback: barge-in
      // A model summary (engine+llm) is already short; an engine answer is cut down
      // for speech by the backend's deterministic brief pass. The screen shows it all.
      await speakerRef.current.speak(reply.answer, reply.links ?? [], {
        brief: reply.source !== 'engine+llm',
      })
      if (turn !== turnRef.current || !activeRef.current) return // interrupted or ended

      if (handsFreeRef.current) listen()
      else {
        activeRef.current = false
        setState('idle')
      }
    },
    [conversationId, listen, setState],
  )

  const onDone = useCallback(
    ({ text, confidence }) => {
      if (!activeRef.current) return
      // Heard during playback but not enough to interrupt (a cough, "a", echo):
      // not a question. The post-playback listen() starts a fresh session.
      if (stateRef.current === 'speaking' && !REAL_WORDS.test(text ?? '')) return
      if (text?.trim()) {
        handleUtterance(text.trim(), confidence)
        return
      }
      // Silence: nothing to ask. Keep the session going.
      if (stateRef.current === 'listening') {
        if (handsFreeRef.current) listen()
        else {
          activeRef.current = false
          setState('idle')
        }
      }
    },
    [handleUtterance, listen, setState],
  )

  // The mic or socket failed: stop rather than show "Listening…" forever.
  const onFail = useCallback(() => {
    if (!activeRef.current) return
    activeRef.current = false
    turnRef.current++
    speakerRef.current.stop()
    setState('idle')
  }, [setState])

  const live = useLiveTranscription({ onDone, onFail, live: true })
  useEffect(() => {
    liveRef.current = live
  })

  // Barge-in: the student talks over the advisor.
  useEffect(() => {
    if (stateRef.current === 'speaking' && REAL_WORDS.test(live.liveText ?? '')) {
      turnRef.current++
      interruptedRef.current = true // the next question tells the server
      setState('listening')
      speakerRef.current.stop()
    }
  }, [live.liveText, setState])

  const start = useCallback(() => {
    if (activeRef.current) return
    activeRef.current = true
    listen()
  }, [listen])

  const end = useCallback(() => {
    activeRef.current = false
    turnRef.current++
    pendingRef.current = null
    liveRef.current?.stop()
    speakerRef.current.stop()
    setState('idle')
  }, [setState])

  return {
    state,
    turns,
    start,
    end,
    supported: live.supported,
    liveText: live.liveText,
    error: live.error ?? speaker.error,
  }
}
