import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { AdvisorTurn } from './AdvisorChat'

vi.mock('../api', () => ({}))

const reply = {
  answer: 'I cannot answer that.',
  source: 'engine',
  grounded: true,
  citations: [],
  links: [{ name: "the Registrar's Office", url: 'https://www.morgan.edu/registrar' }],
}

describe('AdvisorTurn', () => {
  it('renders curated links as safe anchors', () => {
    render(<AdvisorTurn reply={reply} />)
    const link = screen.getByRole('link', { name: "the Registrar's Office" })
    expect(link).toHaveAttribute('href', 'https://www.morgan.edu/registrar')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noreferrer')
  })

  it('offers read aloud when given onSpeak', async () => {
    const onSpeak = vi.fn()
    render(<AdvisorTurn reply={reply} onSpeak={onSpeak} />)
    await userEvent.click(screen.getByRole('button', { name: 'Read aloud' }))
    expect(onSpeak).toHaveBeenCalled()
  })
})

describe('AdvisorTurn streaming', () => {
  it('waits for the voice before showing the answer', () => {
    render(<AdvisorTurn reply={reply} pending />)
    expect(screen.queryByText('I cannot answer that.')).toBeNull()
    expect(screen.getByText('Getting ready to speak…')).toBeInTheDocument()
  })

  it('types the answer out over revealMs, then shows all of it', async () => {
    render(<AdvisorTurn reply={reply} revealMs={200} />)
    expect(screen.queryByText('I cannot answer that.')).toBeNull()
    expect(await screen.findByText('I cannot answer that.')).toBeInTheDocument()
  })
})

describe('AdvisorTurn markdown', () => {
  it('renders bold and lists instead of showing the symbols', () => {
    const md = { ...reply, links: [], answer: '**You need 105 credits.**\n\n- COSC 220\n- MATH 241' }
    const { container } = render(<AdvisorTurn reply={md} />)
    expect(container.querySelector('strong')).toHaveTextContent('You need 105 credits.')
    expect(container.querySelectorAll('li')).toHaveLength(2)
    expect(container.textContent).not.toContain('**')
  })

  it('keeps engine line breaks and ends a list before a following line', () => {
    const engine = {
      ...reply,
      links: [],
      answer: 'Eligible now:\n- COSC220 (4 cr)\n  Warning: offered irregularly\nNot yet eligible: COSC354.',
    }
    const { container } = render(<AdvisorTurn reply={engine} />)
    expect(container.querySelector('li br')).not.toBeNull()
    expect(container.querySelector('li')).not.toHaveTextContent('Not yet eligible')
  })

  it('never renders raw HTML from an answer', () => {
    const html = { ...reply, links: [], answer: 'Hi <img src=x onerror="alert(1)"> there' }
    const { container } = render(<AdvisorTurn reply={html} />)
    expect(container.querySelector('img')).toBeNull()
  })
})

describe('AdvisorTurn full answer', () => {
  it('shows the full engine answer under a spoken summary', () => {
    const spoken = {
      ...reply,
      links: [],
      source: 'engine+llm',
      answer: 'You need 97 credits; the full list is on your screen.',
      details: 'You still need:\n- COSC220\n- MATH241',
    }
    const { container } = render(<AdvisorTurn reply={spoken} />)
    expect(screen.getByText('Full answer')).toBeInTheDocument()
    expect(container.querySelector('details[open]')).not.toBeNull()
    expect(container.querySelectorAll('details li')).toHaveLength(2)
  })
})
