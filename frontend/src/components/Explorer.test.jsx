import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import Explorer from './Explorer'
import { getUnlocks, simulateCourses } from '../api'

vi.mock('../api', () => ({ getUnlocks: vi.fn(), simulateCourses: vi.fn() }))

const SIM = {
  before: { credits_earned: '23.00', credits_remaining: '97.00', credit_progress_percent: 19.2 },
  after: { credits_earned: '27.00', credits_remaining: '93.00', credit_progress_percent: 22.5 },
  applied: [{ code: 'COSC220', title: 'Data Structures', credits: '4' }],
  skipped: [],
  changed_blocks: [{ block_id: 'major', name: 'Major required courses' }],
  newly_satisfied_blocks: [],
  newly_unlocked: [{ code: 'COSC352', title: 'Programming Languages' }],
  still_blocked: [],
  warnings: [],
}

beforeEach(() => {
  getUnlocks.mockReset()
  simulateCourses.mockReset()
})

describe('Explorer', () => {
  it('asks for a transcript when there is no record', async () => {
    const user = userEvent.setup()
    const onGoToUpload = vi.fn()
    render(<Explorer hasRecord={false} onGoToUpload={onGoToUpload} />)
    expect(screen.getByText('No confirmed coursework yet')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Upload a transcript' }))
    expect(onGoToUpload).toHaveBeenCalled()
  })

  it('normalises typed codes and shows the simulated before and after', async () => {
    const user = userEvent.setup()
    simulateCourses.mockResolvedValue(SIM)
    render(<Explorer hasRecord onGoToUpload={() => {}} />)

    await user.type(screen.getByLabelText('Courses to try'), 'cosc 220, COSC-220')
    expect(screen.getByText('Trying: COSC220')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Run what-if' }))

    expect(simulateCourses).toHaveBeenCalledWith({ courses: ['COSC220'], mode: 'same_term' })
    expect(await screen.findByText('This is a simulation')).toBeInTheDocument()
    expect(screen.getByText('COSC220 · 4 cr')).toBeInTheDocument()
    expect(screen.getByText('Major required courses')).toBeInTheDocument()
    expect(screen.getByText('COSC352')).toBeInTheDocument()
    expect(screen.getAllByText('Computed by the engine').length).toBeGreaterThan(0)
  })

  it('reports a what-if failure', async () => {
    const user = userEvent.setup()
    simulateCourses.mockRejectedValue(new Error('Unknown course XYZ999'))
    render(<Explorer hasRecord onGoToUpload={() => {}} />)
    await user.type(screen.getByLabelText('Courses to try'), 'XYZ 999')
    await user.click(screen.getByRole('button', { name: 'Run what-if' }))
    expect(await screen.findByText('Could not run the what-if')).toBeInTheDocument()
    expect(screen.getByText('Unknown course XYZ999')).toBeInTheDocument()
  })

  it('refuses to guess about a course outside the catalog', async () => {
    const user = userEvent.setup()
    getUnlocks.mockResolvedValue({ code: 'ABCD123', known: false })
    render(<Explorer hasRecord onGoToUpload={() => {}} />)
    await user.type(screen.getByLabelText('Course'), 'ABCD 123')
    await user.click(screen.getByRole('button', { name: 'Explore' }))
    expect(getUnlocks).toHaveBeenCalledWith('ABCD123')
    expect(await screen.findByText('ABCD123 is not in the encoded catalog')).toBeInTheDocument()
  })

  it('shows what a course needs and unlocks', async () => {
    const user = userEvent.setup()
    getUnlocks.mockResolvedValue({
      code: 'COSC220',
      known: true,
      course: { code: 'COSC220', title: 'Data Structures' },
      status: 'eligible',
      prerequisite_tree: {
        code: 'COSC220',
        title: 'Data Structures',
        children: [{ code: 'COSC112', title: 'Intro II', status: 'completed', children: [] }],
      },
      direct_unlocks: [
        {
          course: { code: 'COSC352', title: 'Programming Languages' },
          status: 'blocked',
          missing_prerequisites: ['COSC220'],
          missing_after: [],
          depth: 1,
          path: ['COSC220', 'COSC352'],
        },
      ],
      downstream_unlocks: [],
    })
    render(<Explorer hasRecord onGoToUpload={() => {}} />)
    await user.type(screen.getByLabelText('Course'), 'COSC 220')
    await user.click(screen.getByRole('button', { name: 'Explore' }))

    expect(await screen.findByText('COSC220 · Data Structures')).toBeInTheDocument()
    expect(screen.getByRole('tree', { name: 'Prerequisite tree' })).toHaveTextContent('COSC112')
    expect(screen.getByText('this course is all you are missing')).toBeInTheDocument()
  })
})
