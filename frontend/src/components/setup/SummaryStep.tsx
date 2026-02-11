import React, { useState, useEffect } from 'react';
import { useValidation } from '../../hooks/useValidation';
import type { ValidationError } from '../../types/validation';

interface ServiceConfig {
  qbittorrent: {
    host: string;
    web_port: number;
    username?: string;
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

interface PathConfig {
  media_path: string;
  downloads_path: string;
  appdata_path: string;
  scratch_path: string;
  selectedVolume: {
    device: string;
    mountpoint: string;
    size: string;
    available: string;
    filesystem: string;
  } | null;
  useScratch: boolean;
}

interface NetworkConfig {
  interface: string;
  qbittorrent: number;
  prowlarr: number;
  radarr: number;
  sonarr: number;
  jellyfin: number;
  jellyseerr: number;
}

interface SummaryStepProps {
  serviceConfig: ServiceConfig;
  pathConfig: PathConfig;
  networkConfig: NetworkConfig;
  onBack: () => void;
  onApply: () => void;
  canProceed: boolean;
}

export function SummaryStep({ 
  serviceConfig, 
  pathConfig, 
  networkConfig, 
  onBack, 
  onApply,
  canProceed 
}: SummaryStepProps) {
  const [isApplying, setIsApplying] = useState(false);
  const [validationErrors, setValidationErrors] = useState<ValidationError[]>([]);
  const [validationWarnings, setValidationWarnings] = useState<ValidationError[]>([]);
  const [isExpanded, setIsExpanded] = useState<Record<string, boolean>>({
    services: true,
    paths: true,
    network: true,
    validation: true
  });

  const { validateConfiguration, validationState } = useValidation();

  // Run comprehensive validation on component mount
  useEffect(() => {
    runFinalValidation();
  }, [serviceConfig, pathConfig, networkConfig]);

  const runFinalValidation = async () => {
    const fullConfig = {
      services: serviceConfig,
      paths: pathConfig,
      network: networkConfig
    };

    const result = await validateConfiguration(fullConfig, {
      partial: false,
      skip_service_checks: false
    });

    if (result.success && result.result) {
      setValidationErrors(result.result.errors.filter(e => e.severity === 'error'));
      setValidationWarnings(result.result.warnings);
    }
  };

  const handleApply = async () => {
    if (!canProceed || validationErrors.length > 0) {
      return;
    }

    setIsApplying(true);
    
    try {
      // Final validation before apply
      await runFinalValidation();
      
      if (validationErrors.length === 0) {
        onApply();
      }
    } catch (error) {
      console.error('Failed to apply configuration:', error);
    } finally {
      setIsApplying(false);
    }
  };

  const toggleSection = (section: string) => {
    setIsExpanded(prev => ({
      ...prev,
      [section]: !prev[section]
    }));
  };

  const getServiceStatusIcon = (service: keyof ServiceConfig) => {
    const config = serviceConfig[service];
    if (!config) return '❓';
    
    // Basic status check
    const hasRequiredFields = 
      (config as any).host && 
      (config as any).port && 
      (config as any).port > 0;
    
    if (service === 'qbittorrent') {
      const qbConfig = config as ServiceConfig['qbittorrent'];
      return hasRequiredFields && qbConfig.username ? '✅' : '⚠️';
    }
    
    if (service === 'remux_agent') {
      const remuxConfig = config as ServiceConfig['remux_agent'];
      return remuxConfig.ffmpeg_path ? '✅' : '⚠️';
    }
    
    const arrService = config as ServiceConfig['prowlarr'];
    return hasRequiredFields && arrService.api_key ? '✅' : '⚠️';
  };

  const getValidationIcon = (type: 'error' | 'warning' | 'success') => {
    switch (type) {
      case 'error': return '❌';
      case 'warning': return '⚠️';
      case 'success': return '✅';
      default: return '❓';
    }
  };

  const estimatedTime = validationState.estimatedTime || '5-10 minutes';
  const nextSteps = validationState.nextSteps || [
    'Deploy Docker containers',
    'Configure service APIs',
    'Set up media libraries',
    'Initialize download clients'
  ];

  const isReadyToApply = canProceed && validationErrors.length === 0 && !isApplying;

  return (
    <div className="summary-step">
      <h2>Configuration Summary</h2>
      <p>Review your complete configuration before applying. Click any section to expand/collapse details.</p>
      
      {/* Overall Status */}
      <div className={`overall-status ${validationErrors.length === 0 ? 'ready' : 'not-ready'}`}>
        <div className="status-header">
          <h3>
            {validationErrors.length === 0 ? '🚀 Ready to Apply' : '⚠️ Issues Found'}
          </h3>
          <div className="validation-summary">
            <span className="error-count">
              {getValidationIcon('error')} {validationErrors.length} Errors
            </span>
            <span className="warning-count">
              {getValidationIcon('warning')} {validationWarnings.length} Warnings
            </span>
          </div>
        </div>
        
        {validationErrors.length === 0 && validationWarnings.length === 0 && (
          <p className="success-message">
            All checks passed! Your configuration is ready to deploy.
          </p>
        )}
      </div>

      {/* Service Configuration Summary */}
      <div className={`summary-section ${isExpanded.services ? 'expanded' : ''}`}>
        <div className="section-header" onClick={() => toggleSection('services')}>
          <h3>🔧 Services Configuration</h3>
          <button className="toggle-button">
            {isExpanded.services ? '▼' : '▶'}
          </button>
        </div>
        
        {isExpanded.services && (
          <div className="section-content">
            <div className="service-grid">
              <div className="service-item">
                <div className="service-name">
                  {getServiceStatusIcon('qbittorrent')} qBittorrent
                </div>
                <div className="service-details">
                  <span>{serviceConfig.qbittorrent.host}:{serviceConfig.qbittorrent.web_port}</span>
                  <span className="service-auth">
                    {serviceConfig.qbittorrent.username ? `Auth: ${serviceConfig.qbittorrent.username}` : 'No auth'}
                  </span>
                </div>
              </div>

              <div className="service-item">
                <div className="service-name">
                  {getServiceStatusIcon('prowlarr')} Prowlarr
                </div>
                <div className="service-details">
                  <span>{serviceConfig.prowlarr.host}:{serviceConfig.prowlarr.port}</span>
                  <span className="service-auth">
                    {serviceConfig.prowlarr.api_key ? 'API Key configured' : 'No API key'}
                  </span>
                </div>
              </div>

              <div className="service-item">
                <div className="service-name">
                  {getServiceStatusIcon('radarr')} Radarr
                </div>
                <div className="service-details">
                  <span>{serviceConfig.radarr.host}:{serviceConfig.radarr.port}</span>
                  <span className="service-auth">
                    {serviceConfig.radarr.api_key ? 'API Key configured' : 'No API key'}
                  </span>
                </div>
              </div>

              <div className="service-item">
                <div className="service-name">
                  {getServiceStatusIcon('sonarr')} Sonarr
                </div>
                <div className="service-details">
                  <span>{serviceConfig.sonarr.host}:{serviceConfig.sonarr.port}</span>
                  <span className="service-auth">
                    {serviceConfig.sonarr.api_key ? 'API Key configured' : 'No API key'}
                  </span>
                </div>
              </div>

              <div className="service-item">
                <div className="service-name">
                  {getServiceStatusIcon('jellyfin')} Jellyfin
                </div>
                <div className="service-details">
                  <span>{serviceConfig.jellyfin.host}:{serviceConfig.jellyfin.port}</span>
                </div>
              </div>

              <div className="service-item">
                <div className="service-name">
                  {getServiceStatusIcon('jellyseerr')} Jellyseerr
                </div>
                <div className="service-details">
                  <span>{serviceConfig.jellyseerr.host}:{serviceConfig.jellyseerr.port}</span>
                </div>
              </div>

              <div className="service-item">
                <div className="service-name">
                  {getServiceStatusIcon('remux_agent')} Remux Agent
                </div>
                <div className="service-details">
                  <span>FFmpeg: {serviceConfig.remux_agent.ffmpeg_path}</span>
                  <span className="service-auth">
                    Languages: {serviceConfig.remux_agent.language_filters.join(', ')}
                  </span>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Path Configuration Summary */}
      <div className={`summary-section ${isExpanded.paths ? 'expanded' : ''}`}>
        <div className="section-header" onClick={() => toggleSection('paths')}>
          <h3>📁 Storage Paths</h3>
          <button className="toggle-button">
            {isExpanded.paths ? '▼' : '▶'}
          </button>
        </div>
        
        {isExpanded.paths && (
          <div className="section-content">
            {pathConfig.selectedVolume && (
              <div className="volume-info">
                <div className="volume-header">
                  <h4>Selected Volume</h4>
                  <div className="volume-stats">
                    <span>{pathConfig.selectedVolume.device}</span>
                    <span>{pathConfig.selectedVolume.filesystem}</span>
                  </div>
                </div>
                <div className="volume-details">
                  <span>Mount: {pathConfig.selectedVolume.mountpoint}</span>
                  <span>Size: {pathConfig.selectedVolume.size}</span>
                  <span>Available: {pathConfig.selectedVolume.available}</span>
                </div>
              </div>
            )}
            
            <div className="path-grid">
              <div className="path-item">
                <label>Media Library</label>
                <code>{pathConfig.media_path}</code>
              </div>
              <div className="path-item">
                <label>Downloads</label>
                <code>{pathConfig.downloads_path}</code>
              </div>
              <div className="path-item">
                <label>App Data</label>
                <code>{pathConfig.appdata_path}</code>
              </div>
              {pathConfig.useScratch && pathConfig.scratch_path && (
                <div className="path-item">
                  <label>Scratch/Temp</label>
                  <code>{pathConfig.scratch_path}</code>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* Network Configuration Summary */}
      <div className={`summary-section ${isExpanded.network ? 'expanded' : ''}`}>
        <div className="section-header" onClick={() => toggleSection('network')}>
          <h3>🌐 Network Configuration</h3>
          <button className="toggle-button">
            {isExpanded.network ? '▼' : '▶'}
          </button>
        </div>
        
        {isExpanded.network && (
          <div className="section-content">
            <div className="network-overview">
              <div className="interface-info">
                <label>Interface</label>
                <span>{networkConfig.interface || 'Default'}</span>
              </div>
            </div>
            
            <div className="port-grid">
              <div className="port-item">
                <label>qBittorrent</label>
                <span>{networkConfig.qbittorrent}</span>
              </div>
              <div className="port-item">
                <label>Prowlarr</label>
                <span>{networkConfig.prowlarr}</span>
              </div>
              <div className="port-item">
                <label>Radarr</label>
                <span>{networkConfig.radarr}</span>
              </div>
              <div className="port-item">
                <label>Sonarr</label>
                <span>{networkConfig.sonarr}</span>
              </div>
              <div className="port-item">
                <label>Jellyfin</label>
                <span>{networkConfig.jellyfin}</span>
              </div>
              <div className="port-item">
                <label>Jellyseerr</label>
                <span>{networkConfig.jellyseerr}</span>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Validation Results */}
      <div className={`summary-section ${isExpanded.validation ? 'expanded' : ''}`}>
        <div className="section-header" onClick={() => toggleSection('validation')}>
          <h3>✅ Validation Results</h3>
          <button className="toggle-button">
            {isExpanded.validation ? '▼' : '▶'}
          </button>
        </div>
        
        {isExpanded.validation && (
          <div className="section-content">
            {validationErrors.length > 0 && (
              <div className="validation-errors">
                <h4>❌ Errors (Must Fix)</h4>
                {validationErrors.map((error, index) => (
                  <div key={index} className="validation-item error">
                    <div className="validation-header">
                      <strong>{error.field}</strong>
                      <span className="error-code">{error.code}</span>
                    </div>
                    <div className="validation-message">{error.message}</div>
                    {error.suggestions.length > 0 && (
                      <ul className="validation-suggestions">
                        {error.suggestions.map((suggestion, i) => (
                          <li key={i}>{suggestion}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                ))}
              </div>
            )}

            {validationWarnings.length > 0 && (
              <div className="validation-warnings">
                <h4>⚠️ Warnings (Recommended)</h4>
                {validationWarnings.map((warning, index) => (
                  <div key={index} className="validation-item warning">
                    <div className="validation-header">
                      <strong>{warning.field}</strong>
                    </div>
                    <div className="validation-message">{warning.message}</div>
                    {warning.suggestions.length > 0 && (
                      <ul className="validation-suggestions">
                        {warning.suggestions.map((suggestion, i) => (
                          <li key={i}>{suggestion}</li>
                        ))}
                      </ul>
                    )}
                  </div>
                ))}
              </div>
            )}

            {validationErrors.length === 0 && validationWarnings.length === 0 && (
              <div className="validation-success">
                <div className="success-icon">✅</div>
                <h4>All validations passed!</h4>
                <p>Your configuration has been thoroughly checked and is ready to deploy.</p>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Deployment Information */}
      <div className="deployment-info">
        <h3>🚀 Deployment Information</h3>
        <div className="info-grid">
          <div className="info-item">
            <label>Estimated Time</label>
            <span>{estimatedTime}</span>
          </div>
          <div className="info-item">
            <label>Services to Deploy</label>
            <span>7 services</span>
          </div>
        </div>
        
        <div className="next-steps">
          <h4>Next Steps After Deployment:</h4>
          <ul>
            {nextSteps.map((step, index) => (
              <li key={index}>{step}</li>
            ))}
          </ul>
        </div>
      </div>

      {/* Action Buttons */}
      <div className="action-buttons">
        <button
          onClick={onBack}
          className="back-button"
          disabled={isApplying}
        >
          ← Go Back
        </button>
        
        <button
          onClick={runFinalValidation}
          className="refresh-button"
          disabled={validationState.isValidating}
        >
          {validationState.isValidating ? 'Validating...' : '🔄 Refresh Validation'}
        </button>
        
        <button
          onClick={handleApply}
          className={`apply-button ${isReadyToApply ? 'ready' : 'disabled'}`}
          disabled={!isReadyToApply}
        >
          {isApplying ? '🔄 Applying Configuration...' : '🚀 Apply Configuration'}
        </button>
      </div>

      <style jsx>{`
        .summary-step h2 {
          color: #fff;
          margin-bottom: 1.5rem;
        }
        
        .summary-step p {
          color: #aaa;
          margin-bottom: 2rem;
          line-height: 1.6;
        }
        
        .overall-status {
          background: #2a2a3a;
          border-radius: 12px;
          padding: 1.5rem;
          margin-bottom: 2rem;
          border: 2px solid;
        }
        
        .overall-status.ready {
          border-color: #4caf50;
          background: rgba(76, 175, 80, 0.1);
        }
        
        .overall-status.not-ready {
          border-color: #f44336;
          background: rgba(244, 67, 54, 0.1);
        }
        
        .status-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 1rem;
        }
        
        .status-header h3 {
          color: #fff;
          margin: 0;
        }
        
        .validation-summary {
          display: flex;
          gap: 1rem;
        }
        
        .error-count, .warning-count {
          padding: 0.25rem 0.75rem;
          border-radius: 20px;
          font-size: 0.9rem;
          background: rgba(0, 0, 0, 0.2);
        }
        
        .success-message {
          color: #4caf50;
          margin: 0;
          font-weight: 500;
        }
        
        .summary-section {
          background: #2a2a3a;
          border: 2px solid #444;
          border-radius: 12px;
          margin-bottom: 1rem;
          overflow: hidden;
        }
        
        .summary-section.expanded {
          border-color: #5c9ceb;
        }
        
        .section-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 1rem 1.5rem;
          cursor: pointer;
          user-select: none;
          transition: background 0.3s ease;
        }
        
        .section-header:hover {
          background: rgba(92, 156, 235, 0.1);
        }
        
        .section-header h3 {
          color: #fff;
          margin: 0;
        }
        
        .toggle-button {
          background: none;
          border: none;
          color: #aaa;
          font-size: 1rem;
          cursor: pointer;
          transition: color 0.3s ease;
        }
        
        .toggle-button:hover {
          color: #5c9ceb;
        }
        
        .section-content {
          padding: 0 1.5rem 1.5rem;
          border-top: 1px solid #444;
        }
        
        .service-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
          gap: 1rem;
        }
        
        .service-item {
          background: #1e1e2e;
          border-radius: 8px;
          padding: 1rem;
          display: flex;
          justify-content: space-between;
          align-items: center;
        }
        
        .service-name {
          font-weight: 500;
          color: #5c9ceb;
        }
        
        .service-details {
          display: flex;
          flex-direction: column;
          align-items: flex-end;
          gap: 0.25rem;
        }
        
        .service-details span {
          font-size: 0.9rem;
          color: #ccc;
        }
        
        .service-auth {
          font-size: 0.8rem;
          color: #888;
        }
        
        .volume-info {
          background: #1e1e2e;
          border-radius: 8px;
          padding: 1rem;
          margin-bottom: 1rem;
        }
        
        .volume-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 0.5rem;
        }
        
        .volume-header h4 {
          color: #5c9ceb;
          margin: 0;
        }
        
        .volume-stats {
          display: flex;
          gap: 1rem;
          font-size: 0.9rem;
          color: #888;
        }
        
        .volume-details {
          display: flex;
          gap: 1rem;
          font-size: 0.9rem;
          color: #aaa;
        }
        
        .path-grid, .port-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
          gap: 1rem;
        }
        
        .path-item, .port-item {
          display: flex;
          justify-content: space-between;
          align-items: center;
          background: #1e1e2e;
          padding: 0.75rem;
          border-radius: 6px;
        }
        
        .path-item label, .port-item label {
          color: #888;
          font-size: 0.9rem;
        }
        
        .path-item code {
          background: #333;
          padding: 0.25rem 0.5rem;
          border-radius: 4px;
          font-family: monospace;
          color: #5c9ceb;
          font-size: 0.8rem;
        }
        
        .port-item span {
          color: #fff;
          font-weight: 500;
        }
        
        .network-overview {
          margin-bottom: 1rem;
        }
        
        .interface-info {
          display: flex;
          justify-content: space-between;
          align-items: center;
          background: #1e1e2e;
          padding: 0.75rem;
          border-radius: 6px;
        }
        
        .interface-info label {
          color: #888;
          font-size: 0.9rem;
        }
        
        .interface-info span {
          color: #fff;
          font-weight: 500;
        }
        
        .validation-errors, .validation-warnings {
          margin-bottom: 1.5rem;
        }
        
        .validation-errors h4, .validation-warnings h4 {
          color: #fff;
          margin-bottom: 1rem;
        }
        
        .validation-item {
          background: #1e1e2e;
          border-radius: 8px;
          padding: 1rem;
          margin-bottom: 1rem;
          border-left: 4px solid;
        }
        
        .validation-item.error {
          border-left-color: #f44336;
        }
        
        .validation-item.warning {
          border-left-color: #ff9800;
        }
        
        .validation-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
          margin-bottom: 0.5rem;
        }
        
        .validation-header strong {
          color: #fff;
        }
        
        .error-code {
          color: #888;
          font-size: 0.8rem;
          font-family: monospace;
        }
        
        .validation-message {
          color: #ccc;
          margin-bottom: 0.5rem;
        }
        
        .validation-suggestions {
          margin: 0;
          padding-left: 1rem;
        }
        
        .validation-suggestions li {
          color: #aaa;
          margin-bottom: 0.25rem;
        }
        
        .validation-success {
          text-align: center;
          padding: 2rem;
        }
        
        .success-icon {
          font-size: 3rem;
          margin-bottom: 1rem;
        }
        
        .validation-success h4 {
          color: #4caf50;
          margin-bottom: 0.5rem;
        }
        
        .validation-success p {
          color: #ccc;
          margin: 0;
        }
        
        .deployment-info {
          background: #2a2a3a;
          border: 2px solid #444;
          border-radius: 12px;
          padding: 1.5rem;
          margin-bottom: 2rem;
        }
        
        .deployment-info h3 {
          color: #fff;
          margin-bottom: 1rem;
        }
        
        .info-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
          gap: 1rem;
          margin-bottom: 1.5rem;
        }
        
        .info-item {
          display: flex;
          justify-content: space-between;
          align-items: center;
        }
        
        .info-item label {
          color: #888;
          font-size: 0.9rem;
        }
        
        .info-item span {
          color: #fff;
          font-weight: 500;
        }
        
        .next-steps h4 {
          color: #5c9ceb;
          margin-bottom: 0.5rem;
        }
        
        .next-steps ul {
          margin: 0;
          padding-left: 1rem;
        }
        
        .next-steps li {
          color: #ccc;
          margin-bottom: 0.25rem;
        }
        
        .action-buttons {
          display: flex;
          gap: 1rem;
          justify-content: flex-end;
          align-items: center;
        }
        
        .back-button, .refresh-button, .apply-button {
          padding: 0.75rem 1.5rem;
          border-radius: 8px;
          border: none;
          font-size: 1rem;
          cursor: pointer;
          transition: all 0.3s ease;
        }
        
        .back-button {
          background: #666;
          color: white;
        }
        
        .back-button:hover:not(:disabled) {
          background: #555;
        }
        
        .refresh-button {
          background: #ff9800;
          color: white;
        }
        
        .refresh-button:hover:not(:disabled) {
          background: #e68900;
        }
        
        .apply-button.ready {
          background: #4caf50;
          color: white;
          font-weight: 500;
        }
        
        .apply-button.ready:hover {
          background: #45a049;
        }
        
        .apply-button.disabled {
          background: #666;
          color: #888;
          cursor: not-allowed;
        }
        
        button:disabled {
          opacity: 0.6;
          cursor: not-allowed;
        }
        
        @media (max-width: 768px) {
          .status-header, .section-header, .validation-header {
            flex-direction: column;
            align-items: flex-start;
            gap: 0.5rem;
          }
          
          .validation-summary {
            flex-direction: column;
            gap: 0.5rem;
          }
          
          .service-grid, .path-grid, .port-grid {
            grid-template-columns: 1fr;
          }
          
          .service-item {
            flex-direction: column;
            align-items: flex-start;
            gap: 0.5rem;
          }
          
          .action-buttons {
            flex-direction: column;
          }
          
          .action-buttons button {
            width: 100%;
          }
        }
      `}</style>
    </div>
  );
}