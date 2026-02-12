import React, { useState, useEffect } from 'react';
import { useValidation } from '../../hooks/useValidation';
import type { ClientValidationRule } from '../../types/validation';

interface NetworkConfig {
  interface: string;
  qbittorrent: number;
  prowlarr: number;
  radarr: number;
  sonarr: number;
  jellyfin: number;
  jellyseerr: number;
}

interface NetworkConfigurationStepProps {
  config: NetworkConfig;
  onChange: (config: NetworkConfig) => void;
  canProceed: boolean;
  onNext: () => void;
}

export function NetworkConfigurationStep({ config, onChange, canProceed, onNext }: NetworkConfigurationStepProps) {
  const [networkInterfaces, setNetworkInterfaces] = useState<Array<{
    name: string;
    ip: string;
    netmask: string;
  }>>([]);
  
  const [portConflicts, setPortConflicts] = useState<Record<string, string>>({});
  const [isLoadingPorts, setIsLoadingPorts] = useState(false);

  const { validateField, debouncedValidate, getFieldValidation, clearValidation } = useValidation();

  // Network validation rules
  const networkValidationRules: Record<string, ClientValidationRule> = {
    qbittorrent_port: {
      field: 'qbittorrent_port',
      type: 'number',
      required: true,
      min_value: 1,
      max_value: 65535,
      custom_rules: ['port_available', 'unique_port']
    },
    prowlarr_port: {
      field: 'prowlarr_port',
      type: 'number',
      required: true,
      min_value: 1,
      max_value: 65535,
      custom_rules: ['port_available', 'unique_port']
    },
    radarr_port: {
      field: 'radarr_port',
      type: 'number',
      required: true,
      min_value: 1,
      max_value: 65535,
      custom_rules: ['port_available', 'unique_port']
    },
    sonarr_port: {
      field: 'sonarr_port',
      type: 'number',
      required: true,
      min_value: 1,
      max_value: 65535,
      custom_rules: ['port_available', 'unique_port']
    },
    jellyfin_port: {
      field: 'jellyfin_port',
      type: 'number',
      required: true,
      min_value: 1,
      max_value: 65535,
      custom_rules: ['port_available', 'unique_port']
    },
    jellyseerr_port: {
      field: 'jellyseerr_port',
      type: 'number',
      required: true,
      min_value: 1,
      max_value: 65535,
      custom_rules: ['port_available', 'unique_port']
    }
  };

  // Load network interfaces on mount
  useEffect(() => {
    loadNetworkInterfaces();
  }, []);

  const loadNetworkInterfaces = async () => {
    try {
      const response = await fetch('/api/system/network-interfaces');
      const data = await response.json();
      setNetworkInterfaces(data.interfaces || []);
    } catch (error) {
      console.error('Failed to load network interfaces:', error);
    }
  };

  // Check for port conflicts
  useEffect(() => {
    checkPortConflicts();
  }, [config.qbittorrent, config.prowlarr, config.radarr, config.sonarr, config.jellyfin, config.jellyseerr]);

  const checkPortConflicts = () => {
    const ports = {
      qbittorrent: config.qbittorrent,
      prowlarr: config.prowlarr,
      radarr: config.radarr,
      sonarr: config.sonarr,
      jellyfin: config.jellyfin,
      jellyseerr: config.jellyseerr
    };

    const conflicts: Record<string, string> = {};
    const usedPorts: Record<number, string[]> = {};

    // Check which ports are used
    Object.entries(ports).forEach(([service, port]) => {
      if (port && port > 0) {
        if (!usedPorts[port]) {
          usedPorts[port] = [];
        }
        usedPorts[port].push(service);
      }
    });

    // Find conflicts
    Object.entries(usedPorts).forEach(([port, services]) => {
      if (services.length > 1) {
        services.forEach((service, index) => {
          if (index === 0) return; // Keep the first service
          conflicts[`${service}_port`] = `Port ${port} is also used by: ${services.slice(1).join(', ')}`;
        });
      }
    });

    setPortConflicts(conflicts);
  };

  const handlePortChange = (service: keyof NetworkConfig, value: number) => {
    const newConfig = { ...config, [service]: value };
    onChange(newConfig);
    debouncedValidate(`${service}_port`, value);
  };

  const handleInterfaceSelect = (interfaceName: string) => {
    const newConfig = { ...config, interface: interfaceName };
    onChange(newConfig);
  };

  const validateAllPorts = async () => {
    setIsLoadingPorts(true);
    
    try {
      const configForValidation = {
        config: {
          qbittorrent: { port: config.qbittorrent },
          prowlarr: { port: config.prowlarr },
          radarr: { port: config.radarr },
          sonarr: { port: config.sonarr },
          jellyfin: { port: config.jellyfin },
          jellyseerr: { port: config.jellyseerr }
        }
      };

      const response = await fetch('/api/setup/verify', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          config: configForValidation,
          partial: false,
          skip_service_checks: true
        })
      });

      const result = await response.json();
      
      if (result.success) {
        // Port validation successful
        return true;
      } else {
        // Handle validation errors
        return false;
      }
    } catch (error) {
      console.error('Port validation failed:', error);
      return false;
    } finally {
      setIsLoadingPorts(false);
    }
  };

  const getPortStatus = (port: number, service: string) => {
    const conflict = portConflicts[`${service}_port`];
    const fieldValidation = getFieldValidation(`${service}_port`);
    
    if (conflict) {
      return {
        status: 'conflict',
        message: conflict,
        className: 'port-conflict'
      };
    }
    
    if (fieldValidation.error) {
      return {
        status: 'error',
        message: fieldValidation.error,
        className: 'port-error'
      };
    }
    
    if (fieldValidation.isValid) {
      return {
        status: 'valid',
        message: 'Available',
        className: 'port-valid'
      };
    }
    
    return {
      status: 'neutral',
      message: 'Not validated',
      className: 'port-neutral'
    };
  };

  const getRecommendedPorts = () => {
    return {
      qbittorrent: 8080,
      prowlarr: 9696,
      radarr: 7878,
      sonarr: 8989,
      jellyfin: 8096,
      jellyseerr: 5055
    };
  };

  const applyRecommendedPorts = () => {
    const recommended = getRecommendedPorts();
    onChange(recommended);
  };

  return (
    <div className="network-configuration-step">
      <h2>Network Configuration</h2>
      <p>Configure network interfaces and ports for your services. Smart conflict detection ensures no overlapping ports.</p>
      
      {/* Network Interface Selection */}
      <div className="network-section">
        <h3>Network Interface</h3>
        <div className="interface-grid">
          {networkInterfaces.map((iface) => (
            <div
              key={iface.name}
              className={`interface-card ${
                config.interface === iface.name ? 'selected' : ''
              }`}
              onClick={() => handleInterfaceSelect(iface.name)}
            >
              <div className="interface-name">{iface.name}</div>
              <div className="interface-details">
                <div className="interface-ip">{iface.ip}</div>
                <div className="interface-netmask">{iface.netmask}</div>
              </div>
            </div>
          ))}
          
          {networkInterfaces.length === 0 && (
            <div className="no-interfaces">
              <p>No network interfaces detected.</p>
            </div>
          )}
        </div>
      </div>
      
      {/* Port Configuration */}
      <div className="port-section">
        <h3>Service Ports</h3>
        
        <div className="port-controls">
          <button
            onClick={applyRecommendedPorts}
            className="recommended-button"
          >
            Use Recommended Ports
          </button>
          
          <button
            onClick={validateAllPorts}
            disabled={isLoadingPorts}
            className={`validate-button ${isLoadingPorts ? 'loading' : ''}`}
          >
            {isLoadingPorts ? 'Validating...' : 'Validate All Ports'}
          </button>
        </div>
        
        <div className="port-grid">
          <div className="port-input-group">
            <label htmlFor="qbittorrent_port">
              qBittorrent
              <span className="port-status">
                {getPortStatus(config.qbittorrent, 'qbittorrent').status === 'valid' && '✓'}
                {getPortStatus(config.qbittorrent, 'qbittorrent').message}
              </span>
            </label>
            <input
              id="qbittorrent_port"
              type="number"
              value={config.qbittorrent}
              onChange={(e) => handlePortChange('qbittorrent', parseInt(e.target.value))}
              min="1"
              max="65535"
              placeholder="8080"
              className={
                getPortStatus(config.qbittorrent, 'qbittorrent').className
              }
            />
            {portConflicts.qbittorrent_port && (
              <div className="conflict-warning">
                ⚠ {portConflicts.qbittorrent_port}
              </div>
            )}
          </div>
          
          {/* Similar port inputs for other services */}
          <div className="port-input-group">
            <label htmlFor="prowlarr_port">
              Prowlarr
              <span className="port-status">
                {getPortStatus(config.prowlarr, 'prowlarr').status === 'valid' && '✓'}
                {getPortStatus(config.prowlarr, 'prowlarr').message}
              </span>
            </label>
            <input
              id="prowlarr_port"
              type="number"
              value={config.prowlarr}
              onChange={(e) => handlePortChange('prowlarr', parseInt(e.target.value))}
              min="1"
              max="65535"
              placeholder="9696"
              className={
                getPortStatus(config.prowlarr, 'prowlarr').className
              }
            />
            {portConflicts.prowlarr_port && (
              <div className="conflict-warning">
                ⚠ {portConflicts.prowlarr_port}
              </div>
            )}
          </div>
          
          <div className="port-input-group">
            <label htmlFor="radarr_port">
              Radarr
              <span className="port-status">
                {getPortStatus(config.radarr, 'radarr').status === 'valid' && '✓'}
                {getPortStatus(config.radarr, 'radarr').message}
              </span>
            </label>
            <input
              id="radarr_port"
              type="number"
              value={config.radarr}
              onChange={(e) => handlePortChange('radarr', parseInt(e.target.value))}
              min="1"
              max="65535"
              placeholder="7878"
              className={
                getPortStatus(config.radarr, 'radarr').className
              }
            />
            {portConflicts.radarr_port && (
              <div className="conflict-warning">
                ⚠ {portConflicts.radarr_port}
              </div>
            )}
          </div>
          
          <div className="port-input-group">
            <label htmlFor="sonarr_port">
              Sonarr
              <span className="port-status">
                {getPortStatus(config.sonarr, 'sonarr').status === 'valid' && '✓'}
                {getPortStatus(config.sonarr, 'sonarr').message}
              </span>
            </label>
            <input
              id="sonarr_port"
              type="number"
              value={config.sonarr}
              onChange={(e) => handlePortChange('sonarr', parseInt(e.target.value))}
              min="1"
              max="65535"
              placeholder="8989"
              className={
                getPortStatus(config.sonarr, 'sonarr').className
              }
            />
            {portConflicts.sonarr_port && (
              <div className="conflict-warning">
                ⚠ {portConflicts.sonarr_port}
              </div>
            )}
          </div>
          
          <div className="port-input-group">
            <label htmlFor="jellyfin_port">
              Jellyfin
              <span className="port-status">
                {getPortStatus(config.jellyfin, 'jellyfin').status === 'valid' && '✓'}
                {getPortStatus(config.jellyfin, 'jellyfin').message}
              </span>
            </label>
            <input
              id="jellyfin_port"
              type="number"
              value={config.jellyfin}
              onChange={(e) => handlePortChange('jellyfin', parseInt(e.target.value))}
              min="1"
              max="65535"
              placeholder="8096"
              className={
                getPortStatus(config.jellyfin, 'jellyfin').className
              }
            />
            {portConflicts.jellyfin_port && (
              <div className="conflict-warning">
                ⚠ {portConflicts.jellyfin_port}
              </div>
            )}
          </div>
          
          <div className="port-input-group">
            <label htmlFor="jellyseerr_port">
              Jellyseerr
              <span className="port-status">
                {getPortStatus(config.jellyseerr, 'jellyseerr').status === 'valid' && '✓'}
                {getPortStatus(config.jellyseerr, 'jellyseerr').message}
              </span>
            </label>
            <input
              id="jellyseerr_port"
              type="number"
              value={config.jellyseerr}
              onChange={(e) => handlePortChange('jellyseerr', parseInt(e.target.value))}
              min="1"
              max="65535"
              placeholder="5055"
              className={
                getPortStatus(config.jellyseerr, 'jellyseerr').className
              }
            />
            {portConflicts.jellyseerr_port && (
              <div className="conflict-warning">
                ⚠ {portConflicts.jellyseerr_port}
              </div>
            )}
          </div>
        </div>
      </div>
      
      {/* Network Info */}
      <div className="network-info">
        <div className="info-box">
          <h4>🌐 Network Tips</h4>
          <ul>
            <li>Each service needs a unique port to avoid conflicts</li>
            <li>Common ports (80, 443, 22, 21) are not recommended</li>
            <li>Port range: 1-65535 (valid TCP/UDP ports)</li>
            <li>Green checkmark = Port is available and unique</li>
            <li>Red warning = Port conflict or invalid</li>
          </ul>
        </div>
      </div>
      
      <style jsx>{`
        .network-configuration-step h2 {
          color: #fff;
          margin-bottom: 1.5rem;
        }
        
        .network-configuration-step p {
          color: #aaa;
          margin-bottom: 2rem;
          line-height: 1.6;
        }
        
        .network-section {
          margin-bottom: 2rem;
        }
        
        .network-section h3 {
          color: #fff;
          margin-bottom: 1rem;
        }
        
        .interface-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
          gap: 1rem;
        }
        
        .interface-card {
          background: #2a2a3a;
          border: 2px solid #444;
          border-radius: 12px;
          padding: 1rem;
          cursor: pointer;
          transition: all 0.3s ease;
        }
        
        .interface-card:hover {
          border-color: #5c9ceb;
          transform: translateY(-2px);
        }
        
        .interface-card.selected {
          border-color: #5c9ceb;
          background: rgba(92, 156, 235, 0.1);
        }
        
        .interface-name {
          font-weight: bold;
          color: #5c9ceb;
          margin-bottom: 0.5rem;
        }
        
        .interface-details {
          display: flex;
          gap: 1rem;
          font-size: 0.9rem;
          color: #aaa;
        }
        
        .interface-ip, .interface-netmask {
          font-family: monospace;
          background: #333;
          padding: 0.25rem 0.5rem;
          border-radius: 4px;
        }
        
        .port-section {
          margin-bottom: 2rem;
        }
        
        .port-section h3 {
          color: #fff;
          margin-bottom: 1rem;
        }
        
        .port-controls {
          display: flex;
          gap: 1rem;
          margin-bottom: 1rem;
        }
        
        .recommended-button, .validate-button {
          padding: 0.5rem 1rem;
          border-radius: 6px;
          border: none;
          font-size: 0.9rem;
          cursor: pointer;
          transition: all 0.3s ease;
        }
        
        .recommended-button {
          background: #4caf50;
          color: white;
        }
        
        .recommended-button:hover {
          background: #45a049;
        }
        
        .validate-button {
          background: #5c9ceb;
          color: white;
        }
        
        .validate-button:hover:not(:disabled) {
          background: #4a90e2;
        }
        
        .validate-button.loading {
          background: #666;
          cursor: not-allowed;
        }
        
        .port-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
          gap: 1rem;
        }
        
        .port-input-group {
          display: flex;
          flex-direction: column;
          gap: 0.5rem;
        }
        
        .port-input-group label {
          display: flex;
          align-items: center;
          gap: 0.5rem;
          color: #aaa;
          font-size: 0.9rem;
          min-width: 100px;
        }
        
        .port-status {
          display: flex;
          align-items: center;
          gap: 0.5rem;
          font-size: 0.8rem;
        }
        
        .port-input-group input {
          flex: 1;
          padding: 0.5rem;
          background: #1e1e2e;
          border: 1px solid #444;
          border-radius: 6px;
          color: #fff;
          font-size: 1rem;
        }
        
        .port-input-group input.port-valid {
          border-color: #4caf50;
          background: rgba(76, 175, 80, 0.1);
        }
        
        .port-input-group input.port-error {
          border-color: #f44336;
          background: rgba(244, 67, 54, 0.1);
        }
        
        .port-input-group input.port-neutral {
          border-color: #555;
        }
        
        .conflict-warning {
          color: #ff9800;
          font-size: 0.8rem;
          margin-top: 0.25rem;
          padding: 0.25rem 0.5rem;
          background: rgba(255, 152, 0, 0.1);
          border-radius: 4px;
        }
        
        .network-info {
          margin-top: 1.5rem;
        }
        
        .info-box {
          background: rgba(92, 156, 235, 0.1);
          border: 1px solid rgba(92, 156, 235, 0.3);
          border-radius: 8px;
          padding: 1rem;
        }
        
        .info-box h4 {
          color: #5c9ceb;
          margin: 0 0 0.5rem 0;
        }
        
        .info-box ul {
          margin: 0;
          padding-left: 1rem;
          color: #ccc;
        }
        
        .info-box li {
          margin-bottom: 0.5rem;
          line-height: 1.4;
        }
      `}</style>
    </div>
  );
}