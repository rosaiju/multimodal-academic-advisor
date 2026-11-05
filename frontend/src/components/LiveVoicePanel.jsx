import { useState } from 'react'
import { AdvisorTurn } from './AdvisorChat'
import { useVoiceConversation } from './useVoiceConversation'

const LABELS = {
  idle: 'Not listening',
  listening: 'Listening…',
  thinking: 'Checking your record…',
  speaking: 'Speaking — talk to interrupt',
}

/** Talk to the advisor out loud. Every turn is also shown as text, with links. */
export default function LiveVoicePanel() {
  const [handsFree, setHandsFree] = useState(true)
  const voice = useVoiceConversation({ conversationId: 'live', handsFree })
  const active = voice.state !== 'idle'

  if (!voice.supported) {
    return <p className="text-sm text-slate-500">Live voice isn't supported in this browser.</p>
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-4">
        <button
          type="button"
          onClick={active ? voice.end : voice.start}
          className={`rounded-full px-6 py-3 text-sm font-semibold text-white ${
            active ? 'bg-rose-600 hover:bg-rose-700' : 'bg-indigo-600 hover:bg-indigo-700'
          }`}
        >
          {active ? 'End conversation' : handsFree ? 'Start talking' : 'Ask one question'}
        </button>
        <label className="flex items-center gap-2 text-sm text-slate-600">
          <input
            type="checkbox"
            checked={handsFree}
            disabled={active}
            onChange={(event) => setHandsFree(event.target.checked)}
          />
          Hands-free
        </label>
        <span className="text-sm font-medium text-slate-700" aria-live="polite">
          {LABELS[voice.state]}
        </span>
      </div>

      {voice.liveText && voice.state === 'listening' && (
        <p className="text-sm italic text-slate-500">“{voice.liveText}”</p>
      )}
      {voice.error && <p className="text-sm text-amber-700">{voice.error}</p>}

      <div className="max-h-[26rem] space-y-4 overflow-y-auto">
        {voice.turns.map((turn, i) =>
          turn.role === 'user' ? (
            <div key={i} className="flex justify-end">
              <p className="max-w-[85%] rounded-2xl rounded-br-sm bg-slate-900 px-4 py-2.5 text-sm text-white">
                {turn.text}
              </p>
            </div>
          ) : (
            <AdvisorTurn key={i} reply={turn.reply} />
          ),
        )}
      </div>
    </div>
  )
}
