import React, { useState, useEffect } from 'react';
import { useValidation } from '../../hooks/useValidation';
import type { ClientValidationRule } from '../../types/validation';

interface Volume {
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

interface PathConfig {
  media_path: string;
  downloads_path: string;
  appdata_path: string;
  scratch_path: string;
  selectedVolume: Volume | null;
  useScratch: boolean;
}

interface PathSelectionStepProps {
  config: PathConfig;
  onChange: (config: PathConfig) => void;
  canProceed: boolean;
  onNext: () => void;
}

export function PathSelectionStep({ config, onChange, canProceed, onNext }: PathSelectionStepProps) {
  const [volumes, setVolumes] = useState<Volume[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { validateField, debouncedValidate, getFieldValidation, clearValidation } = useValidation();

  // Client-side validation rules for paths
  const pathValidationRules: Record<string, ClientValidationRule> = {
    media_path: {
      field: 'media_path',
      type: 'string',
      required: true,
      min_length: 3,
      max_length: 255,
      custom_rules: ['path_exists', 'is_directory', 'has_permissions']
    },
    downloads_path: {
      field: 'downloads_path',
      type: 'string',
      required: true,
      min_length: 3,
      max_length: 255,
      custom_rules: ['path_exists', 'is_directory', 'has_permissions']
    },
    appdata_path: {
      field: 'appdata_path',
      type: 'string',
      required: true,
      min_length: 3,
      max_length: 255,
      custom_rules: ['path_exists', 'is_directory', 'has_permissions']
    },
    scratch_path: {
      field: 'scratch_path',
      type: 'string',
      required: false,
      min_length: 3,
      max_length: 255,
      custom_rules: ['path_exists', 'is_directory', 'has_permissions']
    }
  };

  // Load volumes on component mount
  useEffect(() => {
    loadVolumes();
  }, []);

  const loadVolumes = async () => {
    setIsLoading(true);
    setError(null);
    
    try {
      const response = await fetch('/api/system/volumes');
      const data = await response.json();
      
      if (!data.volumes || data.volumes.length === 0) {
        setError('No volumes detected. Make sure orchestrator has access to scan mounted volumes.');
      } else {
        setVolumes(data.volumes);
      }
    } catch (err) {
      setError('Failed to scan volumes. Please check system permissions.');
    } finally {
      setIsLoading(false);
    }
  };

  const handleVolumeSelect = (volume: Volume) => {
    const newConfig = {
      ...config,
      selectedVolume: volume,
      media_path: volume.suggested_paths.media,
      appdata_path: volume.suggested_paths.appdata,
      downloads_path: config.useScratch ? volume.suggested_paths.downloads : config.downloads_path,
      scratch_path: config.useScratch ? volume.suggested_paths.downloads : config.scratch_path
    };
    
    onChange(newConfig);
    clearValidation();
  };

  const handlePathChange = (field: keyof PathConfig, value: string) => {
    const newConfig = { ...config, [field]: value };
    onChange(newConfig);
    
    // Validate field on change
    debouncedValidate(field, value);
  };

  const handleScratchToggle = (useScratch: boolean) => {
    const newConfig = { ...config, useScratch };
    
    if (useScratch && config.selectedVolume) {
      newConfig.scratch_path = config.selectedVolume.suggested_paths.downloads;
      newConfig.downloads_path = config.selectedVolume.suggested_paths.downloads;
    }
    
    onChange(newConfig);
  };

  const validateAllPaths = async () => {
    // Validate all path fields
    const fieldsToValidate = ['media_path', 'downloads_path', 'appdata_path'];
    if (config.useScratch) {
      fieldsToValidate.push('scratch_path');
    }

    const configForValidation = {
      config: {
        media_path: config.media_path,
        downloads_path: config.downloads_path,
        appdata_path: config.appdata_path,
        ...(config.useScratch && { scratch_path: config.scratch_path })
      }
    };

    try {
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
      
      if (result.success && result.result) {
        // Store validation results for later use
        return result.result.success;
      }
    } catch (err) {
      setError('Failed to validate paths');
      return false;
    }
    
    return true;
  };

  const getVolumeSpaceColor = (available: string) => {
    const gbMatch = available.match(/([0-9.]+)\s*GB/);
    if (!gbMatch) return '#666';
    
    const availableGb = parseFloat(gbMatch[1]);
    if (availableGb > 100) return '#4caf50';
    if (availableGb > 50) return '#ff9800';
    if (availableGb > 10) return '#ff5722';
    return '#666';
  };

  return (
    <div className="path-selection-step">
      <h2>Configure Storage Paths</h2>
      <p>Select your storage volume and configure paths for your media stack.</p>
      
      {error && (
        <div className="error-message">
          ⚠ {error}
        </div>
      )}
      
      {/* Volume Selection - Temporarily Simplified */}
      <div className="volume-selection">
        <h3>Select Storage Volume</h3>
        <p>Volume selection temporarily simplified for testing.</p>
      </div>
      {volumes.length > 0 ? (
          <div className="volume-grid">
            {volumes.map((volume) => (
              <div
                key={volume.device}
                className={`volume-card ${
                  config.selectedVolume?.device === volume.device ? 'selected' : ''
                }`}
                onClick={() => handleVolumeSelect(volume)}
              >
                <div className="volume-header">
                  <div className="volume-device">{volume.device}</div>
                  <div className="volume-filesystem">{volume.filesystem}</div>
                </div>
                <div className="volume-mountpoint">{volume.mountpoint}</div>
                
                <div className="volume-stats">
                  <div className="volume-size">Total: {volume.size}</div>
                  <div 
                    className="volume-available"
                    style={{ color: getVolumeSpaceColor(volume.available) }}
                  >
                    Available: {volume.available}
                  </div>
                </div>
                
                <div className="volume-suggested">
                  <h4>Suggested Paths</h4>
                  <div className="path-suggestion">
                    <label>Media:</label>
                    <code>{volume.suggested_paths.media}</code>
                  </div>
                  <div className="path-suggestion">
                    <label>Downloads:</label>
                    <code>{volume.suggested_paths.downloads}</code>
                  </div>
                  <div className="path-suggestion">
                    <label>AppData:</label>
                    <code>{volume.suggested_paths.appdata}</code>
                  </div>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="no-volumes">
            <p>No volumes detected.</p>
            <p>Please ensure the orchestrator has access to scan mounted volumes.</p>
          </div>
        )}
      </div>
      
      {/* Path Configuration */}
      {config.selectedVolume && (
        <div className="path-configuration">
          <h3>Path Configuration</h3>
          
          <div className="path-input-grid">
            <div className="path-input-group">
              <label htmlFor="media_path">
                Media Library Path *
                <span className="help-text">
                  Main location for movies, TV shows, and media
                </span>
              </label>
              <input
                id="media_path"
                type="text"
                value={config.media_path}
                onChange={(e) => handlePathChange('media_path', e.target.value)}
                placeholder={config.selectedVolume.suggested_paths.media}
                className={
                  getFieldValidation('media_path').error ? 'error' : 
                  getFieldValidation('media_path').isValid ? 'valid' : ''
                }
              />
              {getFieldValidation('media_path').error && (
                <div className="field-error">
                  {getFieldValidation('media_path').error}
                </div>
              )}
              {getFieldValidation('media_path').isValid && (
                <div className="field-valid">✓</div>
              )}
            </div>
            
            <div className="path-input-group">
              <label htmlFor="appdata_path">
                Application Data Path *
                <span className="help-text">
                  Service configurations and databases
                </span>
              </label>
              <input
                id="appdata_path"
                type="text"
                value={config.appdata_path}
                onChange={(e) => handlePathChange('appdata_path', e.target.value)}
                placeholder={config.selectedVolume.suggested_paths.appdata}
                className={
                  getFieldValidation('appdata_path').error ? 'error' : 
                  getFieldValidation('appdata_path').isValid ? 'valid' : ''
                }
              />
              {getFieldValidation('appdata_path').error && (
                <div className="field-error">
                  {getFieldValidation('appdata_path').error}
                </div>
              )}
              {getFieldValidation('appdata_path').isValid && (
                <div className="field-valid">✓</div>
              )}
            </div>
            
            <div className="path-input-group">
              <label htmlFor="downloads_path">
                Downloads Path *
                <span className="help-text">
                  Temporary download location
                </span>
              </label>
              <input
                id="downloads_path"
                type="text"
                value={config.downloads_path}
                onChange={(e) => handlePathChange('downloads_path', e.target.value)}
                placeholder={config.selectedVolume.suggested_paths.downloads}
                className={
                  getFieldValidation('downloads_path').error ? 'error' : 
                  getFieldValidation('downloads_path').isValid ? 'valid' : ''
                }
              />
              {getFieldValidation('downloads_path').error && (
                <div className="field-error">
                  {getFieldValidation('downloads_path').error}
                </div>
              )}
              {getFieldValidation('downloads_path').isValid && (
                <div className="field-valid">✓</div>
              )}
            </div>
            
            <div className="scratch-toggle">
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={config.useScratch}
                  onChange={(e) => handleScratchToggle(e.target.checked)}
                />
                Use separate scratch directory for temporary files
              </label>
            </div>
            
            {config.useScratch && (
              <div className="path-input-group">
                <label htmlFor="scratch_path">
                  Scratch/Temp Path
                  <span className="help-text">
                    For temporary processing files
                  </span>
                </label>
                <input
                  id="scratch_path"
                  type="text"
                  value={config.scratch_path}
                  onChange={(e) => handlePathChange('scratch_path', e.target.value)}
                  placeholder={config.selectedVolume?.suggested_paths.downloads || ''}
                  className={
                    getFieldValidation('scratch_path').error ? 'error' : 
                    getFieldValidation('scratch_path').isValid ? 'valid' : ''
                  }
                />
                {getFieldValidation('scratch_path').error && (
                  <div className="field-error">
                    {getFieldValidation('scratch_path').error}
                  </div>
                )}
                {getFieldValidation('scratch_path').isValid && (
                  <div className="field-valid">✓</div>
                )}
              </div>
            )}
          </div>
        </div>
        
        <div className="path-info">
          <div className="info-box">
            <h4>💡 Smart Validation</h4>
            <ul>
              <li>Real-time path validation as you type</li>
              <li>Automatic permission and space checking</li>
              <li>Conflict detection between services</li>
              <li>Suggested optimal paths based on volume</li>
            </ul>
          </div>
        </div>
      </div>
      )}
      
      <style jsx>{`
        .path-selection-step h2 {
          color: #fff;
          margin-bottom: 1.5rem;
        }
        
        .path-selection-step p {
          color: #aaa;
          margin-bottom: 2rem;
          line-height: 1.6;
        }
        
        .error-message {
          background: rgba(244, 67, 54, 0.1);
          border: 1px solid rgba(244, 67, 54, 0.3);
          color: #ff6b6b;
          padding: 1rem;
          border-radius: 8px;
          margin-bottom: 1.5rem;
        }
        
        .volume-section h3 {
          color: #fff;
          margin-bottom: 1rem;
        }
        
        .loading-spinner {
          text-align: center;
          padding: 2rem;
        }
        
        .spinner {
          border: 3px solid #5c9ceb;
          border-top: 3px solid #1e1e2e;
          border-radius: 50%;
          width: 40px;
          height: 40px;
          animation: spin 1s linear infinite;
          margin: 0 auto 1rem;
        }
        
        @keyframes spin {
          0% { transform: rotate(0deg); }
          100% { transform: rotate(360deg); }
        }
        
        .volume-grid {
          display: grid;
          grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
          gap: 1rem;
          margin-bottom: 2rem;
        }
        
        .volume-card {
          background: #2a2a3a;
          border: 2px solid #444;
          border-radius: 12px;
          padding: 1.5rem;
          cursor: pointer;
          transition: all 0.3s ease;
        }
        
        .volume-card:hover {
          border-color: #5c9ceb;
          transform: translateY(-2px);
        }
        
        .volume-card.selected {
          border-color: #5c9ceb;
          background: rgba(92, 156, 235, 0.1);
        }
        
        .volume-header {
          display: flex;
          justify-content: space-between;
          margin-bottom: 1rem;
        }
        
        .volume-device {
          font-weight: bold;
          color: #5c9ceb;
        }
        
        .volume-filesystem {
          background: #333;
          padding: 0.25rem 0.5rem;
          border-radius: 4px;
          font-size: 0.8rem;
          color: #888;
        }
        
        .volume-mountpoint {
          font-family: monospace;
          color: #aaa;
          font-size: 0.9rem;
        }
        
        .volume-stats {
          display: flex;
          gap: 1rem;
          margin-bottom: 1rem;
        }
        
        .volume-available {
          font-weight: 500;
        }
        
        .volume-suggested h4 {
          color: #5c9ceb;
          margin: 0 0 0.5rem 0;
        }
        
        .path-suggestion label {
          display: block;
          color: #888;
          font-size: 0.8rem;
          margin-bottom: 0.25rem;
        }
        
        .path-suggestion code {
          background: #333;
          padding: 0.25rem 0.5rem;
          border-radius: 4px;
          font-family: monospace;
          color: #5c9ceb;
        }
        
        .path-configuration {
          background: #2a2a3a;
          border-radius: 12px;
          padding: 1.5rem;
          margin-bottom: 2rem;
        }
        
        .path-configuration h3 {
          color: #fff;
          margin-bottom: 1rem;
        }
        
        .path-input-grid {
          display: grid;
          gap: 1rem;
        }
        
        .path-input-group {
          display: flex;
          flex-direction: column;
          gap: 0.5rem;
        }
        
        .path-input-group label {
          display: flex;
          flex-direction: column;
          gap: 0.25rem;
          color: #aaa;
          font-size: 0.9rem;
        }
        
        .path-input-group input {
          padding: 0.75rem;
          background: #1e1e2e;
          border: 1px solid #444;
          border-radius: 6px;
          color: #fff;
          font-size: 1rem;
          font-family: monospace;
        }
        
        .path-input-group input.error {
          border-color: #f44336;
          background: rgba(244, 67, 54, 0.1);
        }
        
        .path-input-group input.valid {
          border-color: #4caf50;
          background: rgba(76, 175, 80, 0.1);
        }
        
        .field-error {
          color: #f44336;
          font-size: 0.8rem;
          margin-top: 0.25rem;
        }
        
        .field-valid {
          color: #4caf50;
          font-size: 0.8rem;
          margin-top: 0.25rem;
          display: flex;
          align-items: center;
          gap: 0.25rem;
        }
        
        .scratch-toggle {
          grid-column: span 2;
          margin-top: 1rem;
        }
        
        .checkbox-label {
          display: flex;
          align-items: center;
          gap: 0.5rem;
          color: #aaa;
          font-size: 0.9rem;
          cursor: pointer;
        }
        
        .checkbox-label input[type="checkbox"] {
          accent-color: #5c9ceb;
        }
        
        .path-info {
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
        }
        
        .no-volumes {
          background: rgba(244, 67, 54, 0.1);
          border: 1px solid rgba(244, 67, 54, 0.3);
          border-radius: 8px;
          padding: 2rem;
          text-align: center;
          color: #ff6b6b;
        }
      `}</style>
    </div>
  );
}