import type {
  ApplyResponse,
  RenderResult,
  StackConfig,
  StatusResponse,
  ValidationResult,
} from './components/types'

const JSON_HEADERS = { 'Content-Type': 'application/json' }

async function handleResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const text = await response.text()
    throw new Error(text || response.statusText)
  }
  return response.json() as Promise<T>
}

export async function loadConfig(): Promise<StackConfig> {
  const response = await fetch('/api/config')
  return handleResponse(response)
}

export async function saveConfig(config: StackConfig): Promise<StackConfig> {
  const response = await fetch('/api/config', {
    method: 'PUT',
    headers: JSON_HEADERS,
    body: JSON.stringify(config),
  })
  return handleResponse(response)
}

export async function validateConfig(config: StackConfig): Promise<ValidationResult> {
  const response = await fetch('/api/validate', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(config),
  })
  return handleResponse(response)
}

export async function renderConfig(config: StackConfig): Promise<RenderResult> {
  const response = await fetch('/api/render', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(config),
  })
  return handleResponse(response)
}

export async function applyConfig(config: StackConfig): Promise<ApplyResponse> {
  const response = await fetch('/api/apply', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(config),
  })
  return handleResponse(response)
}

export async function fetchStatus(): Promise<StatusResponse> {
  const response = await fetch('/api/status')
  if (response.status === 404) {
    return { services: [] }
  }
  return handleResponse(response)
}
