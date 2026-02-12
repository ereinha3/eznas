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

// Get auth token from localStorage
function getAuthToken(): string | null {
  return localStorage.getItem('nas_orchestrator_token');
}

// Build headers with optional auth
function buildHeaders(contentType: boolean = true): Record<string, string> {
  const headers: Record<string, string> = {};
  if (contentType) {
    headers['Content-Type'] = 'application/json';
  }
  const token = getAuthToken();
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  return headers;
}

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
    headers: buildHeaders(),
    body: JSON.stringify(config),
  })
  return handleResponse(response)
}

export async function validateConfig(config: StackConfig): Promise<ValidationResult> {
  const response = await fetch('/api/validate', {
    method: 'POST',
    headers: buildHeaders(),
    body: JSON.stringify(config),
  })
  return handleResponse(response)
}

export async function renderConfig(config: StackConfig): Promise<RenderResult> {
  const response = await fetch('/api/render', {
    method: 'POST',
    headers: buildHeaders(),
    body: JSON.stringify(config),
  })
  return handleResponse(response)
}

export async function applyConfig(config: StackConfig): Promise<ApplyResponse> {
  const response = await fetch('/api/apply', {
    method: 'POST',
    headers: buildHeaders(),
    body: JSON.stringify(config),
  })
  return handleResponse(response)
}

export async function fetchStatus(): Promise<StatusResponse> {
  const response = await fetch('/api/status', {
    headers: buildHeaders(false),
  })
  if (response.status === 404) {
    return { services: [] }
  }
  return handleResponse(response)
}

export async function fetchServiceCredentials(): Promise<CredentialsResponse> {
  const response = await fetch('/api/secrets', {
    headers: buildHeaders(false),
  })
  return handleResponse(response)
}

export async function updateQbCredentials(payload: { username: string; password: string }): Promise<ServiceCredential> {
  const response = await fetch('/api/services/qbittorrent/credentials', {
    method: 'POST',
    headers: buildHeaders(),
    body: JSON.stringify(payload),
  })
  return handleResponse(response)
}

export async function createJellyfinUser(payload: { username: string; password: string }): Promise<CredentialUser> {
  const response = await fetch('/api/services/jellyfin/users', {
    method: 'POST',
    headers: buildHeaders(),
    body: JSON.stringify(payload),
  })
  return handleResponse(response)
}

export async function fetchHealth(): Promise<HealthResponse> {
  const response = await fetch('/api/health', {
    headers: buildHeaders(false),
  })
  if (!response.ok) throw new Error('Failed to fetch health')
  return response.json()
}

// Indexer management
export async function fetchAvailableIndexers(): Promise<AvailableIndexersResponse> {
  const response = await fetch('/api/indexers/available', {
    headers: buildHeaders(false),
  })
  return handleResponse(response)
}

export async function fetchConfiguredIndexers(): Promise<ConfiguredIndexersResponse> {
  const response = await fetch('/api/indexers', {
    headers: buildHeaders(false),
  })
  return handleResponse(response)
}

export async function addIndexers(indexers: string[]): Promise<AddIndexersResponse> {
  const response = await fetch('/api/indexers', {
    method: 'POST',
    headers: buildHeaders(),
    body: JSON.stringify({ indexers }),
  })
  return handleResponse(response)
}

export async function removeIndexer(indexerId: number): Promise<void> {
  const response = await fetch(`/api/indexers/${indexerId}`, {
    method: 'DELETE',
    headers: buildHeaders(false),
  })
  if (!response.ok) {
    throw new Error('Failed to remove indexer')
  }
}

export async function autoPopulateIndexers(): Promise<AutoPopulateIndexersResponse> {
  const response = await fetch('/api/indexers/auto-populate', {
    method: 'POST',
    headers: buildHeaders(),
  })
  return handleResponse(response)
}

// Authentication API

export interface LoginResponse {
  success: boolean;
  token?: string;
  username?: string;
  role?: string;
  message?: string;
}

export interface SessionResponse {
  valid: boolean;
  username?: string;
  role?: string;
  sudo_active?: boolean;
}

export interface SetupStatus {
  needs_setup: boolean;
  has_config: boolean;
}

export async function fetchSetupStatus(): Promise<SetupStatus> {
  const response = await fetch('/api/setup/status')
  return handleResponse(response)
}

export async function login(username: string, password: string): Promise<LoginResponse> {
  const response = await fetch('/api/auth/login', {
    method: 'POST',
    headers: buildHeaders(),
    body: JSON.stringify({ username, password }),
  })
  return handleResponse(response)
}

export async function logout(): Promise<void> {
  const response = await fetch('/api/auth/logout', {
    method: 'POST',
    headers: buildHeaders(false),
  })
  if (!response.ok) {
    throw new Error('Logout failed')
  }
}

export async function fetchSession(): Promise<SessionResponse> {
  const response = await fetch('/api/auth/session', {
    headers: buildHeaders(false),
  })
  return handleResponse(response)
}

export async function verifySudo(password: string): Promise<{ success: boolean; message: string }> {
  const response = await fetch('/api/auth/sudo', {
    method: 'POST',
    headers: buildHeaders(),
    body: JSON.stringify({ password }),
  })
  return handleResponse(response)
}

export interface User {
  username: string;
  role: string;
  created_at: string;
}

export async function fetchUsers(): Promise<{ users: User[] }> {
  const response = await fetch('/api/auth/users', {
    headers: buildHeaders(false),
  })
  return handleResponse(response)
}

export async function createUser(username: string, password: string, role: string = 'viewer'): Promise<User> {
  const response = await fetch('/api/auth/users', {
    method: 'POST',
    headers: buildHeaders(),
    body: JSON.stringify({ username, password, role }),
  })
  return handleResponse(response)
}

export async function deleteUser(username: string): Promise<void> {
  const response = await fetch(`/api/auth/users/${username}`, {
    method: 'DELETE',
    headers: buildHeaders(false),
  })
  if (!response.ok) {
    throw new Error('Failed to delete user')
  }
}

export async function changePassword(currentPassword: string, newPassword: string): Promise<{ success: boolean; message: string }> {
  const response = await fetch('/api/auth/password', {
    method: 'POST',
    headers: buildHeaders(),
    body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }),
  })
  return handleResponse(response)
}

// Setup and Initialization API

export interface Volume {
  device: string;
  mountpoint: string;
  size: string;
  available: string;
  filesystem: string;
  suggested_paths: {
    media: string;
    downloads: string;
    appdata: string;
  };
}

export interface VolumesResponse {
  volumes: Volume[];
}

export interface InitializeRequest {
  admin_username: string;
  admin_password: string;
  pool_path: string;
  scratch_path?: string;
  appdata_path: string;
}

export interface InitializeResponse {
  success: boolean;
  message: string;
  config_created: boolean;
}

export async function fetchVolumes(): Promise<VolumesResponse> {
  const response = await fetch('/api/system/volumes', {
    headers: buildHeaders(false),
  })
  return handleResponse(response)
}

export async function validatePath(path: string, requireWritable: boolean = true): Promise<{
  valid: boolean;
  exists: boolean;
  writable: boolean;
  error: string | null;
}> {
  const response = await fetch('/api/system/validate-path', {
    method: 'POST',
    headers: buildHeaders(),
    body: JSON.stringify({ path, require_writable: requireWritable }),
  })
  return handleResponse(response)
}

export async function initializeSystem(request: InitializeRequest): Promise<InitializeResponse> {
  const response = await fetch('/api/setup/initialize', {
    method: 'POST',
    headers: buildHeaders(),
    body: JSON.stringify(request),
  })
  return handleResponse(response)
}
