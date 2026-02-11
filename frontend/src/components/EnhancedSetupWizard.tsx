import React, { useState, useEffect, useCallback } from 'react';
import { useValidation } from '../hooks/useValidation';
import type { ClientValidationRule } from '../types/validation';

interface SetupWizardProps {
  onComplete: () => void;
}

interface StepConfig {
  id: string;
  title: string;
  component: React.ComponentType<any>;
  fields: string[];
  dependencies: string[];
  canProceed: boolean;
  optional?: boolean;
}

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

interface ServiceConfig {
  [key: string]: {
    host?: string;
    port?: number;
    api_key?: string;
    enabled?: boolean;
    root_folder?: string;
    download_dir?: string;
  };
}

export function EnhancedSetupWizard({ onComplete }: SetupWizardProps) {
  const [currentStep, setCurrentStep] = useState(0);
  const [isLoading, setIsLoading] = useState(false);
  const [volumes, setVolumes] = useState<Volume[]>([]);
  
  // Form data
  const [adminAccount, setAdminAccount] = useState({
    username: 'admin',
    password: '',
    confirmPassword: ''
  });
  
  const [pathConfig, setPathConfig] = useState({
    media_path: '',
    downloads_path: '',
    appdata_path: '',
    scratch_path: '',
    selectedVolume: null as Volume | null,
    useScratch: true
  });
  
  const [networkConfig, setNetworkConfig] = useState({
    interface: '',
    qbittorrent: 8080,
    prowlarr: 9696,
    radarr: 7878,
    sonarr: 8989,
    jellyfin: 8096,
    jellyseerr: 5055
  });
  
  const [serviceConfig, setServiceConfig] = useState<ServiceConfig>({});

  // Initialize validation system
  const { 
    validationState, 
    debouncedValidate,
    validateConfiguration,
    clearValidation,
    hasErrors,
    canProceed
  } = useValidation();

  // Load volumes on mount
  useEffect(() => {
    loadVolumes();
  }, []);

  // Dynamic step configuration based on validation rules
  const [steps, setSteps] = useState<StepConfig[]>([]);

  // Update client-side validation rules based on configuration
  const getClientSideRules = useCallback((): Record<string, ClientValidationRule> => {
    const rules: Record<string, ClientValidationRule> = {};

    // Path validation rules
    rules.media_path = {
      field: 'media_path',
      type: 'string',
      required: true,
      min_length: 3,
      max_length: 255,
      custom_rules: ['path_exists', 'is_directory', 'has_permissions']
    };

    rules.downloads_path = {
      field: 'downloads_path',
      type: 'string',
      required: true,
      min_length: 3,
      max_length: 255,
      custom_rules: ['path_exists', 'is_directory', 'has_permissions']
    };

    rules.appdata_path = {
      field: 'appdata_path',
      type: 'string',
      required: true,
      min_length: 3,
      max_length: 255,
      custom_rules: ['path_exists', 'is_directory', 'has_permissions']
    };

    rules.scratch_path = {
      field: 'scratch_path',
      type: 'string',
      required: false,
      min_length: 3,
      max_length: 255,
      custom_rules: ['path_exists', 'is_directory', 'has_permissions']
    };

    // Password validation rules
    rules.admin_password = {
      field: 'admin_password',
      type: 'string',
      required: true,
      min_length: 8,
      max_length: 128
    };

    rules.admin_confirmPassword = {
      field: 'admin_confirmPassword',
      type: 'string',
      required: true,
      min_length: 8,
      max_length: 128
    };

    // Port validation rules
    rules.qbittorrent_port = {
      field: 'qbittorrent_port',
      type: 'number',
      required: true,
      min_value: 1,
      max_value: 65535,
      custom_rules: ['port_available', 'unique_port']
    };

    rules.prowlarr_port = {
      field: 'prowlarr_port',
      type: 'number',
      required: true,
      min_value: 1,
      max_value: 65535,
      custom_rules: ['port_available', 'unique_port']
    };

    rules.radarr_port = {
      field: 'radarr_port',
      type: 'number',
      required: true,
      min_value: 1,
      max_value: 65535,
      custom_rules: ['port_available', 'unique_port']
    };

    rules.sonarr_port = {
      field: 'sonarr_port',
      type: 'number',
      required: true,
      min_value: 1,
      max_value: 65535,
      custom_rules: ['port_available', 'unique_port']
    };

    rules.jellyfin_port = {
      field: 'jellyfin_port',
      type: 'number',
      required: true,
      min_value: 1,
      max_value: 65535,
      custom_rules: ['port_available', 'unique_port']
    };

    rules.jellyseerr_port = {
      field: 'jellyseerr_port',
      type: 'number',
      required: true,
      min_value: 1,
      max_value: 65535,
      custom_rules: ['port_available', 'unique_port']
    };

    // Service API key validation rules
    rules.prowlarr_api_key = {
      field: 'prowlarr_api_key',
      type: 'string',
      required: false,
      min_length: 20,
      max_length: 100,
      custom_rules: ['api_key_format']
    };

    rules.radarr_api_key = {
      field: 'radarr_api_key',
      type: 'string',
      required: false,
      min_length: 20,
      max_length: 100,
      custom_rules: ['api_key_format']
    };

    rules.sonarr_api_key = {
      field: 'sonarr_api_key',
      type: 'string',
      required: false,
      min_length: 20,
      max_length: 100,
      custom_rules: ['api_key_format']
    };

    rules.qbittorrent_host = {
      field: 'qbittorrent_host',
      type: 'string',
      required: true,
      min_length: 3,
      max_length: 255,
      custom_rules: ['hostname_format']
    };

    return rules;
  }, []);

  // Update dynamic steps based on configuration
  useEffect(() => {
    const dynamicSteps: StepConfig[] = [
      {
        id: 'welcome',
        title: 'Welcome',
        component: WelcomeStep,
        fields: [],
        dependencies: [],
        canProceed: true,
        optional: false
      },
      {
        id: 'admin',
        title: 'Admin Account',
        component: AdminAccountStep,
        fields: ['admin_password', 'admin_confirmPassword'],
        dependencies: [],
        canProceed: canProceed(['admin_password', 'admin_confirmPassword']),
        optional: false
      },
      {
        id: 'paths',
        title: 'Storage Configuration',
        component: PathSelectionStep,
        fields: ['media_path', 'downloads_path', 'appdata_path', 'scratch_path'],
        dependencies: [],
        canProceed: canProceed(['media_path', 'downloads_path', 'appdata_path']),
        optional: false
      },
      {
        id: 'network',
        title: 'Network Configuration',
        component: NetworkConfigurationStep,
        fields: ['qbittorrent_port', 'prowlarr_port', 'radarr_port', 'sonarr_port', 'jellyfin_port', 'jellyseerr_port'],
        dependencies: ['qbittorrent_host'],
        canProceed: canProceed(['qbittorrent_port', 'prowlarr_port', 'radarr_port', 'sonarr_port', 'jellyfin_port', 'jellyseerr_port']),
        optional: false
      },
      {
        id: 'services',
        title: 'Service Configuration',
        component: ServiceConfigurationStep,
        fields: ['prowlarr_api_key', 'radarr_api_key', 'sonarr_api_key'],
        dependencies: ['prowlarr_port', 'radarr_port', 'sonarr_port'],
        canProceed: true, // Services are optional initially
        optional: true
      },
      {
        id: 'verify',
        title: 'Verify Configuration',
        component: VerificationStep,
        fields: [],
        dependencies: [],
        canProceed: validationState.overallSuccess || validationState.warnings.length === 0,
        optional: false
      },
      {
        id: 'review',
        title: 'Review & Apply',
        component: ReviewStep,
        fields: [],
        dependencies: ['verify'],
        canProceed: validationState.overallSuccess,
        optional: false
      }
    ];

    setSteps(dynamicSteps);
  }, [canProceed, getClientSideRules, validationState.overallSuccess]);

  // Event handlers
  const loadVolumes = async () => {
    try {
      const response = await fetch('/api/system/volumes');
      const data = await response.json();
      setVolumes(data.volumes || []);
    } catch (error) {
      console.error('Failed to load volumes:', error);
    }
  };

  const handleVolumeSelect = (volume: Volume) => {
    setPathConfig(prev => ({
      ...prev,
      selectedVolume: volume,
      media_path: volume.suggested_paths.media,
      appdata_path: volume.suggested_paths.appdata,
      downloads_path: pathConfig.useScratch ? volume.suggested_paths.downloads : prev.downloads_path,
      scratch_path: pathConfig.useScratch ? volume.suggested_paths.downloads : prev.scratch_path
    }));
  };

  const handleNext = async () => {
    if (currentStep < steps.length - 1 && steps[currentStep].canProceed) {
      setCurrentStep(prev => prev + 1);
      clearValidation(); // Clear validation for next step
    }
  };

  const handleBack = () => {
    if (currentStep > 0) {
      setCurrentStep(prev => prev - 1);
    }
  };

  const handleApply = async () => {
    setIsLoading(true);
    
    try {
      const fullConfig = {
        // Combine all configuration
        admin_account: adminAccount,
        paths: pathConfig,
        network: networkConfig,
        services: serviceConfig,
        volumes: volumes
      };

      const result = await validateConfiguration(fullConfig, {
        partial: false,
        skip_service_checks: true // Skip service checks for initial apply
      });

      if (result.success && result.result?.success) {
        // Apply the configuration
        const applyResponse = await fetch('/api/setup/initialize', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            admin_username: adminAccount.username,
            admin_password: adminAccount.password,
            pool_path: pathConfig.media_path,
            scratch_path: pathConfig.useScratch ? pathConfig.scratch_path : undefined,
            appdata_path: pathConfig.appdata_path
          })
        });

        if (applyResponse.ok) {
          setCurrentStep(steps.length); // Success step
        } else {
          const errorData = await applyResponse.json();
          throw new Error(errorData.message || 'Failed to apply configuration');
        }
      }
    } catch (error) {
      console.error('Failed to apply configuration:', error);
    } finally {
      setIsLoading(false);
    }
  };

  const CurrentStepComponent = steps[currentStep]?.component;

  return (
    <div className="enhanced-setup-wizard">
      {/* Progress Bar */}
      <div className="wizard-progress">
        {steps.map((step, index) => (
          <div
            key={step.id}
            className={`progress-step ${
              index === currentStep ? 'active' : 
              index < currentStep ? 'completed' : 
              step.optional && index > currentStep ? 'skipped' : ''
            }`}
          >
            <div className="step-number">
              {index < currentStep ? '✓' : step.optional ? '○' : index + 1}
            </div>
            <div className="step-label">{step.title}</div>
            {step.dependencies.length > 0 && (
              <div className="step-dependencies">
                Requires: {step.dependencies.join(', ')}
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Validation Status */}
      {(validationState.isValidating || validationState.errors.length > 0) && (
        <div className={`validation-status ${validationState.isValidating ? 'validating' : 'has-errors'}`}>
          {validationState.isValidating && (
            <div className="validating-message">
              <span className="spinner">⟳</span>
              Validating configuration...
            </div>
          )}
          
          {validationState.errors.length > 0 && (
            <div className="error-summary">
              <h4>Validation Issues Found</h4>
              <ul>
                {validationState.errors.map((error, index) => (
                  <li key={index}>
                    <strong>{error.field}:</strong> {error.message}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* Step Content */}
      <div className="wizard-content">
        {CurrentStepComponent && (
          <CurrentStepComponent
            // Pass props based on current step
            {...(currentStep.id === 'welcome' && {})}
            {...(currentStep.id === 'admin' && {
              adminAccount,
              setAdminAccount,
              getFieldValidation,
              debouncedValidate
            })}
            {...(currentStep.id === 'paths' && {
              pathConfig,
              setPathConfig,
              volumes,
              handleVolumeSelect,
              getFieldValidation,
              debouncedValidate
            })}
            {...(currentStep.id === 'network' && {
              networkConfig,
              setNetworkConfig,
              getFieldValidation,
              debouncedValidate
            })}
            {...(currentStep.id === 'services' && {
              serviceConfig,
              setServiceConfig,
              getFieldValidation,
              debouncedValidate
            })}
            {...(currentStep.id === 'verify' && {
              validationState,
              fullConfig: {
                admin_account: adminAccount,
                paths: pathConfig,
                network: networkConfig,
                services: serviceConfig
              },
              validateConfiguration
            })}
            {...(currentStep.id === 'review' && {
              adminAccount,
              pathConfig,
              networkConfig,
              serviceConfig,
              validationState,
              onApply: handleApply,
              isLoading
            })}
          />
        )}
      </div>

      {/* Navigation */}
      <div className="wizard-navigation">
        {currentStep > 0 && (
          <button
            onClick={handleBack}
            disabled={isLoading}
            className="secondary-button"
          >
            ← Back
          </button>
        )}
        
        {currentStep < steps.length - 1 ? (
          <button
            onClick={handleNext}
            disabled={!steps[currentStep].canProceed || isLoading || validationState.isValidating}
            className="primary-button"
          >
            {validationState.isValidating ? 'Validating...' : 'Next →'}
          </button>
        ) : (
          <button
            onClick={handleApply}
            disabled={!validationState.overallSuccess || isLoading || validationState.isValidating}
            className="apply-button"
          >
            {isLoading ? 'Applying...' : 'Apply Configuration'}
          </button>
        )}
      </div>

      {/* CSS Styles */}
      <style>{`
        .enhanced-setup-wizard {
          max-width: 900px;
          margin: 0 auto;
          padding: 2rem;
          background: #1e1e2e;
          border-radius: 16px;
          box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
        }

        .wizard-progress {
          display: flex;
          justify-content: space-between;
          margin-bottom: 2rem;
          padding: 0 1rem;
          border-bottom: 1px solid #333;
        }

        .progress-step {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 0.5rem;
          flex: 1;
          position: relative;
        }

        .step-number {
          width: 32px;
          height: 32px;
          border-radius: 50%;
          background: #333;
          color: #888;
          display: flex;
          align-items: center;
          justify-content: center;
          font-weight: bold;
          font-size: 0.9rem;
          transition: all 0.3s ease;
        }

        .progress-step.active .step-number {
          background: #5c9ceb;
          color: white;
          transform: scale(1.1);
        }

        .progress-step.completed .step-number {
          background: #4caf50;
          color: white;
        }

        .progress-step.skipped .step-number {
          background: #666;
          color: #999;
        }

        .step-label {
          font-size: 0.8rem;
          color: #aaa;
          text-transform: uppercase;
          letter-spacing: 0.05em;
        }

        .progress-step.active .step-label {
          color: #5c9ceb;
        }

        .step-dependencies {
          font-size: 0.7rem;
          color: #666;
          margin-top: 0.25rem;
        }

        .validation-status {
          margin-bottom: 1.5rem;
          padding: 1rem;
          border-radius: 8px;
        }

        .validation-status.validating {
          background: rgba(92, 156, 235, 0.1);
          border: 1px solid rgba(92, 156, 235, 0.3);
        }

        .validation-status.has-errors {
          background: rgba(244, 67, 54, 0.1);
          border: 1px solid rgba(244, 67, 54, 0.3);
        }

        .validating-message {
          display: flex;
          align-items: center;
          gap: 0.5rem;
          color: #5c9ceb;
          font-weight: 500;
        }

        .spinner {
          animation: spin 1s linear infinite;
        }

        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }

        .error-summary h4 {
          color: #f44336;
          margin: 0 0 0.5rem 0;
        }

        .error-summary ul {
          margin: 0;
          padding-left: 1.5rem;
        }

        .error-summary li {
          color: #ff6b6b;
          margin-bottom: 0.25rem;
        }

        .wizard-content {
          min-height: 400px;
          margin-bottom: 2rem;
        }

        .wizard-navigation {
          display: flex;
          justify-content: space-between;
          gap: 1rem;
        }

        .primary-button {
          background: #5c9ceb;
          color: white;
          border: none;
          padding: 0.75rem 2rem;
          border-radius: 8px;
          font-size: 1rem;
          font-weight: 500;
          cursor: pointer;
          transition: all 0.2s ease;
        }

        .primary-button:hover:not(:disabled) {
          background: #4a90e2;
          transform: translateY(-1px);
        }

        .primary-button:disabled {
          opacity: 0.5;
          cursor: not-allowed;
          transform: none;
        }

        .secondary-button {
          background: transparent;
          color: #aaa;
          border: 1px solid #555;
          padding: 0.75rem 1.5rem;
          border-radius: 8px;
          font-size: 1rem;
          cursor: pointer;
          transition: all 0.2s ease;
        }

        .secondary-button:hover:not(:disabled) {
          color: #fff;
          background: #555;
        }

        .apply-button {
          background: linear-gradient(45deg, #4caf50, #45a049);
          color: white;
          border: none;
          padding: 0.75rem 2rem;
          border-radius: 8px;
          font-size: 1rem;
          font-weight: 600;
          cursor: pointer;
          transition: all 0.3s ease;
        }

        .apply-button:hover:not(:disabled) {
          transform: translateY(-2px);
          box-shadow: 0 4px 12px rgba(76, 175, 80, 0.3);
        }

        .apply-button:disabled {
          opacity: 0.5;
          cursor: not-allowed;
          transform: none;
        }
      `}</style>
    </div>
  );
}

// Step Components (simplified for now)
const WelcomeStep = () => (
  <div className="step-welcome">
    <h2>🚀 Welcome to NAS Orchestrator</h2>
    <p>Let's get your media automation stack set up with intelligent validation and real-time feedback.</p>
    <div className="feature-highlights">
      <div className="highlight">✓ Real-time validation</div>
      <div className="highlight">✓ Smart error detection</div>
      <div className="highlight">✓ Safe configuration testing</div>
      <div className="highlight">✓ Progressive setup</div>
    </div>
  </div>
);

const AdminAccountStep = ({ adminAccount, setAdminAccount, getFieldValidation, debouncedValidate }) => (
  <div className="step-admin">
    <h2>Create Admin Account</h2>
    
    <div className="form-group">
      <label>Username</label>
      <input
        type="text"
        value={adminAccount.username}
        onChange={(e) => setAdminAccount(prev => ({ ...prev, username: e.target.value }))}
        placeholder="admin"
      />
    </div>
    
    <div className="form-group">
      <label>Password</label>
      <input
        type="password"
        value={adminAccount.password}
        onChange={(e) => {
          setAdminAccount(prev => ({ ...prev, password: e.target.value }));
          debouncedValidate('admin_password', e.target.value);
        }}
        placeholder="Enter a strong password"
        className={getFieldValidation('admin_password').error ? 'error' : ''}
      />
      {getFieldValidation('admin_password').error && (
        <span className="field-error">{getFieldValidation('admin_password').error}</span>
      )}
    </div>
    
    <div className="form-group">
      <label>Confirm Password</label>
      <input
        type="password"
        value={adminAccount.confirmPassword}
        onChange={(e) => {
          setAdminAccount(prev => ({ ...prev, confirmPassword: e.target.value }));
          debouncedValidate('admin_confirmPassword', e.target.value);
        }}
        placeholder="Confirm your password"
        className={getFieldValidation('admin_confirmPassword').error ? 'error' : ''}
      />
      {getFieldValidation('admin_confirmPassword').error && (
        <span className="field-error">{getFieldValidation('admin_confirmPassword').error}</span>
      )}
    </div>
  </div>
);

const PathSelectionStep = ({ pathConfig, setPathConfig, volumes, handleVolumeSelect, getFieldValidation, debouncedValidate }) => (
  <div className="step-paths">
    <h2>Select Storage Configuration</h2>
    
    <div className="volume-selection">
      <h3>Available Volumes</h3>
      <div className="volume-grid">
        {volumes.map((volume) => (
          <div
            key={volume.device}
            className={`volume-card ${pathConfig.selectedVolume?.device === volume.device ? 'selected' : ''}`}
            onClick={() => handleVolumeSelect(volume)}
          >
            <div className="volume-header">
              <span className="volume-device">{volume.device}</span>
              <span className="volume-filesystem">{volume.filesystem}</span>
            </div>
            <div className="volume-mountpoint">{volume.mountpoint}</div>
            <div className="volume-stats">
              <span>Size: {volume.size}</span>
              <span>Available: {volume.available}</span>
            </div>
            <div className="volume-suggested">
              <strong>Suggested paths:</strong>
              <ul>
                <li>Media: {volume.suggested_paths.media}</li>
                <li>Downloads: {volume.suggested_paths.downloads}</li>
                <li>AppData: {volume.suggested_paths.appdata}</li>
              </ul>
            </div>
          </div>
        ))}
      </div>
    </div>
    
    <div className="path-inputs">
      <h3>Path Configuration</h3>
      <div className="form-group">
        <label>Media Library Path</label>
        <input
          type="text"
          value={pathConfig.media_path}
          onChange={(e) => {
            setPathConfig(prev => ({ ...prev, media_path: e.target.value }));
            debouncedValidate('media_path', e.target.value);
          }}
          placeholder="/mnt/media"
          className={getFieldValidation('media_path').error ? 'error' : ''}
        />
        {getFieldValidation('media_path').error && (
          <span className="field-error">{getFieldValidation('media_path').error}</span>
        )}
      </div>
      
      {/* Similar form groups for downloads, appdata, scratch */}
    </div>
  </div>
);

const NetworkConfigurationStep = ({ networkConfig, setNetworkConfig, getFieldValidation, debouncedValidate }) => (
  <div className="step-network">
    <h2>Network Configuration</h2>
    
    <div className="port-grid">
      <div className="port-input-group">
        <label>qBittorrent Port</label>
        <input
          type="number"
          value={networkConfig.qbittorrent}
          onChange={(e) => {
            setNetworkConfig(prev => ({ ...prev, qbittorrent: parseInt(e.target.value) }));
            debouncedValidate('qbittorrent_port', e.target.value);
          }}
          min="1"
          max="65535"
          className={getFieldValidation('qbittorrent_port').error ? 'error' : ''}
        />
        {getFieldValidation('qbittorrent_port').error && (
          <span className="field-error">{getFieldValidation('qbittorrent_port').error}</span>
        )}
      </div>
      
      {/* Similar port inputs for other services */}
    </div>
  </div>
);

const ServiceConfigurationStep = ({ serviceConfig, setServiceConfig, getFieldValidation, debouncedValidate }) => (
  <div className="step-services">
    <h2>Service Configuration (Optional)</h2>
    <p>Configure services for optimal automation. These can be configured later.</p>
    
    <div className="service-grid">
      {/* API key inputs for services */}
    </div>
  </div>
);

const VerificationStep = ({ validationState, fullConfig, validateConfiguration }) => {
  const [verifying, setVerifying] = useState(false);

  const handleVerify = async () => {
    setVerifying(true);
    await validateConfiguration(fullConfig, {
      partial: false,
      skip_service_checks: false
    });
    setVerifying(false);
  };

  return (
    <div className="step-verify">
      <h2>Verify Configuration</h2>
      
      <div className="verification-actions">
        <button
          onClick={handleVerify}
          disabled={verifying || validationState.isValidating}
          className="verify-button"
        >
          {verifying ? 'Verifying...' : 'Verify Configuration'}
        </button>
      </div>
      
      {validationState.errors.length > 0 && (
        <div className="verification-errors">
          <h3>Issues Found</h3>
          <ul>
            {validationState.errors.map((error, index) => (
              <li key={index}>
                <strong>{error.field}:</strong> {error.message}
              </li>
            ))}
          </ul>
        </div>
      )}
      
      {validationState.warnings.length > 0 && (
        <div className="verification-warnings">
          <h3>Warnings</h3>
          <ul>
            {validationState.warnings.map((warning, index) => (
              <li key={index}>
                <strong>{warning.field}:</strong> {warning.message}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
};

const ReviewStep = ({ adminAccount, pathConfig, networkConfig, serviceConfig, validationState, onApply, isLoading }) => (
  <div className="step-review">
    <h2>Review & Apply Configuration</h2>
    
    <div className="review-summary">
      <div className="review-section">
        <h3>Configuration Summary</h3>
        <div className="summary-grid">
          <div className="summary-item">
            <label>Admin Username:</label>
            <span>{adminAccount.username}</span>
          </div>
          <div className="summary-item">
            <label>Media Path:</label>
            <span>{pathConfig.media_path}</span>
          </div>
          {/* More summary items */}
        </div>
      </div>
      
      <div className="validation-status">
        {validationState.overallSuccess ? (
          <div className="success-message">
            ✓ Configuration is valid and ready to apply!
          </div>
        ) : (
          <div className="issues-message">
            ⚠ Please fix validation issues before applying
          </div>
        )}
      </div>
    </div>
    
    <button
      onClick={onApply}
      disabled={!validationState.overallSuccess || isLoading}
      className="apply-button"
    >
      {isLoading ? 'Applying...' : 'Apply Configuration'}
    </button>
  </div>
);