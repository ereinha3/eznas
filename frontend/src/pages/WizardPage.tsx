import { useState, useEffect, useRef, useCallback } from 'react';
import { initializeSystem, validatePath, browsePath } from '../api';
import { PasswordStrength } from '../components/common/PasswordStrength';

interface WizardPageProps {
  onSetupComplete: () => void;
}

interface PathState {
  value: string;
  valid: boolean;
  message: string;
  exists: boolean;
  isValidating: boolean;
  showAutocomplete: boolean;
  autocompleteItems: Array<{name: string; path: string; writable: boolean}>;
}

// Service definitions for the selection step
const AVAILABLE_SERVICES = [
  {
    id: 'qbittorrent' as const,
    name: 'qBittorrent',
    description: 'BitTorrent download client',
    icon: '⬇️',
    defaultPort: 8080,
    required: true,
  },
  {
    id: 'radarr' as const,
    name: 'Radarr',
    description: 'Movie collection manager',
    icon: '🎬',
    defaultPort: 7878,
    required: false,
  },
  {
    id: 'sonarr' as const,
    name: 'Sonarr',
    description: 'TV series collection manager',
    icon: '📺',
    defaultPort: 8989,
    required: false,
  },
  {
    id: 'prowlarr' as const,
    name: 'Prowlarr',
    description: 'Indexer manager for *arr apps',
    icon: '🔍',
    defaultPort: 9696,
    required: false,
  },
  {
    id: 'jellyfin' as const,
    name: 'Jellyfin',
    description: 'Media server & streaming',
    icon: '🖥️',
    defaultPort: 8096,
    required: false,
  },
  {
    id: 'jellyseerr' as const,
    name: 'Jellyseerr',
    description: 'Media request management',
    icon: '📋',
    defaultPort: 5055,
    required: false,
  },
  {
    id: 'pipeline' as const,
    name: 'Pipeline',
    description: 'Auto-remux & organize downloads',
    icon: '⚡',
    defaultPort: null,
    required: false,
  },
] as const;

type ServiceId = typeof AVAILABLE_SERVICES[number]['id'];

const TOTAL_STEPS = 4;

export function WizardPage({ onSetupComplete }: WizardPageProps) {
  const [step, setStep] = useState(1);
  const [formData, setFormData] = useState({
    admin_username: 'admin',
    admin_password: '',
  });

  // Path states with real-time validation
  const [poolPath, setPoolPath] = useState<PathState>({
    value: '/mnt/pool/media',
    valid: true,
    message: '',
    exists: false,
    isValidating: false,
    showAutocomplete: false,
    autocompleteItems: []
  });

  const [appdataPath, setAppdataPath] = useState<PathState>({
    value: '/mnt/pool/appdata',
    valid: true,
    message: '',
    exists: false,
    isValidating: false,
    showAutocomplete: false,
    autocompleteItems: []
  });

  // Service selection state
  const [enabledServices, setEnabledServices] = useState<Set<ServiceId>>(
    new Set(['qbittorrent', 'radarr', 'sonarr', 'prowlarr', 'jellyfin', 'jellyseerr', 'pipeline'])
  );

  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [deployLog, setDeployLog] = useState<string[]>([]);
  const [showPermissionsHelp, setShowPermissionsHelp] = useState(false);

  // Dialog states
  const [showCreateDialog, setShowCreateDialog] = useState<{type: 'pool' | 'appdata'; path: string} | null>(null);
  const [showExistingDialog, setShowExistingDialog] = useState<{type: 'pool' | 'appdata'; path: string} | null>(null);

  // Refs for debouncing
  const poolTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const appdataTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const poolInputRef = useRef<HTMLInputElement>(null);
  const appdataInputRef = useRef<HTMLInputElement>(null);

  const toggleService = (id: ServiceId) => {
    const svc = AVAILABLE_SERVICES.find(s => s.id === id);
    if (svc?.required) return; // Can't disable required services
    setEnabledServices(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // Debounced validation function
  const validatePathDebounced = useCallback(async (
    path: string,
    type: 'pool' | 'appdata',
    autoCreate: boolean = false
  ) => {
    const setState = type === 'pool' ? setPoolPath : setAppdataPath;

    if (!path) {
      setState(prev => ({
        ...prev,
        valid: false,
        message: 'Path is required',
        isValidating: false
      }));
      return;
    }

    setState(prev => ({ ...prev, isValidating: true, showAutocomplete: false }));

    try {
      const result = await validatePath(path, true, autoCreate);

      const message = result.valid
        ? (result.warning || 'Path is valid')
        : (result.error || 'Path validation failed');

      setState(prev => ({
        ...prev,
        valid: result.valid,
        message: message,
        exists: result.exists || false,
        isValidating: false
      }));

      if (!result.valid && !result.exists && result.error?.includes('does not exist')) {
        setShowCreateDialog({ type, path });
      }

      if (result.exists && !autoCreate) {
        setShowExistingDialog({ type, path });
      }
    } catch (err: any) {
      setState(prev => ({
        ...prev,
        valid: false,
        message: err.message || 'Validation failed',
        isValidating: false
      }));
    }
  }, []);

  // Autocomplete function
  const loadAutocomplete = useCallback(async (path: string, type: 'pool' | 'appdata') => {
    const setState = type === 'pool' ? setPoolPath : setAppdataPath;

    if (!path || path === '/') {
      try {
        const result = await browsePath('/');
        setState(prev => ({
          ...prev,
          autocompleteItems: result.directories.slice(0, 10),
          showAutocomplete: true
        }));
      } catch {
        setState(prev => ({ ...prev, showAutocomplete: false }));
      }
      return;
    }

    const parentPath = path.split('/').slice(0, -1).join('/') || '/';
    const searchTerm = path.split('/').pop()?.toLowerCase() || '';

    try {
      const result = await browsePath(parentPath);
      const filtered = result.directories
        .filter(dir => dir.name.toLowerCase().includes(searchTerm))
        .slice(0, 10);

      setState(prev => ({
        ...prev,
        autocompleteItems: filtered,
        showAutocomplete: filtered.length > 0
      }));
    } catch {
      setState(prev => ({ ...prev, showAutocomplete: false }));
    }
  }, []);

  const handlePathChange = (type: 'pool' | 'appdata', value: string) => {
    const setState = type === 'pool' ? setPoolPath : setAppdataPath;
    const timeoutRef = type === 'pool' ? poolTimeoutRef : appdataTimeoutRef;

    setState(prev => ({ ...prev, value, valid: true, message: '' }));

    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    timeoutRef.current = setTimeout(() => {
      loadAutocomplete(value, type);
    }, 200);
  };

  const handlePathBlur = (type: 'pool' | 'appdata') => {
    const pathState = type === 'pool' ? poolPath : appdataPath;
    const timeoutRef = type === 'pool' ? poolTimeoutRef : appdataTimeoutRef;

    if (timeoutRef.current) clearTimeout(timeoutRef.current);

    setTimeout(() => {
      const setState = type === 'pool' ? setPoolPath : setAppdataPath;
      setState(prev => ({ ...prev, showAutocomplete: false }));
    }, 200);

    validatePathDebounced(pathState.value, type, false);
  };

  const selectAutocompleteItem = (type: 'pool' | 'appdata', path: string) => {
    const setState = type === 'pool' ? setPoolPath : setAppdataPath;
    setState(prev => ({ ...prev, value: path, showAutocomplete: false }));
    validatePathDebounced(path, type, false);
  };

  const handleCreateDirectory = async (type: 'pool' | 'appdata') => {
    const path = type === 'pool' ? poolPath.value : appdataPath.value;
    setShowCreateDialog(null);
    await validatePathDebounced(path, type, true);
  };

  const handleAcceptExisting = (type: 'pool' | 'appdata') => {
    const setState = type === 'pool' ? setPoolPath : setAppdataPath;
    setShowExistingDialog(null);
    setState(prev => ({ ...prev, valid: true, message: 'Using existing directory' }));
  };

  const canProceed = (): boolean => {
    switch (step) {
      case 1: return formData.admin_username.trim() !== '' && formData.admin_password.length >= 8;
      case 2: return poolPath.valid && appdataPath.valid && !poolPath.isValidating && !appdataPath.isValidating;
      case 3: return enabledServices.size > 0;
      case 4: return true;
      default: return false;
    }
  };

  const handleNext = () => {
    if (step < TOTAL_STEPS) {
      setStep(step + 1);
      setError(null);
    }
  };

  const handleDeploy = async () => {
    setIsLoading(true);
    setError(null);
    setDeployLog(['Initializing system...']);

    try {
      const response = await initializeSystem({
        admin_username: formData.admin_username,
        admin_password: formData.admin_password,
        pool_path: poolPath.value,
        appdata_path: appdataPath.value,
        enabled_services: Array.from(enabledServices),
      });

      if (response.success) {
        setDeployLog(prev => [...prev, 'System initialized successfully!']);
        setDeployLog(prev => [...prev, 'Setup complete. Redirecting to login...']);
        setTimeout(() => onSetupComplete(), 2000);
      } else {
        // Permission errors include the fix command — show it prominently
        const msg = response.message || 'Setup failed';
        setError(msg);
        setDeployLog(prev => [...prev, `Error: ${msg}`]);
        if (response.config_created) {
          setDeployLog(prev => [...prev,
            'Your account and config were saved. Fix the permissions above, then click Deploy again.'
          ]);
        }
      }
    } catch (err: any) {
      setError(err.message || 'Setup failed');
      setDeployLog(prev => [...prev, `Error: ${err.message}`]);
    } finally {
      setIsLoading(false);
    }
  };

  // Cleanup timeouts on unmount
  useEffect(() => {
    return () => {
      if (poolTimeoutRef.current) clearTimeout(poolTimeoutRef.current);
      if (appdataTimeoutRef.current) clearTimeout(appdataTimeoutRef.current);
    };
  }, []);

  // Validate paths immediately when entering step 2
  useEffect(() => {
    if (step === 2) {
      setTimeout(() => {
        validatePath(poolPath.value, true, false).then(result => {
          const message = result.valid
            ? (result.warning || 'Path is valid')
            : (result.error || 'Path validation failed');
          setPoolPath(prev => ({
            ...prev,
            valid: result.valid,
            message: message,
            exists: result.exists || false,
            isValidating: false
          }));
        });

        validatePath(appdataPath.value, true, false).then(result => {
          const message = result.valid
            ? (result.warning || 'Path is valid')
            : (result.error || 'Path validation failed');
          setAppdataPath(prev => ({
            ...prev,
            valid: result.valid,
            message: message,
            exists: result.exists || false,
            isValidating: false
          }));
        });
      }, 100);
    }
  }, [step]);

  return (
    <div className="wizard-container">
      <div className="wizard-box">
        <div className="wizard-header">
          <h1>NAS Orchestrator Setup</h1>
          <p>Let's get your media stack configured.</p>
        </div>

        {/* Step Progress Indicator */}
        <div className="wizard-progress">
          {['Account', 'Storage', 'Services', 'Deploy'].map((label, i) => (
            <div key={i} className={`progress-step${step > i + 1 ? ' done' : step === i + 1 ? ' active' : ''}`}>
              <div className="progress-dot">
                {step > i + 1 ? '\u2713' : i + 1}
              </div>
              <span className="progress-label">{label}</span>
            </div>
          ))}
        </div>

        {error && (
          <div className="alert alert-error" style={{ whiteSpace: 'pre-wrap', fontFamily: error.includes('sudo') ? 'var(--font-mono, monospace)' : 'inherit' }}>
            {error}
          </div>
        )}

        {/* Step 1: Admin Account */}
        {step === 1 && (
          <div className="wizard-step">
            <h2>Create Admin Account</h2>
            <div className="form-group">
              <label>Username</label>
              <input
                type="text"
                value={formData.admin_username}
                onChange={(e) => setFormData({...formData, admin_username: e.target.value})}
                required
              />
            </div>
            <div className="form-group">
              <label>Password</label>
              <input
                type="password"
                value={formData.admin_password}
                onChange={(e) => setFormData({...formData, admin_password: e.target.value})}
                required
                autoFocus
              />
              <PasswordStrength password={formData.admin_password} />
            </div>
          </div>
        )}

        {/* Step 2: Storage Configuration */}
        {step === 2 && (
          <div className="wizard-step">
            <h2>Storage Configuration</h2>

            <div className="permissions-info">
              <button
                type="button"
                className="help-toggle"
                onClick={() => setShowPermissionsHelp(!showPermissionsHelp)}
              >
                {showPermissionsHelp ? 'Hide' : 'Show'} Permission Setup Help
              </button>

              {showPermissionsHelp && (
                <div className="help-content">
                  <h4>Permission Setup</h4>
                  <div className="help-section">
                    <h5>Recommended: Use a Subdirectory</h5>
                    <p>Create a dedicated media folder:</p>
                    <pre className="code-block">
{`sudo mkdir -p /mnt/pool/media
sudo groupadd -g 1001 nas-users 2>/dev/null || true
sudo usermod -aG nas-users $USER
sudo chown -R :nas-users /mnt/pool/media
sudo chmod -R 775 /mnt/pool/media`}
                    </pre>
                  </div>
                </div>
              )}
            </div>

            <div className={`form-group ${!poolPath.valid && poolPath.message ? 'has-error' : poolPath.valid && poolPath.message ? 'has-success' : ''}`}>
              <label>Pool Path (Media Storage)</label>
              <div className="input-with-autocomplete">
                <input
                  ref={poolInputRef}
                  type="text"
                  value={poolPath.value}
                  onChange={(e) => handlePathChange('pool', e.target.value)}
                  onBlur={() => handlePathBlur('pool')}
                  onFocus={() => loadAutocomplete(poolPath.value, 'pool')}
                  placeholder="/mnt/pool/media"
                  required
                  autoFocus
                  className={poolPath.isValidating ? 'validating' : ''}
                />
                {poolPath.isValidating && <span className="validating-indicator">Validating...</span>}

                {poolPath.showAutocomplete && poolPath.autocompleteItems.length > 0 && (
                  <div className="autocomplete-dropdown">
                    {poolPath.autocompleteItems.map((item, index) => (
                      <div
                        key={`pool-${item.path}-${index}`}
                        className="autocomplete-item"
                        onClick={() => selectAutocompleteItem('pool', item.path)}
                      >
                        {item.name}
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <small className="hint">Path to your large storage pool (must be writable)</small>

              {poolPath.message && (
                <div className={`validation-message ${poolPath.valid ? 'success' : 'error'}`}>
                  {poolPath.valid ? '\u2713' : '\u2717'} {poolPath.message}
                </div>
              )}
            </div>

            <div className={`form-group ${!appdataPath.valid && appdataPath.message ? 'has-error' : appdataPath.valid && appdataPath.message ? 'has-success' : ''}`}>
              <label>AppData Path (Config)</label>
              <div className="input-with-autocomplete">
                <input
                  ref={appdataInputRef}
                  type="text"
                  value={appdataPath.value}
                  onChange={(e) => handlePathChange('appdata', e.target.value)}
                  onBlur={() => handlePathBlur('appdata')}
                  onFocus={() => loadAutocomplete(appdataPath.value, 'appdata')}
                  placeholder="/mnt/pool/appdata"
                  required
                  className={appdataPath.isValidating ? 'validating' : ''}
                />
                {appdataPath.isValidating && <span className="validating-indicator">Validating...</span>}

                {appdataPath.showAutocomplete && appdataPath.autocompleteItems.length > 0 && (
                  <div className="autocomplete-dropdown">
                    {appdataPath.autocompleteItems.map((item, index) => (
                      <div
                        key={`appdata-${item.path}-${index}`}
                        className="autocomplete-item"
                        onClick={() => selectAutocompleteItem('appdata', item.path)}
                      >
                        {item.name}
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <small className="hint">Where to store service configurations</small>

              {appdataPath.message && (
                <div className={`validation-message ${appdataPath.valid ? 'success' : 'error'}`}>
                  {appdataPath.valid ? '\u2713' : '\u2717'} {appdataPath.message}
                </div>
              )}
            </div>
          </div>
        )}

        {/* Step 3: Service Selection */}
        {step === 3 && (
          <div className="wizard-step">
            <h2>Select Services</h2>
            <p className="step-hint">Choose which services to deploy. You can change this later in Settings.</p>

            <div className="service-select-grid">
              {AVAILABLE_SERVICES.map((svc) => {
                const enabled = enabledServices.has(svc.id);
                return (
                  <div
                    key={svc.id}
                    className={`service-select-card${enabled ? ' selected' : ''}${svc.required ? ' required' : ''}`}
                    onClick={() => toggleService(svc.id)}
                  >
                    <div className="service-select-header">
                      <span className="service-select-icon">{svc.icon}</span>
                      <div className="service-select-toggle">
                        <div className={`mini-toggle${enabled ? ' on' : ''}`}>
                          <div className="mini-toggle-knob" />
                        </div>
                      </div>
                    </div>
                    <div className="service-select-name">{svc.name}</div>
                    <div className="service-select-desc">{svc.description}</div>
                    {svc.defaultPort && (
                      <div className="service-select-port">Port {svc.defaultPort}</div>
                    )}
                    {svc.required && (
                      <div className="service-select-required">Required</div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Step 4: Review & Deploy */}
        {step === 4 && (
          <div className="wizard-step">
            <h2>Review & Deploy</h2>

            <div className="review-section">
              <div className="review-item">
                <span className="review-label">Admin</span>
                <span className="review-value">{formData.admin_username}</span>
              </div>
              <div className="review-item">
                <span className="review-label">Pool Path</span>
                <span className="review-value mono">{poolPath.value}</span>
              </div>
              <div className="review-item">
                <span className="review-label">AppData Path</span>
                <span className="review-value mono">{appdataPath.value}</span>
              </div>
              <div className="review-item">
                <span className="review-label">Services</span>
                <span className="review-value">
                  {Array.from(enabledServices)
                    .map(id => AVAILABLE_SERVICES.find(s => s.id === id)?.name)
                    .filter(Boolean)
                    .join(', ')}
                </span>
              </div>
            </div>

            {deployLog.length > 0 && (
              <div className="deploy-log">
                {deployLog.map((line, i) => (
                  <div key={i} className="deploy-log-line">{line}</div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Navigation Buttons */}
        <div className="wizard-actions">
          {step > 1 && !isLoading && (
            <button type="button" onClick={() => setStep(step - 1)} className="secondary-button">
              Back
            </button>
          )}
          {step < TOTAL_STEPS ? (
            <button
              type="button"
              className="primary-button"
              onClick={handleNext}
              disabled={!canProceed()}
            >
              Next
            </button>
          ) : (
            <button
              type="button"
              className="primary-button deploy"
              onClick={handleDeploy}
              disabled={isLoading}
            >
              {isLoading ? 'Deploying...' : 'Deploy Stack'}
            </button>
          )}
        </div>
      </div>

      {/* Create Directory Dialog */}
      {showCreateDialog && (
        <div className="modal-overlay" onClick={() => setShowCreateDialog(null)}>
          <div className="modal" onClick={e => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Create Directory?</h3>
            </div>
            <div className="modal-body">
              <p>The directory <code>{showCreateDialog.path}</code> does not exist.</p>
              <p>Would you like to create it?</p>
            </div>
            <div className="modal-footer">
              <button className="secondary-button" onClick={() => setShowCreateDialog(null)}>Cancel</button>
              <button className="primary-button" onClick={() => handleCreateDirectory(showCreateDialog.type)}>
                Create Directory
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Existing Directory Warning Dialog */}
      {showExistingDialog && (
        <div className="modal-overlay" onClick={() => setShowExistingDialog(null)}>
          <div className="modal warning-modal" onClick={e => e.stopPropagation()}>
            <div className="modal-header warning">
              <h3>Directory Already Exists</h3>
            </div>
            <div className="modal-body">
              <p>The directory <code>{showExistingDialog.path}</code> already exists.</p>
              <p>NAS Orchestrator will use this directory. Existing files will remain untouched.</p>
              <div className="warning-options">
                <label className="radio-label">
                  <input type="radio" name="existing-action" defaultChecked onChange={() => handleAcceptExisting(showExistingDialog.type)} />
                  Use existing directory (recommended)
                </label>
                <label className="radio-label">
                  <input
                    type="radio"
                    name="existing-action"
                    onClick={() => {
                      setShowExistingDialog(null);
                      if (showExistingDialog.type === 'pool') {
                        setPoolPath(prev => ({ ...prev, value: '', valid: false, message: '' }));
                        poolInputRef.current?.focus();
                      } else {
                        setAppdataPath(prev => ({ ...prev, value: '', valid: false, message: '' }));
                        appdataInputRef.current?.focus();
                      }
                    }}
                  />
                  Choose a different path
                </label>
              </div>
            </div>
            <div className="modal-footer">
              <button className="primary-button" onClick={() => handleAcceptExisting(showExistingDialog.type)}>
                Use This Directory
              </button>
            </div>
          </div>
        </div>
      )}

      <style>{`
        .wizard-container {
          min-height: 100vh;
          display: flex;
          align-items: center;
          justify-content: center;
          background: var(--gradient-login, linear-gradient(135deg, #0f172a 0%, #1e293b 100%));
          color: var(--color-text-primary, #e2e8f0);
          padding: 2rem;
        }
        .wizard-box {
          background: #1e293b;
          padding: 2.5rem;
          border-radius: 1rem;
          width: 100%;
          max-width: 680px;
          box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
          border: 1px solid rgba(148, 163, 184, 0.1);
        }
        .wizard-header { text-align: center; margin-bottom: 1.5rem; }
        .wizard-header h1 { margin: 0 0 0.5rem; color: #fff; font-size: 1.5rem; }
        .wizard-header p { margin: 0; color: #94a3b8; }

        /* Progress Indicator */
        .wizard-progress {
          display: flex;
          justify-content: center;
          gap: 0.5rem;
          margin-bottom: 2rem;
          padding-bottom: 1.5rem;
          border-bottom: 1px solid rgba(148, 163, 184, 0.15);
        }
        .progress-step {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 0.35rem;
          flex: 1;
          max-width: 100px;
        }
        .progress-dot {
          width: 28px;
          height: 28px;
          border-radius: 50%;
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 0.75rem;
          font-weight: 700;
          background: rgba(148, 163, 184, 0.15);
          color: #64748b;
          border: 2px solid rgba(148, 163, 184, 0.25);
          transition: all 0.3s ease;
        }
        .progress-step.active .progress-dot {
          background: rgba(56, 189, 248, 0.2);
          color: #38bdf8;
          border-color: #38bdf8;
        }
        .progress-step.done .progress-dot {
          background: rgba(34, 197, 94, 0.2);
          color: #22c55e;
          border-color: #22c55e;
        }
        .progress-label {
          font-size: 0.7rem;
          color: #64748b;
          text-transform: uppercase;
          letter-spacing: 0.06em;
        }
        .progress-step.active .progress-label { color: #38bdf8; }
        .progress-step.done .progress-label { color: #22c55e; }

        .step-hint {
          color: #94a3b8;
          font-size: 0.85rem;
          margin: 0 0 1.25rem;
        }

        .form-group {
          margin-bottom: 1.5rem;
          display: flex;
          flex-direction: column;
          gap: 0.5rem;
          position: relative;
        }
        .form-group label { font-weight: 500; font-size: 0.9rem; color: #cbd5e1; }
        .input-with-autocomplete { position: relative; }
        .form-group input {
          width: 100%;
          padding: 0.75rem;
          background: #0f172a;
          border: 1px solid #334155;
          border-radius: 0.5rem;
          color: #fff;
          font-size: 1rem;
          transition: all 0.2s;
        }
        .form-group input:focus { outline: none; border-color: #38bdf8; box-shadow: 0 0 0 3px rgba(56, 189, 248, 0.1); }
        .form-group input.validating {
          padding-right: 2.5rem;
        }
        .validating-indicator {
          position: absolute;
          right: 0.75rem;
          top: 50%;
          transform: translateY(-50%);
          font-size: 0.75rem;
          color: #38bdf8;
        }
        .autocomplete-dropdown {
          position: absolute;
          top: 100%;
          left: 0;
          right: 0;
          background: #0f172a;
          border: 1px solid #334155;
          border-top: none;
          border-radius: 0 0 0.5rem 0.5rem;
          max-height: 200px;
          overflow-y: auto;
          z-index: 100;
          box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.5);
        }
        .autocomplete-item {
          padding: 0.75rem 1rem;
          cursor: pointer;
          transition: background 0.15s;
          border-bottom: 1px solid #1e293b;
        }
        .autocomplete-item:last-child { border-bottom: none; }
        .autocomplete-item:hover { background: rgba(56, 189, 248, 0.1); }
        .hint { color: #64748b; font-size: 0.8rem; }
        .validation-message {
          padding: 0.5rem 0.75rem;
          border-radius: 0.375rem;
          font-size: 0.85rem;
          display: flex;
          align-items: center;
          gap: 0.5rem;
        }
        .validation-message.success { background: rgba(74, 222, 128, 0.1); color: #4ade80; border: 1px solid rgba(74, 222, 128, 0.2); }
        .validation-message.error { background: rgba(239, 68, 68, 0.1); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.2); }
        .form-group.has-error input { border-color: #ef4444; }
        .form-group.has-success input { border-color: #4ade80; }

        /* Service Selection Grid */
        .service-select-grid {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(175px, 1fr));
          gap: 0.75rem;
        }
        .service-select-card {
          padding: 1rem;
          border-radius: 0.75rem;
          border: 1px solid rgba(148, 163, 184, 0.15);
          background: rgba(15, 23, 42, 0.6);
          cursor: pointer;
          transition: all 0.2s;
          display: flex;
          flex-direction: column;
          gap: 0.35rem;
        }
        .service-select-card:hover { border-color: rgba(56, 189, 248, 0.3); }
        .service-select-card.selected {
          border-color: rgba(56, 189, 248, 0.5);
          background: rgba(56, 189, 248, 0.08);
        }
        .service-select-card.required { cursor: default; }
        .service-select-header {
          display: flex;
          justify-content: space-between;
          align-items: center;
        }
        .service-select-icon { font-size: 1.5rem; }
        .mini-toggle {
          width: 32px;
          height: 18px;
          border-radius: 9px;
          background: rgba(148, 163, 184, 0.3);
          position: relative;
          transition: background 0.2s;
        }
        .mini-toggle.on { background: #38bdf8; }
        .mini-toggle-knob {
          position: absolute;
          width: 14px;
          height: 14px;
          border-radius: 50%;
          background: white;
          top: 2px;
          left: 2px;
          transition: transform 0.2s;
        }
        .mini-toggle.on .mini-toggle-knob { transform: translateX(14px); }
        .service-select-name { font-weight: 600; font-size: 0.9rem; color: #e2e8f0; }
        .service-select-desc { font-size: 0.75rem; color: #94a3b8; line-height: 1.3; }
        .service-select-port { font-size: 0.7rem; color: #64748b; font-family: monospace; }
        .service-select-required {
          font-size: 0.65rem;
          color: #38bdf8;
          text-transform: uppercase;
          letter-spacing: 0.08em;
        }

        /* Review Section */
        .review-section {
          display: flex;
          flex-direction: column;
          gap: 0.75rem;
          margin-bottom: 1.5rem;
        }
        .review-item {
          display: flex;
          justify-content: space-between;
          align-items: center;
          padding: 0.75rem 1rem;
          border-radius: 0.5rem;
          background: rgba(15, 23, 42, 0.6);
          border: 1px solid rgba(148, 163, 184, 0.12);
        }
        .review-label {
          font-size: 0.8rem;
          color: #94a3b8;
          text-transform: uppercase;
          letter-spacing: 0.06em;
        }
        .review-value { font-size: 0.9rem; color: #e2e8f0; }
        .review-value.mono { font-family: monospace; font-size: 0.85rem; }

        /* Deploy Log */
        .deploy-log {
          margin-top: 1rem;
          padding: 1rem;
          border-radius: 0.5rem;
          background: #0f172a;
          border: 1px solid rgba(148, 163, 184, 0.15);
          font-family: monospace;
          font-size: 0.82rem;
          max-height: 200px;
          overflow-y: auto;
        }
        .deploy-log-line {
          padding: 0.2rem 0;
          color: #94a3b8;
        }
        .deploy-log-line:last-child { color: #38bdf8; }

        /* Actions */
        .wizard-actions {
          display: flex;
          justify-content: flex-end;
          gap: 1rem;
          margin-top: 2rem;
        }
        .primary-button {
          background: #38bdf8;
          color: #0f172a;
          padding: 0.75rem 1.5rem;
          border-radius: 0.5rem;
          font-weight: 600;
          border: none;
          cursor: pointer;
          transition: all 0.2s;
          text-transform: none;
          letter-spacing: normal;
          box-shadow: none;
        }
        .primary-button:hover:not(:disabled) { background: #0ea5e9; transform: translateY(-1px); }
        .primary-button:disabled { opacity: 0.5; cursor: not-allowed; }
        .primary-button.deploy {
          background: linear-gradient(135deg, #22c55e, #38bdf8);
          color: #0f172a;
          padding: 0.85rem 2rem;
          font-size: 1rem;
        }
        .secondary-button {
          background: transparent;
          color: #94a3b8;
          padding: 0.75rem 1.5rem;
          border: 1px solid #334155;
          border-radius: 0.5rem;
          font-weight: 600;
          cursor: pointer;
          transition: all 0.2s;
          text-transform: none;
          letter-spacing: normal;
          box-shadow: none;
        }
        .secondary-button:hover { border-color: #475569; color: #e2e8f0; transform: none; }

        .alert-error {
          background: rgba(239, 68, 68, 0.1);
          border: 1px solid rgba(239, 68, 68, 0.2);
          color: #f87171;
          padding: 1rem;
          border-radius: 0.5rem;
          margin-bottom: 1.5rem;
        }

        /* Permission help (condensed) */
        .permissions-info {
          background: rgba(56, 189, 248, 0.1);
          border: 1px solid rgba(56, 189, 248, 0.2);
          border-radius: 0.5rem;
          padding: 1rem;
          margin-bottom: 1.5rem;
        }
        .help-toggle {
          background: transparent;
          border: none;
          color: #38bdf8;
          cursor: pointer;
          font-size: 0.9rem;
          padding: 0;
          text-decoration: underline;
          text-transform: none;
          letter-spacing: normal;
          box-shadow: none;
        }
        .help-toggle:hover { transform: none; }
        .help-content { margin-top: 1rem; padding-top: 1rem; border-top: 1px solid rgba(56, 189, 248, 0.2); }
        .help-content h4 { margin: 0 0 0.5rem; color: #38bdf8; }
        .help-section { margin-bottom: 1rem; }
        .help-section h5 { margin: 0 0 0.5rem; color: #e2e8f0; font-size: 0.95rem; }
        .help-content p { margin: 0.5rem 0; color: #94a3b8; font-size: 0.85rem; }
        .code-block {
          background: #0f172a;
          border: 1px solid #334155;
          border-radius: 0.375rem;
          padding: 0.75rem;
          font-family: monospace;
          font-size: 0.78rem;
          color: #e2e8f0;
          overflow-x: auto;
          white-space: pre-wrap;
          margin: 0.5rem 0;
        }

        /* Modals */
        .modal-overlay {
          position: fixed;
          inset: 0;
          background: rgba(0, 0, 0, 0.7);
          display: flex;
          align-items: center;
          justify-content: center;
          z-index: 1000;
          padding: 1rem;
        }
        .modal {
          background: #1e293b;
          border-radius: 0.75rem;
          border: 1px solid #334155;
          max-width: 500px;
          width: 100%;
          max-height: 90vh;
          overflow-y: auto;
        }
        .modal-header { padding: 1.25rem 1.5rem; border-bottom: 1px solid #334155; }
        .modal-header h3 { margin: 0; color: #e2e8f0; font-size: 1.1rem; }
        .modal-header.warning h3 { color: #f59e0b; }
        .modal-body { padding: 1.5rem; }
        .modal-body p { color: #e2e8f0; margin-bottom: 1rem; line-height: 1.6; }
        .modal-body code { background: #0f172a; padding: 0.125rem 0.375rem; border-radius: 0.25rem; font-family: monospace; color: #38bdf8; }
        .modal-footer { padding: 1rem 1.5rem; border-top: 1px solid #334155; display: flex; justify-content: flex-end; gap: 0.75rem; }
        .warning-options {
          background: rgba(245, 158, 11, 0.1);
          border: 1px solid rgba(245, 158, 11, 0.2);
          border-radius: 0.5rem;
          padding: 1rem;
          margin: 1rem 0;
        }
        .warning-options p { margin-top: 0; color: #f59e0b; }
        .radio-label {
          display: flex;
          align-items: center;
          gap: 0.5rem;
          color: #e2e8f0;
          cursor: pointer;
          margin-bottom: 0.75rem;
        }
        .radio-label:last-child { margin-bottom: 0; }
        .radio-label input[type="radio"] { width: auto; margin: 0; }
      `}</style>
    </div>
  );
}
