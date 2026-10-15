import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import UploadStep from './UploadStep'
import { uploadTranscript } from '../api'

vi.mock('../api', () => ({ uploadTranscript: vi.fn() }))

const programs = [
  {
    program_id: 'morgan-cs-bs',
    program: 'B.S. Computer Science',
    institution: 'Morgan State University',
    catalog_year: '2023-2024',
  },
]

function renderUpload() {
  const onExtracted = vi.fn()
  const { container } = render(
    <UploadStep
      programs={programs}
      programId="morgan-cs-bs"
      onProgramChange={vi.fn()}
      health={null}
      onExtracted={onExtracted}
    />,
  )
  // The real input is hidden behind the drop zone, so it has no accessible name.
  const fileInput = container.querySelector('input[type="file"]')
  return { onExtracted, fileInput }
}

const transcript = () =>
  new File(['COSC 111  A  3  FALL 2023'], 'demo_transcript.txt', { type: 'text/plain' })
const readButton = () => screen.getByRole('button', { name: 'Read transcript' })

beforeEach(() => {
  uploadTranscript.mockReset()
})

describe('UploadStep', () => {
  it('keeps "Read transcript" disabled until a file is chosen', async () => {
    const user = userEvent.setup()
    const { fileInput } = renderUpload()

    expect(readButton()).toBeDisabled()

    await user.upload(fileInput, transcript())

    expect(screen.getByText('demo_transcript.txt')).toBeInTheDocument()
    expect(readButton()).toBeEnabled()
    expect(uploadTranscript).not.toHaveBeenCalled()
  })

  it('uploads the chosen file for the selected program and passes on the result', async () => {
    const user = userEvent.setup()
    const result = { stored: false, extraction: { courses: [] } }
    uploadTranscript.mockResolvedValue(result)
    const { fileInput, onExtracted } = renderUpload()
    const file = transcript()

    await user.upload(fileInput, file)
    await user.click(readButton())

    expect(uploadTranscript).toHaveBeenCalledWith(file, 'morgan-cs-bs')
    expect(onExtracted).toHaveBeenCalledWith(result)
  })

  it('shows the error when the file cannot be read', async () => {
    const user = userEvent.setup()
    uploadTranscript.mockRejectedValue(new Error('Unsupported file type'))
    const { fileInput, onExtracted } = renderUpload()

    await user.upload(fileInput, transcript())
    await user.click(readButton())

    expect(await screen.findByText('Unsupported file type')).toBeInTheDocument()
    expect(screen.getByText('Could not read that file')).toBeInTheDocument()
    expect(onExtracted).not.toHaveBeenCalled()
  })
})
