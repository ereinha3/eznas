import type { ChangeEvent } from 'react'
import type { StackConfig } from '../components/types'
import { ActionBar } from '../components/ActionBar'

const LANGUAGE_OPTIONS = [
  { code: 'eng', label: 'English' },
  { code: 'und', label: 'Undetermined' },
  { code: 'spa', label: 'Spanish' },
  { code: 'fra', label: 'French' },
  { code: 'deu', label: 'German' },
  { code: 'ita', label: 'Italian' },
  { code: 'jpn', label: 'Japanese' },
  { code: 'kor', label: 'Korean' },
  { code: 'chi', label: 'Chinese' },
  { code: 'por', label: 'Portuguese' },
  { code: 'rus', label: 'Russian' },
] as const

interface MediaPolicyPageProps {
  config: StackConfig
  onChange: (config: StackConfig) => void
  onSave: (config: StackConfig) => void
  onValidate: (config: StackConfig) => void
  onApply: (config: StackConfig) => void
  onBuild?: () => Promise<void>
  isApplying: boolean
}

export function MediaPolicyPage({
  config,
  onChange,
  onSave,
  onValidate,
  onApply,
  onBuild,
  isApplying,
}: MediaPolicyPageProps) {
  const handleLanguageSelect = (
    field: 'keep_audio' | 'keep_subs',
    event: ChangeEvent<HTMLSelectElement>,
  ) => {
    const values = Array.from(event.target.selectedOptions).map((option) => option.value)
    onChange({
      ...config,
      media_policy: {
        ...config.media_policy,
        movies: {
          ...config.media_policy.movies,
          [field]: values,
        },
      },
    })
  }

  return (
    <div className="media-policy-page">
      <h1>Media Policy</h1>

      <div className="card">
        <h2>Media Language Policy</h2>
        <p className="field-hint">
          Original language is automatically preserved for foreign films and anime.
          Select additional languages to keep below.
        </p>
        <div className="grid two">
          <label htmlFor="movies-audio">
            Audio languages
            <select
              id="movies-audio"
              multiple
              size={6}
              value={config.media_policy.movies.keep_audio}
              onChange={(e) => handleLanguageSelect('keep_audio', e)}
            >
              {LANGUAGE_OPTIONS.map((opt) => (
                <option key={opt.code} value={opt.code}>
                  {opt.label} ({opt.code})
                </option>
              ))}
            </select>
          </label>
          <label htmlFor="movies-subs">
            Subtitle languages
            <select
              id="movies-subs"
              multiple
              size={6}
              value={config.media_policy.movies.keep_subs}
              onChange={(e) => handleLanguageSelect('keep_subs', e)}
            >
              {LANGUAGE_OPTIONS.map((opt) => (
                <option key={opt.code} value={opt.code}>
                  {opt.label} ({opt.code})
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>

      <div className="card">
        <h2>Quality &amp; Format Preferences</h2>
        <div className="grid three">
          <label htmlFor="quality-preset">
            Quality preset
            <select
              id="quality-preset"
              value={config.quality.preset}
              onChange={(e) =>
                onChange({
                  ...config,
                  quality: {
                    ...config.quality,
                    preset: e.target.value,
                  },
                })
              }
            >
              <option value="balanced">Balanced</option>
              <option value="1080p">1080p</option>
              <option value="4k">4K</option>
            </select>
          </label>
          <label htmlFor="target-resolution">
            Target resolution
            <select
              id="target-resolution"
              value={config.quality.target_resolution ?? ''}
              onChange={(e) =>
                onChange({
                  ...config,
                  quality: {
                    ...config.quality,
                    target_resolution: e.target.value === '' ? null : e.target.value,
                  },
                })
              }
            >
              <option value="">No preference</option>
              <option value="720p">720p</option>
              <option value="1080p">1080p</option>
              <option value="1440p">1440p</option>
              <option value="2160p">2160p (4K)</option>
            </select>
          </label>
          <label htmlFor="max-bitrate">
            Max bitrate (Mbps)
            <input
              id="max-bitrate"
              type="number"
              min={1}
              value={config.quality.max_bitrate_mbps ?? ''}
              placeholder="Optional"
              onChange={(e) =>
                onChange({
                  ...config,
                  quality: {
                    ...config.quality,
                    max_bitrate_mbps: e.target.value === '' ? null : Number(e.target.value),
                  },
                })
              }
            />
          </label>
          <label htmlFor="preferred-container">
            Preferred container
            <select
              id="preferred-container"
              value={config.quality.preferred_container}
              onChange={(e) =>
                onChange({
                  ...config,
                  quality: {
                    ...config.quality,
                    preferred_container: e.target.value,
                  },
                })
              }
            >
              <option value="mkv">MKV</option>
              <option value="mp4">MP4</option>
            </select>
          </label>
        </div>
      </div>

      <ActionBar
        config={config}
        onSave={onSave}
        onValidate={onValidate}
        onApply={onApply}
        onBuild={onBuild}
        isApplying={isApplying}
      />
    </div>
  )
}
