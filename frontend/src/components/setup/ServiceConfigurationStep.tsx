import React, { useState, useEffect } from 'react';
import { useValidation } from '../../hooks/useValidation';

interface ServiceConfig {
  qbittorrent: {
    host: string;
    web_port: number;
    username?: string;
    password?: string;
  };
  prowlarr: {
    host: string;
    port: number;
    api_key: string;
  };
  radarr: {
    host: string;
    port: number;
    api_key: string;
    root_folder: string;
  };
  sonarr: {
    host: string;
    port: number;
    api_key: string;
    root_folder: string;
  };
  jellyfin: {
    host: string;
    port: number;
  };
  jellyseerr: {
    host: string;
    port: number;
  };
  remux_agent: {
    ffmpeg_path: string;
    language_filters: string[];
  };
}

interface DiscoveredService {
  service: string;
  host: string;
  port: number;
  status: 'online' | 'offline' | 'unknown';
  version?: string;
  auth_required: boolean;
}

interface ServiceConfigurationStepProps {
  config: ServiceConfig;
  onChange: (config: ServiceConfig) => void;
  networkConfig?: Record<string, unknown>; // For host/port defaults
  pathConfig?: Record<string, unknown>; // For root folder defaults
}

export function ServiceConfigurationStep({ 
  config, 
  onChange,
  networkConfig,
  pathConfig 
}: ServiceConfigurationStepProps) {
  const [discoveredServices, setDiscoveredServices] = useState<DiscoveredService[]>([]);
  const [isScanning, setIsScanning] = useState(false);
  const [testingConnection, setTestingConnection] = useState<string | null>(null);
  const [connectionStatus, setConnectionStatus] = useState<Record<string, 'success' | 'error' | 'testing'>>({});
  const [expandedServices, setExpandedServices] = useState<Set<string>>(new Set(['qbittorrent']));
  const [advancedMode, setAdvancedMode] = useState<Record<string, boolean>>({});

  const { debouncedValidate, getFieldValidation } = useValidation();



  // Auto-discover services on component mount
  useEffect(() => {
    discoverServices();
    applyDefaults();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [networkConfig, pathConfig]);

  const discoverServices = async () => {
    setIsScanning(true);
    
    try {
      const response = await fetch('/api/system/discover-services');
      const data = await response.json();
      
      if (data.services) {
        setDiscoveredServices(data.services);
        
        // Auto-populate discovered services
        const updatedConfig = { ...config };
        data.services.forEach((service: DiscoveredService) => {
          if (service.status === 'online' && !updatedConfig[service.service as keyof ServiceConfig]) {
            const serviceKey = service.service as keyof ServiceConfig;
            if (serviceKey === 'qbittorrent') {
              updatedConfig.qbittorrent = {
                host: service.host,
                web_port: service.port,
                username: '',
                password: ''
              };
            } else if (serviceKey === 'prowlarr') {
              updatedConfig.prowlarr = {
                host: service.host,
                port: service.port,
                api_key: ''
              };
            } else if (serviceKey === 'radarr') {
              updatedConfig.radarr = {
                host: service.host,
                port: service.port,
                api_key: '',
                root_folder: (pathConfig?.media_path as string) || '/media/movies'
              };
            } else if (serviceKey === 'sonarr') {
              updatedConfig.sonarr = {
                host: service.host,
                port: service.port,
                api_key: '',
                root_folder: (pathConfig?.media_path as string) || '/media/tv'
              };
            } else if (serviceKey === 'jellyfin') {
              updatedConfig.jellyfin = {
                host: service.host,
                port: service.port
              };
            } else if (serviceKey === 'jellyseerr') {
              updatedConfig.jellyseerr = {
                host: service.host,
                port: service.port
              };
            }
          }
        });
        
        if (JSON.stringify(updatedConfig) !== JSON.stringify(config)) {
          onChange(updatedConfig);
        }
      }
    } finally {
      setIsScanning(false);
    }
  };

  const applyDefaults = () => {
    const defaults = {
      qbittorrent: {
        host: 'localhost',
        web_port: networkConfig?.qbittorrent || 8080,
        username: 'admin',
        password: 'adminadmin'
      },
      prowlarr: {
        host: 'localhost',
        port: networkConfig?.prowlarr || 9696,
        api_key: ''
      },
      radarr: {
        host: 'localhost',
        port: networkConfig?.radarr || 7878,
        api_key: '',
        root_folder: pathConfig?.media_path ? `${pathConfig.media_path}/movies` : '/media/movies'
      },
      sonarr: {
        host: 'localhost',
        port: networkConfig?.sonarr || 8989,
        api_key: '',
        root_folder: pathConfig?.media_path ? `${pathConfig.media_path}/tv` : '/media/tv'
      },
      jellyfin: {
        host: 'localhost',
        port: networkConfig?.jellyfin || 8096
      },
      jellyseerr: {
        host: 'localhost',
        port: networkConfig?.jellyseerr || 5055
      },
      remux_agent: {
        ffmpeg_path: '/usr/bin/ffmpeg',
        language_filters: ['eng', 'en']
      }
    };

    const mergedConfig = { ...defaults, ...config };
    if (JSON.stringify(mergedConfig) !== JSON.stringify(config)) {
      onChange(mergedConfig);
    }
  };

  const testServiceConnection = async (service: string) => {
    setTestingConnection(service);
    setConnectionStatus(prev => ({ ...prev, [service]: 'testing' }));
    
    try {
      const serviceConfig = (config as unknown)[service];
      const testPayload = {
        service,
        config: serviceConfig,
        test_type: 'connection'
      };

      const response = await fetch('/api/setup/test-service', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(testPayload)
      });

      const result = await response.json();
      
      setConnectionStatus(prev => ({ 
        ...prev, 
        [service]: result.success ? 'success' : 'error' 
      }));
      
        if (result.success && result.api_key) {
          // Auto-fill API key if returned by test
          const updatedConfig = { ...config };
          const serviceObj = updatedConfig[service] as Record<string, unknown>;
          updatedConfig[service] = {
            ...serviceObj,
            api_key: result.api_key
          };
          onChange(updatedConfig);
        }
      
      return result.success;
    } catch {
      setConnectionStatus(prev => ({ ...prev, [service]: 'error' }));
      return false;
    } finally {
      setTestingConnection(null);
    }
  };

  const handleServiceChange = (service: keyof ServiceConfig, field: string, value: string | number) => {
    const updatedConfig = { ...config };
    const serviceObj = updatedConfig[service] as Record<string, unknown>;
    serviceObj[field] = value;
    onChange(updatedConfig);
    
    // Validate field
    const validationKey = `${service}.${field}`;
    debouncedValidate(validationKey, value);
  };

  const toggleServiceExpanded = (service: string) => {
    const newExpanded = new Set(expandedServices);
    if (newExpanded.has(service)) {
      newExpanded.delete(service);
    } else {
      newExpanded.add(service);
    }
    setExpandedServices(newExpanded);
  };

  const toggleAdvancedMode = (service: string) => {
    setAdvancedMode(prev => ({ ...prev, [service]: !prev[service] }));
  };

  const getConnectionStatusIcon = (service: string) => {
    const status = connectionStatus[service];
    if (status === 'testing') return '🔄';
    if (status === 'success') return '✅';
    if (status === 'error') return '❌';
    return '❓';
  };

  const getDiscoveredServiceInfo = (service: string) => {
    return discoveredServices.find(s => s.service === service);
  };

  const renderServiceConfig = (serviceKey: keyof ServiceConfig, title: string, icon: string) => {
    const serviceConfig = config[serviceKey] as Record<string, unknown>;
    const isExpanded = expandedServices.has(serviceKey);
    const discovered = getDiscoveredServiceInfo(serviceKey);
    const status = connectionStatus[serviceKey];
    const isAdvanced = advancedMode[serviceKey];

    return (
      <div key={serviceKey} className={`service-card ${isExpanded ? 'expanded' : ''}`}>
        <div 
          className="service-header"
          onClick={() => toggleServiceExpanded(serviceKey)}
        >
          <div className="service-title">
            <span className="service-icon">{icon}</span>
            <h3>{title}</h3>
            {discovered && (
              <span className="discovered-badge">Discovered</span>
            )}
            <span className="connection-status">
              {getConnectionStatusIcon(serviceKey)}
            </span>
          </div>
          <button className="expand-button">
            {isExpanded ? '▼' : '▶'}
          </button>
        </div>

        {isExpanded && (
          <div className="service-content">
            {discovered && (
              <div className="discovered-info">
                <span className="discovered-status">
                  Found at {discovered.host}:{discovered.port}
                  {discovered.version && ` (v${discovered.version})`}
                  {discovered.auth_required && ' - Auth Required'}
                </span>
              </div>
            )}

            {serviceKey === 'qbittorrent' && (
              <div className="service-fields">
                <div className="form-row">
                  <div className="form-group">
                    <label>Host *</label>
                    <input
                      type="text"
                      value={(serviceConfig as { host?: string }).host || ''}
                      onChange={(e) => handleServiceChange('qbittorrent', 'host', e.target.value)}
                      className={getFieldValidation('qbittorrent.host').error ? 'error' : ''}
                    />
                  </div>
                  <div className="form-group">
                    <label>Web Port *</label>
                    <input
                      type="number"
                      value={(serviceConfig as { web_port?: number }).web_port || 8080}
                      onChange={(e) => handleServiceChange('qbittorrent', 'web_port', parseInt(e.target.value))}
                      min="1"
                      max="65535"
                      className={getFieldValidation('qbittorrent.web_port').error ? 'error' : ''}
                    />
                  </div>
                </div>
                
                {isAdvanced && (
                  <div className="form-row">
                    <div className="form-group">
                      <label>Username</label>
                      <input
                        type="text"
                        value={(serviceConfig as { username?: string }).username || ''}
                        onChange={(e) => handleServiceChange('qbittorrent', 'username', e.target.value)}
                      />
                    </div>
                    <div className="form-group">
                      <label>Password</label>
                      <input
                        type="password"
                        value={(serviceConfig as { password?: string }).password || ''}
                        onChange={(e) => handleServiceChange('qbittorrent', 'password', e.target.value)}
                      />
                    </div>
                  </div>
                )}

                <div className="service-actions">
                  <button
                    onClick={() => testServiceConnection('qbittorrent')}
                    disabled={testingConnection === 'qbittorrent'}
                    className={`test-button ${status || 'default'}`}
                  >
                    {testingConnection === 'qbittorrent' ? 'Testing...' : 'Test Connection'}
                  </button>
                  
                  <button
                    onClick={() => toggleAdvancedMode('qbittorrent')}
                    className="advanced-button"
                  >
                    {isAdvanced ? 'Basic' : 'Advanced'}
                  </button>
                </div>
              </div>
            )}

            {(serviceKey === 'prowlarr' || serviceKey === 'radarr' || serviceKey === 'sonarr') && (
              <div className="service-fields">
                <div className="form-row">
                  <div className="form-group">
                    <label>Host *</label>
                    <input
                      type="text"
                      value={serviceConfig.host}
                      onChange={(e) => handleServiceChange(serviceKey, 'host', e.target.value)}
                      className={getFieldValidation(`${serviceKey}.host`).error ? 'error' : ''}
                    />
                  </div>
                  <div className="form-group">
                    <label>Port *</label>
                    <input
                      type="number"
                      value={serviceConfig.port}
                      onChange={(e) => handleServiceChange(serviceKey, 'port', parseInt(e.target.value))}
                      min="1"
                      max="65535"
                      className={getFieldValidation(`${serviceKey}.port`).error ? 'error' : ''}
                    />
                  </div>
                </div>
                
                <div className="form-row">
                  <div className="form-group">
                    <label>API Key *</label>
                    <input
                      type="password"
                      value={(serviceConfig as { api_key?: string }).api_key || ''}
                      onChange={(e) => handleServiceChange(serviceKey, 'api_key', e.target.value)}
                      className={getFieldValidation(`${serviceKey}.api_key`).error ? 'error' : ''}
                    />
                  </div>
                </div>

                {(serviceKey === 'radarr' || serviceKey === 'sonarr') && (
                  <div className="form-row">
                    <div className="form-group">
                      <label>Root Folder *</label>
                      <input
                        type="text"
                        value={(serviceConfig as { root_folder?: string }).root_folder || ''}
                        onChange={(e) => handleServiceChange(serviceKey, 'root_folder', e.target.value)}
                        className={getFieldValidation(`${serviceKey}.root_folder`).error ? 'error' : ''}
                      />
                    </div>
                  </div>
                )}

                <div className="service-actions">
                  <button
                    onClick={() => testServiceConnection(serviceKey)}
                    disabled={testingConnection === serviceKey}
                    className={`test-button ${status || 'default'}`}
                  >
                    {testingConnection === serviceKey ? 'Testing...' : 'Test Connection'}
                  </button>
                </div>
              </div>
            )}

            {(serviceKey === 'jellyfin' || serviceKey === 'jellyseerr') && (
              <div className="service-fields">
                <div className="form-row">
                  <div className="form-group">
                    <label>Host *</label>
                    <input
                      type="text"
                      value={serviceConfig.host}
                      onChange={(e) => handleServiceChange(serviceKey, 'host', e.target.value)}
                      className={getFieldValidation(`${serviceKey}.host`).error ? 'error' : ''}
                    />
                  </div>
                  <div className="form-group">
                    <label>Port *</label>
                    <input
                      type="number"
                      value={serviceConfig.port}
                      onChange={(e) => handleServiceChange(serviceKey, 'port', parseInt(e.target.value))}
                      min="1"
                      max="65535"
                      className={getFieldValidation(`${serviceKey}.port`).error ? 'error' : ''}
                    />
                  </div>
                </div>

                <div className="service-actions">
                  <button
                    onClick={() => testServiceConnection(serviceKey)}
                    disabled={testingConnection === serviceKey}
                    className={`test-button ${status || 'default'}`}
                  >
                    {testingConnection === serviceKey ? 'Testing...' : 'Test Connection'}
                  </button>
                </div>
              </div>
            )}

            {serviceKey === 'remux_agent' && (
              <div className="service-fields">
                <div className="form-row">
                  <div className="form-group">
                    <label>FFmpeg Path *</label>
                    <input
                      type="text"
                      value={serviceConfig.ffmpeg_path}
                      onChange={(e) => handleServiceChange('remux_agent', 'ffmpeg_path', e.target.value)}
                      className={getFieldValidation('remux_agent.ffmpeg_path').error ? 'error' : ''}
                    />
                  </div>
                </div>

                <div className="form-row">
                  <div className="form-group">
                    <label>Language Filters</label>
                    <input
                      type="text"
                      value={serviceConfig.language_filters.join(', ')}
                      onChange={(e) => handleServiceChange('remux_agent', 'language_filters', e.target.value.split(',').map(s => s.trim()))}
                      placeholder="eng, en, spa, es"
                    />
                  </div>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="service-configuration-step">
      <h2>Service Configuration</h2>
      <p>Configure your media services with API keys and connection settings. Auto-discovery will find services running on default ports.</p>
      
      {/* Service Discovery Section */}
      <div className="discovery-section">
        <div className="discovery-header">
          <h3>🔍 Service Discovery</h3>
          <button
            onClick={discoverServices}
            disabled={isScanning}
            className="scan-button"
          >
            {isScanning ? 'Scanning...' : 'Rescan Services'}
          </button>
        </div>
        
        {discoveredServices.length > 0 && (
          <div className="discovered-services">
            {discoveredServices.map((service) => (
              <div key={service.service} className="discovered-item">
                <span className="service-name">{service.service}</span>
                <span className={`status ${service.status}`}>
                  {service.status} {service.version && `(${service.version})`}
                </span>
                <span className="endpoint">{service.host}:{service.port}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Service Configuration Cards */}
      <div className="services-grid">
        {renderServiceConfig('qbittorrent', 'qBittorrent', '🏎️')}
        {renderServiceConfig('prowlarr', 'Prowlarr', '🔍')}
        {renderServiceConfig('radarr', 'Radarr', '🎬')}
        {renderServiceConfig('sonarr', 'Sonarr', '📺')}
        {renderServiceConfig('jellyfin', 'Jellyfin', '🎭')}
        {renderServiceConfig('jellyseerr', 'Jellyseerr', '🎫')}
        {renderServiceConfig('remux_agent', 'Remux Agent', '🔄')}
      </div>

      {/* Configuration Tips */}
      <div className="configuration-tips">
        <div className="tips-box">
          <h4>💡 Configuration Tips</h4>
          <ul>
            <li><strong>Auto-discovery:</strong> Services on default ports are found automatically</li>
            <li><strong>API Keys:</strong> Found in service settings under API/Integration tabs</li>
            <li><strong>Connection Testing:</strong> Verify each service before proceeding</li>
            <li><strong>Dependencies:</strong> Radarr/Sonarr need qBittorrent configured first</li>
            <li><strong>Root Folders:</strong> Should match your media library paths</li>
          </ul>
        </div>
      </div>

      <style jsx>{`
        .service-configuration-step h2 {
          color: #fff;
          margin-bottom: 1.5rem;
        }
        
        .service-configuration-step p {
          color: #aaa;
          margin-bottom: 2rem;
          line-height: 1.6;
        }
        
        .discovery-section {
          background: #2a2a3a;
          border-radius: 12px;
          padding: 1.5rem;
          margin-bottom: 2rem;
        }
        
        .discovery-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 1rem;
        }
        
        .discovery-header h3 {
          color: #5c9ceb;
          margin: 0;
        }
        
        .scan-button {
          background: #5c9ceb;
          color: white;
          border: none;
          padding: 0.5rem 1rem;
          border-radius: 6px;
          cursor: pointer;
          transition: all 0.3s ease;
        }
        
        .scan-button:hover:not(:disabled) {
          background: #4a90e2;
        }
        
        .scan-button:disabled {
          background: #666;
          cursor: not-allowed;
        }
        
        .discovered-services {
          display: grid;
          gap: 0.5rem;
        }
        
        .discovered-item {
          display: flex;
          justify-content: space-between;
          align-items: center;
          background: #1e1e2e;
          padding: 0.75rem;
          border-radius: 6px;
          font-size: 0.9rem;
        }
        
        .service-name {
          color: #5c9ceb;
          font-weight: 500;
        }
        
        .status {
          color: #aaa;
        }
        
        .status.online {
          color: #4caf50;
        }
        
        .status.offline {
          color: #f44336;
        }
        
        .endpoint {
          color: #888;
          font-family: monospace;
        }
        
        .services-grid {
          display: grid;
          gap: 1rem;
          margin-bottom: 2rem;
        }
        
        .service-card {
          background: #2a2a3a;
          border: 2px solid #444;
          border-radius: 12px;
          overflow: hidden;
          transition: all 0.3s ease;
        }
        
        .service-card:hover {
          border-color: #5c9ceb;
        }
        
        .service-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 1rem 1.5rem;
          cursor: pointer;
          user-select: none;
        }
        
        .service-title {
          display: flex;
          align-items: center;
          gap: 0.5rem;
        }
        
        .service-icon {
          font-size: 1.5rem;
        }
        
        .service-title h3 {
          color: #fff;
          margin: 0;
          font-size: 1.1rem;
        }
        
        .discovered-badge {
          background: #4caf50;
          color: white;
          padding: 0.25rem 0.5rem;
          border-radius: 4px;
          font-size: 0.7rem;
          font-weight: 500;
        }
        
        .connection-status {
          font-size: 1.2rem;
          margin-left: 0.5rem;
        }
        
        .expand-button {
          background: none;
          border: none;
          color: #aaa;
          font-size: 1rem;
          cursor: pointer;
          transition: color 0.3s ease;
        }
        
        .expand-button:hover {
          color: #5c9ceb;
        }
        
        .service-content {
          padding: 0 1.5rem 1.5rem;
          border-top: 1px solid #444;
        }
        
        .discovered-info {
          margin-bottom: 1rem;
        }
        
        .discovered-status {
          color: #4caf50;
          font-size: 0.9rem;
          background: rgba(76, 175, 80, 0.1);
          padding: 0.5rem;
          border-radius: 4px;
          display: inline-block;
        }
        
        .service-fields {
          display: flex;
          flex-direction: column;
          gap: 1rem;
        }
        
        .form-row {
          display: grid;
          grid-template-columns: 1fr 1fr;
          gap: 1rem;
        }
        
        .form-group {
          display: flex;
          flex-direction: column;
          gap: 0.5rem;
        }
        
        .form-group label {
          color: #aaa;
          font-size: 0.9rem;
          font-weight: 500;
        }
        
        .form-group input {
          padding: 0.75rem;
          background: #1e1e2e;
          border: 1px solid #444;
          border-radius: 6px;
          color: #fff;
          font-size: 1rem;
        }
        
        .form-group input.error {
          border-color: #f44336;
          background: rgba(244, 67, 54, 0.1);
        }
        
        .form-group input:focus {
          outline: none;
          border-color: #5c9ceb;
        }
        
        .service-actions {
          display: flex;
          gap: 1rem;
          margin-top: 1rem;
        }
        
        .test-button {
          padding: 0.5rem 1rem;
          border-radius: 6px;
          border: none;
          font-size: 0.9rem;
          cursor: pointer;
          transition: all 0.3s ease;
        }
        
        .test-button.default {
          background: #666;
          color: white;
        }
        
        .test-button.success {
          background: #4caf50;
          color: white;
        }
        
        .test-button.error {
          background: #f44336;
          color: white;
        }
        
        .test-button.testing {
          background: #ff9800;
          color: white;
        }
        
        .test-button:hover:not(:disabled) {
          opacity: 0.8;
        }
        
        .test-button:disabled {
          cursor: not-allowed;
          opacity: 0.6;
        }
        
        .advanced-button {
          background: #666;
          color: white;
          border: none;
          padding: 0.5rem 1rem;
          border-radius: 6px;
          font-size: 0.9rem;
          cursor: pointer;
          transition: all 0.3s ease;
        }
        
        .advanced-button:hover {
          background: #555;
        }
        
        .configuration-tips {
          background: rgba(92, 156, 235, 0.1);
          border: 1px solid rgba(92, 156, 235, 0.3);
          border-radius: 8px;
          padding: 1.5rem;
        }
        
        .tips-box h4 {
          color: #5c9ceb;
          margin: 0 0 1rem 0;
        }
        
        .tips-box ul {
          margin: 0;
          padding-left: 1.5rem;
          color: #ccc;
        }
        
        .tips-box li {
          margin-bottom: 0.5rem;
          line-height: 1.4;
        }
        
        @media (max-width: 768px) {
          .form-row {
            grid-template-columns: 1fr;
          }
          
          .discovery-header {
            flex-direction: column;
            gap: 1rem;
            align-items: flex-start;
          }
          
          .service-actions {
            flex-direction: column;
          }
        }
      `}</style>
    </div>
  );
}