import React, { useState, useEffect } from 'react';
import { fetchSetupStatus, fetchVolumes, initializeSystem } from '../api';

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

interface SetupWizardProps {
  onComplete: () => void;
}

export function SetupWizard({ onComplete }: SetupWizardProps) {
  const [currentStep, setCurrentStep] = useState(0);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [volumes, setVolumes] = useState<Volume[]>([]);
  
  // Form data
  const [adminUsername, setAdminUsername] = useState('admin');
  const [adminPassword, setAdminPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [selectedVolume, setSelectedVolume] = useState<Volume | null>(null);
  const [poolPath, setPoolPath] = useState('');
  const [scratchPath, setScratchPath] = useState('');
  const [appdataPath, setAppdataPath] = useState('');
  const [useScratch, setUseScratch] = useState(true);

  // Load volumes on mount
  useEffect(() => {
    loadVolumes();
  }, []);

  const loadVolumes = async () => {
    try {
      const response = await fetchVolumes();
      setVolumes(response.volumes);
      
      // Auto-select first volume if available
      if (response.volumes.length > 0) {
        handleVolumeSelect(response.volumes[0]);
      }
    } catch (err) {
      setError('Failed to scan volumes. Please check system permissions.');
    }
  };

  const handleVolumeSelect = (volume: Volume) => {
    setSelectedVolume(volume);
    setPoolPath(volume.mountpoint);
    setAppdataPath(volume.suggested_paths.appdata);
    if (useScratch) {
      setScratchPath(volume.suggested_paths.downloads);
    }
  };

  const handleNext = () => {
    setError(null);
    
    // Validation
    if (currentStep === 1) {
      if (adminPassword.length < 8) {
        setError('Password must be at least 8 characters');
        return;
      }
      if (adminPassword !== confirmPassword) {
        setError('Passwords do not match');
        return;
      }
    }
    
    if (currentStep === 3) {
      if (!poolPath) {
        setError('Please enter a pool path');
        return;
      }
      if (!appdataPath) {
        setError('Please enter an appdata path');
        return;
      }
    }
    
    setCurrentStep(prev => prev + 1);
  };

  const handleBack = () => {
    setError(null);
    setCurrentStep(prev => prev - 1);
  };

  const handleInitialize = async () => {
    setIsLoading(true);
    setError(null);
    
    try {
      const response = await initializeSystem({
        admin_username: adminUsername,
        admin_password: adminPassword,
        pool_path: poolPath,
        scratch_path: useScratch ? scratchPath : undefined,
        appdata_path: appdataPath,
      });
      
      if (response.success) {
        setCurrentStep(5); // Success step
      } else {
        setError(response.message || 'Initialization failed');
      }
    } catch (err) {
      setError('Failed to initialize system. Please try again.');
    } finally {
      setIsLoading(false);
    }
  };

  const steps = [
    'Welcome',
    'Admin Account',
    'Select Volume',
    'Configure Paths',
    'Review',
    'Complete'
  ];

  return (
    <div className="setup-wizard">
      {/* Progress Bar */}
      <div className="wizard-progress">
        {steps.map((step, index) => (
          <div
            key={step}
            className={`progress-step ${
              index === currentStep ? 'active' : 
              index < currentStep ? 'completed' : ''
            }`}
          >
            <div className="step-number">
              {index < currentStep ? '✓' : index + 1}
            </div>
            <div className="step-label">{step}</div>
          </div>
        ))}
      </div>

      {/* Error Display */}
      {error && (
        <div className="wizard-error">
          {error}
        </div>
      )}

      {/* Step Content */}
      <div className="wizard-content">
        {currentStep === 0 && (
          <div className="step-welcome">
            <div className="welcome-icon">🚀</div>
            <h2>Welcome to NAS Orchestrator</h2>
            <p>
              Let's get your media automation stack set up. This wizard will guide you through
              creating your admin account and configuring your storage paths.
            </p>
            <div className="info-box">
              <h4>What you'll need:</h4>
              <ul>
                <li>A location for your media library (movies, TV shows)</li>
                <li>A location for downloads and temporary files</li>
                <li>A location for application data and configurations</li>
              </ul>
            </div>
          </div>
        )}

        {currentStep === 1 && (
          <div className="step-admin">
            <h2>Create Admin Account</h2>
            <p>This account will have full access to manage your NAS Orchestrator.</p>
            
            <div className="form-group">
              <label>Username</label>
              <input
                type="text"
                value={adminUsername}
                onChange={(e) => setAdminUsername(e.target.value)}
                placeholder="admin"
              />
            </div>
            
            <div className="form-group">
              <label>Password</label>
              <input
                type="password"
                value={adminPassword}
                onChange={(e) => setAdminPassword(e.target.value)}
                placeholder="Enter a strong password"
              />
              <span className="field-hint">Minimum 8 characters</span>
            </div>
            
            <div className="form-group">
              <label>Confirm Password</label>
              <input
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                placeholder="Confirm your password"
              />
            </div>
          </div>
        )}

        {currentStep === 2 && (
          <div className="step-volumes">
            <h2>Select Storage Volume</h2>
            <p>Choose the main storage volume for your media and downloads.</p>
            
            {volumes.length === 0 ? (
              <div className="warning-box">
                No volumes detected. Make sure the orchestrator has access to scan mounted volumes.
              </div>
            ) : (
              <div className="volume-list">
                {volumes.map((volume) => (
                  <div
                    key={volume.device}
                    className={`volume-card ${
                      selectedVolume?.device === volume.device ? 'selected' : ''
                    }`}
                    onClick={() => handleVolumeSelect(volume)}
                  >
                    <div className="volume-header">
                      <div className="volume-device">{volume.device}</div>
                      <div className="volume-filesystem">{volume.filesystem}</div>
                    </div>
                    <div className="volume-mountpoint">{volume.mountpoint}</div>
                    <div className="volume-stats">
                      <span>Size: {volume.size}</span>
                      <span>Available: {volume.available}</span>
                    </div>
                    <div className="volume-suggested">
                      Suggested paths:
                      <ul>
                        <li>Media: {volume.suggested_paths.media}</li>
                        <li>Downloads: {volume.suggested_paths.downloads}</li>
                        <li>AppData: {volume.suggested_paths.appdata}</li>
                      </ul>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {currentStep === 3 && (
          <div className="step-paths">
            <h2>Configure Storage Paths</h2>
            <p>Review and customize the storage locations for your media stack.</p>
            
            <div className="path-config">
              <div className="path-group">
                <label>Media Library Path (Pool)</label>
                <input
                  type="text"
                  value={poolPath}
                  onChange={(e) => setPoolPath(e.target.value)}
                  placeholder="/mnt/media or /data/media"
                />
                <span className="field-hint">
                  Main location for movies, TV shows, and other media
                </span>
              </div>
              
              <div className="path-group">
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={useScratch}
                    onChange={(e) => {
                      setUseScratch(e.target.checked);
                      if (e.target.checked && selectedVolume) {
                        setScratchPath(selectedVolume.suggested_paths.downloads);
                      } else {
                        setScratchPath('');
                      }
                    }}
                  />
                  Use separate scratch/download location
                </label>
              </div>
              
              {useScratch && (
                <div className="path-group">
                  <label>Downloads/Scratch Path</label>
                  <input
                    type="text"
                    value={scratchPath}
                    onChange={(e) => setScratchPath(e.target.value)}
                    placeholder="/mnt/downloads or /data/downloads"
                  />
                  <span className="field-hint">
                    Location for incomplete downloads and temporary processing
                  </span>
                </div>
              )}
              
              <div className="path-group">
                <label>Application Data Path</label>
                <input
                  type="text"
                  value={appdataPath}
                  onChange={(e) => setAppdataPath(e.target.value)}
                  placeholder="/mnt/appdata or /data/appdata"
                />
                <span className="field-hint">
                  Location for service configurations (Radarr, Sonarr, etc.)
                </span>
              </div>
            </div>
          </div>
        )}

        {currentStep === 4 && (
          <div className="step-review">
            <h2>Review Configuration</h2>
            <p>Please review your settings before applying.</p>
            
            <div className="review-section">
              <h3>Admin Account</h3>
              <div className="review-item">
                <span className="review-label">Username:</span>
                <span className="review-value">{adminUsername}</span>
              </div>
            </div>
            
            <div className="review-section">
              <h3>Storage Configuration</h3>
              <div className="review-item">
                <span className="review-label">Media Library:</span>
                <span className="review-value">{poolPath}</span>
              </div>
              {useScratch && (
                <div className="review-item">
                  <span className="review-label">Downloads:</span>
                  <span className="review-value">{scratchPath}</span>
                </div>
              )}
              <div className="review-item">
                <span className="review-label">Application Data:</span>
                <span className="review-value">{appdataPath}</span>
              </div>
            </div>
            
            <div className="warning-box">
              <strong>Note:</strong> The orchestrator will create these directories if they don't exist.
              Make sure you have write permissions to the parent directories.
            </div>
          </div>
        )}

        {currentStep === 5 && (
          <div className="step-complete">
            <div className="success-icon">✓</div>
            <h2>Setup Complete!</h2>
            <p>
              Your NAS Orchestrator has been initialized successfully.
              You can now log in with your admin account.
            </p>
            <div className="next-steps">
              <h4>What's next?</h4>
              <ul>
                <li>Log in with your admin credentials</li>
                <li>Configure your services (qBittorrent, Radarr, Sonarr, etc.)</li>
                <li>Apply the configuration to deploy your media stack</li>
                <li>Start managing your media!</li>
              </ul>
            </div>
            <button
              onClick={onComplete}
              className="primary-button"
            >
              Go to Login
            </button>
          </div>
        )}
      </div>

      {/* Navigation Buttons */}
      {currentStep < 5 && (
        <div className="wizard-navigation">
          {currentStep > 0 && (
            <button
              onClick={handleBack}
              className="secondary-button"
              disabled={isLoading}
            >
              Back
            </button>
          )}
          
          {currentStep < 4 ? (
            <button
              onClick={handleNext}
              className="primary-button"
              disabled={isLoading}
            >
              Next
            </button>
          ) : (
            <button
              onClick={handleInitialize}
              className="primary-button"
              disabled={isLoading}
            >
              {isLoading ? 'Initializing...' : 'Initialize System'}
            </button>
          )}
        </div>
      )}

      <style>{`
        .setup-wizard {
          max-width: 800px;
          margin: 0 auto;
          padding: 2rem;
          background: #1e1e2e;
          border-radius: 12px;
          box-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
        }

        .wizard-progress {
          display: flex;
          justify-content: space-between;
          margin-bottom: 2rem;
          padding-bottom: 1rem;
          border-bottom: 1px solid #333;
        }

        .progress-step {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 0.5rem;
          flex: 1;
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
        }

        .progress-step.active .step-number {
          background: #5c9ceb;
          color: white;
        }

        .progress-step.completed .step-number {
          background: #4caf50;
          color: white;
        }

        .step-label {
          font-size: 0.75rem;
          color: #666;
          text-transform: uppercase;
          letter-spacing: 0.05em;
        }

        .progress-step.active .step-label {
          color: #5c9ceb;
        }

        .wizard-error {
          background: rgba(244, 67, 54, 0.1);
          border: 1px solid rgba(244, 67, 54, 0.3);
          color: #f44336;
          padding: 1rem;
          border-radius: 8px;
          margin-bottom: 1.5rem;
        }

        .wizard-content {
          margin-bottom: 2rem;
        }

        .step-welcome {
          text-align: center;
        }

        .welcome-icon {
          font-size: 4rem;
          margin-bottom: 1rem;
        }

        .step-welcome h2 {
          color: #fff;
          margin-bottom: 1rem;
        }

        .step-welcome p {
          color: #aaa;
          margin-bottom: 2rem;
          line-height: 1.6;
        }

        .info-box {
          background: rgba(92, 156, 235, 0.1);
          border: 1px solid rgba(92, 156, 235, 0.3);
          border-radius: 8px;
          padding: 1.5rem;
          text-align: left;
        }

        .info-box h4 {
          color: #5c9ceb;
          margin: 0 0 1rem;
        }

        .info-box ul {
          margin: 0;
          padding-left: 1.5rem;
          color: #ccc;
        }

        .info-box li {
          margin-bottom: 0.5rem;
        }

        .form-group {
          margin-bottom: 1.5rem;
        }

        .form-group label {
          display: block;
          color: #aaa;
          margin-bottom: 0.5rem;
          font-size: 0.9rem;
        }

        .form-group input {
          width: 100%;
          padding: 0.75rem;
          background: #2a2a3e;
          border: 1px solid #444;
          border-radius: 6px;
          color: #fff;
          font-size: 1rem;
        }

        .form-group input:focus {
          outline: none;
          border-color: #5c9ceb;
        }

        .field-hint {
          display: block;
          color: #666;
          font-size: 0.8rem;
          margin-top: 0.25rem;
        }

        .volume-list {
          display: flex;
          flex-direction: column;
          gap: 1rem;
        }

        .volume-card {
          background: #2a2a3e;
          border: 2px solid #444;
          border-radius: 8px;
          padding: 1.5rem;
          cursor: pointer;
          transition: all 0.2s;
        }

        .volume-card:hover {
          border-color: #666;
        }

        .volume-card.selected {
          border-color: #5c9ceb;
          background: rgba(92, 156, 235, 0.1);
        }

        .volume-header {
          display: flex;
          justify-content: space-between;
          margin-bottom: 0.5rem;
        }

        .volume-device {
          font-weight: bold;
          color: #fff;
        }

        .volume-filesystem {
          background: #333;
          padding: 0.25rem 0.5rem;
          border-radius: 4px;
          font-size: 0.75rem;
          color: #888;
        }

        .volume-mountpoint {
          color: #5c9ceb;
          font-size: 0.9rem;
          margin-bottom: 0.5rem;
        }

        .volume-stats {
          display: flex;
          gap: 1rem;
          color: #888;
          font-size: 0.85rem;
          margin-bottom: 1rem;
        }

        .volume-suggested {
          font-size: 0.8rem;
          color: #666;
        }

        .volume-suggested ul {
          margin: 0.5rem 0 0;
          padding-left: 1.5rem;
        }

        .path-config {
          display: flex;
          flex-direction: column;
          gap: 1.5rem;
        }

        .path-group {
          display: flex;
          flex-direction: column;
          gap: 0.5rem;
        }

        .path-group label {
          color: #aaa;
          font-size: 0.9rem;
        }

        .path-group input {
          padding: 0.75rem;
          background: #2a2a3e;
          border: 1px solid #444;
          border-radius: 6px;
          color: #fff;
          font-size: 1rem;
        }

        .checkbox-label {
          display: flex;
          align-items: center;
          gap: 0.5rem;
          cursor: pointer;
        }

        .checkbox-label input[type="checkbox"] {
          width: auto;
          accent-color: #5c9ceb;
        }

        .review-section {
          background: #2a2a3e;
          border-radius: 8px;
          padding: 1.5rem;
          margin-bottom: 1.5rem;
        }

        .review-section h3 {
          color: #5c9ceb;
          margin: 0 0 1rem;
          font-size: 1rem;
        }

        .review-item {
          display: flex;
          justify-content: space-between;
          padding: 0.5rem 0;
          border-bottom: 1px solid #444;
        }

        .review-item:last-child {
          border-bottom: none;
        }

        .review-label {
          color: #888;
        }

        .review-value {
          color: #fff;
          font-family: monospace;
        }

        .warning-box {
          background: rgba(255, 193, 7, 0.1);
          border: 1px solid rgba(255, 193, 7, 0.3);
          color: #ffc107;
          padding: 1rem;
          border-radius: 8px;
          margin-top: 1rem;
        }

        .step-complete {
          text-align: center;
        }

        .success-icon {
          width: 80px;
          height: 80px;
          background: #4caf50;
          border-radius: 50%;
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 2.5rem;
          color: white;
          margin: 0 auto 1.5rem;
        }

        .next-steps {
          background: #2a2a3e;
          border-radius: 8px;
          padding: 1.5rem;
          margin: 1.5rem 0;
          text-align: left;
        }

        .next-steps h4 {
          color: #5c9ceb;
          margin: 0 0 1rem;
        }

        .next-steps ul {
          margin: 0;
          padding-left: 1.5rem;
          color: #ccc;
        }

        .next-steps li {
          margin-bottom: 0.5rem;
        }

        .wizard-navigation {
          display: flex;
          justify-content: space-between;
          padding-top: 1.5rem;
          border-top: 1px solid #333;
        }

        .primary-button {
          background: #5c9ceb;
          color: white;
          border: none;
          padding: 0.75rem 2rem;
          border-radius: 6px;
          font-size: 1rem;
          cursor: pointer;
          transition: background 0.2s;
        }

        .primary-button:hover:not(:disabled) {
          background: #4a8bd9;
        }

        .primary-button:disabled {
          opacity: 0.5;
          cursor: not-allowed;
        }

        .secondary-button {
          background: transparent;
          color: #aaa;
          border: 1px solid #555;
          padding: 0.75rem 2rem;
          border-radius: 6px;
          font-size: 1rem;
          cursor: pointer;
          transition: all 0.2s;
        }

        .secondary-button:hover:not(:disabled) {
          border-color: #888;
          color: #fff;
        }

        h2 {
          color: #fff;
          margin-bottom: 1rem;
        }

        p {
          color: #aaa;
          line-height: 1.6;
        }
      `}</style>
    </div>
  );
}
