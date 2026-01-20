import { ApplyLog } from './ApplyLog'
import { CredentialsPanel } from './CredentialsPanel'
import { IndexerPanel } from './IndexerPanel'
import { SummaryPanel } from './SummaryPanel'
import type { CredentialsResponse, HealthResponse, ServiceStatus, StackConfig } from './types'

interface SidebarProps {
  config: StackConfig
  serviceStatus: ServiceStatus[]
  health: HealthResponse | null
  credentials: CredentialsResponse | null
  credentialsLoading: boolean
  logEntries: string[]
  onRefreshCredentials: () => Promise<void>
  onUpdateQb: (username: string, password: string) => Promise<void>
  onAddJellyfinUser: (username: string, password: string) => Promise<void>
}

export function Sidebar({
  config,
  serviceStatus,
  health,
  credentials,
  credentialsLoading,
  logEntries,
  onRefreshCredentials,
  onUpdateQb,
  onAddJellyfinUser,
}: SidebarProps) {
  return (
    <aside className="sidebar">
      <CredentialsPanel
        credentials={credentials}
        loading={credentialsLoading}
        onRefresh={onRefreshCredentials}
        onUpdateQb={onUpdateQb}
        onAddJellyfinUser={onAddJellyfinUser}
      />
      <IndexerPanel prowlarrEnabled={config.services.prowlarr.enabled} />
      <SummaryPanel config={config} serviceStatus={serviceStatus} health={health} />
      <ApplyLog entries={logEntries} />
    </aside>
  )
}
