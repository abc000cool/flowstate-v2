import { useState } from 'react';
import { DEFAULT_API_KEY, DEFAULT_BASE_URL, getSettings, saveSettings } from '../api/client';
import { toast } from './toast';

export function SettingsDrawer({ onClose }: { onClose: () => void }): JSX.Element {
  const current = getSettings();
  const [baseUrl, setBaseUrl] = useState(current.baseUrl);
  const [apiKey, setApiKey] = useState(current.apiKey);

  const save = (): void => {
    saveSettings({ baseUrl, apiKey });
    toast('ok', 'API settings saved');
    onClose();
  };

  return (
    <>
      <div className="drawer-scrim" onClick={onClose} />
      <div className="drawer" role="dialog" aria-label="Settings">
        <h2>Connection settings</h2>
        <div className="field">
          <label htmlFor="set-base">API base URL</label>
          <input
            id="set-base"
            className="input"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
            placeholder={DEFAULT_BASE_URL}
          />
        </div>
        <div className="field">
          <label htmlFor="set-key">API key (X-API-Key)</label>
          <input
            id="set-key"
            className="input"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={DEFAULT_API_KEY}
          />
        </div>
        <p className="small muted">
          Stored locally in this browser. The dev proxy forwards <span className="mono">/api</span>{' '}
          to <span className="mono">localhost:8000</span>; set an absolute base URL to reach a
          remote API. Set <span className="mono">VITE_MOCK=1</span> at build time to force demo
          data.
        </p>
        <div className="row">
          <button className="btn primary" onClick={save}>
            Save
          </button>
          <button className="btn" onClick={onClose}>
            Cancel
          </button>
        </div>
      </div>
    </>
  );
}
