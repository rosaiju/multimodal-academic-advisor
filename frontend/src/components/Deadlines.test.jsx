import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Deadlines from './Deadlines'
import { downloadDeadlinesCalendar, getDeadlines } from '../api'

vi.mock('../api', () => ({ downloadDeadlinesCalendar: vi.fn(), getDeadlines: vi.fn() }))

const REPORT = {
  standing: { label: 'Sophomores' },
  deadlines: [
    {
      id: 'reg',
      title: 'Spring registration',
      date: '2026-11-10',
      end_date: null,
      state: 'upcoming',
      days_away: 6,
      soon: true,
      is_yours: true,
      detail: 'Your registration window opens.',
      source: 'https://www.morgan.edu/calendar',
    },
    {
      id: 'old',
      title: 'Fall classes begin',
      date: '2026-08-24',
      end_date: null,
      state: 'past',
      days_away: -72,
      soon: false,
      is_yours: false,
      detail: null,
      source: 'https://www.morgan.edu/calendar',
    },
  ],
  not_yet_sourced: [
    {
      title: 'Add/drop deadline',
      where_to_look: 'Check the registrar.',
      source: 'https://www.morgan.edu/registrar',
    },
  ],
  caveats: ['Dates can change.'],
  calendar_retrieved: '2026-10-30',
}

beforeEach(() => {
  getDeadlines.mockReset()
  downloadDeadlinesCalendar.mockReset()
})

describe('Deadlines', () => {
  it('shows a loading state, then the dates with their sources', async () => {
    getDeadlines.mockResolvedValue(REPORT)
    render(<Deadlines studentId="s1" />)

    expect(screen.getByText('Loading your dates…')).toBeInTheDocument()
    expect(await screen.findByText('Spring registration')).toBeInTheDocument()
    expect(getDeadlines).toHaveBeenCalledWith('s1')
    expect(screen.getByText('Registration dates shown for: Sophomores')).toBeInTheDocument()
    expect(screen.getByText('Your group')).toBeInTheDocument()
    expect(screen.getByText('In 6 days')).toBeInTheDocument()
    expect(screen.getByText('Past')).toBeInTheDocument()
    expect(screen.getAllByText('Source on morgan.edu')).toHaveLength(2)
    expect(screen.getByText("Dates we don't have yet")).toBeInTheDocument()
    expect(screen.getByText('Dates can change.')).toBeInTheDocument()
  })

  it('downloads the calendar file and reports a failure without hiding the dates', async () => {
    const user = userEvent.setup()
    getDeadlines.mockResolvedValue(REPORT)
    downloadDeadlinesCalendar.mockRejectedValueOnce(
      new Error('Could not build the calendar file (500).'),
    )
    render(<Deadlines studentId="s1" />)

    await user.click(await screen.findByRole('button', { name: 'Add to my calendar' }))
    expect(downloadDeadlinesCalendar).toHaveBeenCalledWith('s1')
    expect(await screen.findByText("Couldn't build the calendar file")).toBeInTheDocument()
    expect(screen.getByText('Spring registration')).toBeInTheDocument()
  })

  it('shows a friendly error when the dates cannot be loaded', async () => {
    getDeadlines.mockRejectedValue(new Error('Cannot reach the backend.'))
    render(<Deadlines studentId="s1" />)
    expect(await screen.findByText("Couldn't load your dates")).toBeInTheDocument()
    expect(screen.getByText('Cannot reach the backend.')).toBeInTheDocument()
  })
})
