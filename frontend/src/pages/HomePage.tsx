import type { CredentialsResponse, HealthResponse, StackConfig } from '../components/types'
import { SERVICE_DISPLAY, SERVICE_ORDER } from '../constants'
import { ServiceLinkDropdown } from '../components/common/ServiceLinkDropdown'
import { ActivityEvent } from '../components/common/ActivityEvent'
import { usePolling } from '../hooks/usePolling'
import { fetchPipelineEvents, fetchStorageUsage, pipelineHealth, enrichmentStatus } from '../api'
import type { PipelineEvent, StoragePool } from '../api'

interface HomePageProps {
  config: StackConfig
  health: HealthResponse | null
  credentials: CredentialsResponse | null
}

export function HomePage({ config, health }: HomePageProps) {
  const { data: recentEvents } = usePolling(() => fetchPipelineEvents(7), 10000)
  const { data: pipelineStatus } = usePolling(pipelineHealth, 10000)
  const { data: storageData } = usePolling(fetchStorageUsage, 30000)
  const { data: enrichData } = usePolling(enrichmentStatus, 15000)

  const enabledServices = SERVICE_ORDER.filter((key) => config.services[key]?.enabled)

  return (
    <div className="home-page">
      {/* KPI Strip */}
      <div className="kpi-strip">
        <KPITile
          label="media storage"
          value={storageData?.pools?.[0] ? `${(storageData.pools[0].free_bytes / (1024**4)).toFixed(1)}` : '--'}
          unit="TB free"
          color="var(--color-accent)"
        />
        <KPITile
          label="services"
          value={String(enabledServices.filter(k => health?.services.find(s => s.name === k)?.healthy).length)}
          unit={`/ ${enabledServices.length} healthy`}
          color="var(--color-success)"
        />
        <KPITile
          label="pipeline"
          value={pipelineStatus?.status === 'running' ? 'active' : pipelineStatus?.status || '--'}
          unit={pipelineStatus?.processed_count ? `${pipelineStatus.processed_count} processed` : ''}
          color="var(--color-accent)"
        />
        <KPITile
          label="enhancer"
          value={enrichData ? String(enrichData.succeeded) : '--'}
          unit={enrichData ? `done · ${enrichData.queue_size} queued` : ''}
          color="var(--color-accent-indigo, #6366f1)"
        />
        <KPITile
          label="this week"
          value={pipelineStatus?.processed_count ? `+${Math.min(pipelineStatus.processed_count, 99)}` : '--'}
          unit="items"
          color="var(--color-accent)"
        />
      </div>

      {/* Main 2-column layout */}
      <div className="home-columns">
        {/* Left: Storage + Activity stacked */}
        <div className="home-col-left">
          <div className="card">
            <h3>Storage</h3>
            {storageData?.pools?.map((pool: StoragePool) => {
              const usedPct = pool.total_bytes > 0 ? Math.round((pool.used_bytes / pool.total_bytes) * 100) : 0
              const totalLabel = pool.total_bytes > 1024**4
                ? `${(pool.total_bytes / (1024**4)).toFixed(1)} TB`
                : `${Math.round(pool.total_bytes / (1024**3))} GB`
              const usedLabel = pool.used_bytes > 1024**4
                ? `${(pool.used_bytes / (1024**4)).toFixed(1)}`
                : `${Math.round(pool.used_bytes / (1024**3))}`
              return (
                <div key={pool.path} className="storage-row">
                  <div className="storage-row-header">
                    <span className="storage-row-label">{pool.label}</span>
                    <span className="storage-row-stats">{usedLabel} / {totalLabel} · {usedPct}%</span>
                  </div>
                  <div className="storage-bar-sm">
                    <div className={`storage-bar-fill ${usedPct > 90 ? 'critical' : usedPct > 75 ? 'warning' : ''}`} style={{ width: `${usedPct}%` }} />
                  </div>
                </div>
              )
            })}
          </div>

          <div className="card">
            <h3>Recent activity</h3>
            <div className="home-activity-feed">
              {!recentEvents?.events?.length ? (
                <p className="text-muted text-sm">No events yet.</p>
              ) : (
                recentEvents.events.map((event: PipelineEvent, idx: number) => (
                  <ActivityEvent key={`${event.ts}-${idx}`} event={event} compact />
                ))
              )}
            </div>
          </div>
        </div>

        {/* Right: Services */}
        <div className="home-col-services card">
          <div className="home-services-header">
            <h3>Services <span className="text-muted">{enabledServices.length} running</span></h3>
          </div>
          <div className="home-services-grid">
            {SERVICE_ORDER.map((key) => {
              const service = config.services[key]
              if (!service?.enabled) return null
              const healthStatus = health?.services.find((h) => h.name === key)
              const display = SERVICE_DISPLAY[key]

              return (
                <div key={key} className="home-service-tile">
                  <span className="home-service-icon">{display.icon}</span>
                  <div className="home-service-info">
                    <span className="home-service-name">{display.name}</span>
                    <span className="home-service-tech">{display.technicalName}</span>
                  </div>
                  <span className={`health-dot ${healthStatus?.healthy === true ? 'healthy' : healthStatus?.healthy === false ? 'unhealthy' : 'unknown'}`} />
                  {service.port && (
                    <ServiceLinkDropdown serviceName={display.technicalName} port={service.port} proxyUrl={service.proxy_url} />
                  )}
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}

function KPITile({ label, value, unit, color }: { label: string; value: string; unit: string; color: string }) {
  return (
    <div className="kpi-tile">
      <span className="kpi-label">{label}</span>
      <div className="kpi-value-row">
        <span className="kpi-value" style={{ color }}>{value}</span>
        <span className="kpi-unit">{unit}</span>
      </div>
    </div>
  )
}
