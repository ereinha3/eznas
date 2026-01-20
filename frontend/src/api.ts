import type {
  AddIndexersResponse,
  ApplyResponse,
  AutoPopulateIndexersResponse,
  AvailableIndexersResponse,
  ConfiguredIndexersResponse,
  RenderResult,
  StackConfig,
  StatusResponse,
  ValidationResult,
  CredentialsResponse,
  ServiceCredential,
  CredentialUser,
  HealthResponse,
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

export async function fetchServiceCredentials(): Promise<CredentialsResponse> {
  const response = await fetch('/api/secrets')
  return handleResponse(response)
}

export async function updateQbCredentials(payload: { username: string; password: string }): Promise<ServiceCredential> {
  const response = await fetch('/api/services/qbittorrent/credentials', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  })
  return handleResponse(response)
}

export async function createJellyfinUser(payload: { username: string; password: string }): Promise<CredentialUser> {
  const response = await fetch('/api/services/jellyfin/users', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify(payload),
  })
  return handleResponse(response)
}

export async function fetchHealth(): Promise<HealthResponse> {
  const response = await fetch('/api/health')
  if (!response.ok) throw new Error('Failed to fetch health')
  return response.json()
}

// Indexer management
export async function fetchAvailableIndexers(): Promise<AvailableIndexersResponse> {
  const response = await fetch('/api/indexers/available')
  return handleResponse(response)
}

export async function fetchConfiguredIndexers(): Promise<ConfiguredIndexersResponse> {
  const response = await fetch('/api/indexers')
  return handleResponse(response)
}

export async function addIndexers(indexers: string[]): Promise<AddIndexersResponse> {
  const response = await fetch('/api/indexers', {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ indexers }),
  })
  return handleResponse(response)
}

export async function removeIndexer(indexerId: number): Promise<void> {
  const response = await fetch(`/api/indexers/${indexerId}`, {
    method: 'DELETE',
  })
  if (!response.ok) {
    throw new Error('Failed to remove indexer')
  }
}

export async function autoPopulateIndexers(): Promise<AutoPopulateIndexersResponse> {
  const response = await fetch('/api/indexers/auto-populate', {
    method: 'POST',
  })
  return handleResponse(response)
}
