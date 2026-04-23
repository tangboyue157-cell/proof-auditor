import { useState, useEffect } from 'react';
import { useConfig, useSaveConfig } from '../hooks/useApi';
import { useQueryClient } from '@tanstack/react-query';

export default function Settings() {
  const { data: config, isLoading } = useConfig();
  const saveConfig = useSaveConfig();
  const queryClient = useQueryClient(); // P1 #7

  // Settings State - Initially load from whatever is given by backend
  const [provider, setProvider] = useState('openai');
  const [apiBase, setApiBase] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [modelName, setModelName] = useState('');

  // Sync incoming config when it loads or when the user changes a provider
  useEffect(() => {
    if (config) {
      if (provider === 'openai') {
        setApiKey(config.openai_key || '');
        setApiBase(config.openai_base || '');
      } else if (provider === 'anthropic') {
        setApiKey(config.anthropic_key || '');
        setApiBase(config.anthropic_base || '');
      } else if (provider === 'gemini') {
        setApiKey(config.gemini_key || '');
        setApiBase('');
      } else if (provider === 'openrouter') {
        setApiKey(config.openrouter_key || '');
        setApiBase('');
      }
    }
  }, [config, provider]);

  // Set initial generic choices on first load
  useEffect(() => {
    if (config) {
      setProvider(config.provider || 'openai');
      setModelName(config.model || '');
    }
  }, [config]);

  // P2 #12: Dynamic page title
  useEffect(() => {
    document.title = 'Settings — Proof Auditor';
  }, []);

  const [saved, setSaved] = useState(false);
  const [testStatus, setTestStatus] = useState<'idle' | 'testing' | 'ok' | 'fail'>('idle');
  const [testMessage, setTestMessage] = useState('');

  const handleSave = () => {
    const updates: Record<string, string> = {
      provider,
      model: modelName,
    };

    if (provider === 'openai') {
      updates.openai_key = apiKey;
      updates.openai_base = apiBase;
    } else if (provider === 'anthropic') {
      updates.anthropic_key = apiKey;
      updates.anthropic_base = apiBase;
    } else if (provider === 'gemini') {
      updates.gemini_key = apiKey;
    } else if (provider === 'openrouter') {
      updates.openrouter_key = apiKey;
    }

    saveConfig.mutate(updates, {
      onSuccess: () => {
        setSaved(true);
        setTimeout(() => setSaved(false), 2000);
        // P1 #7: Invalidate status cache so Dashboard immediately reflects changes
        queryClient.invalidateQueries({ queryKey: ['status'] });
        queryClient.invalidateQueries({ queryKey: ['config'] });
      }
    });
  };

  // P1 #8: Test connection
  const handleTestConnection = async () => {
    setTestStatus('testing');
    setTestMessage('');
    try {
      const res = await fetch('/api/config/test');
      const data = await res.json();
      if (data.success) {
        setTestStatus('ok');
        setTestMessage(data.message || 'Connection successful');
      } else {
        setTestStatus('fail');
        setTestMessage(data.error || 'Connection failed');
      }
    } catch (e: any) {
      setTestStatus('fail');
      setTestMessage(e.message || 'Request failed');
    }
    setTimeout(() => setTestStatus('idle'), 5000);
  };

  if (isLoading) return <div style={{ padding: 24, color: 'var(--text-secondary)' }}>Loading configuration...</div>;

  return (
    <div style={{ maxWidth: 960, margin: '0 auto' }}>
      <h1 style={{
        fontSize: 22,
        fontWeight: 700,
        letterSpacing: '-0.03em',
        marginBottom: 24,
      }}>
        Settings
      </h1>

      <div className="card" style={{ marginBottom: 24 }}>
        <div className="card-header">
          <span className="card-title">Backend API Configuration (.env)</span>
          {saved && (
             <span style={{ fontSize: 13, color: 'var(--success)', fontWeight: 600 }}>✓ Saved to .env</span>
          )}
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px', paddingBottom: 16 }}>
          <div>
            <label style={{ display: 'block', fontSize: 13, marginBottom: 8, color: 'var(--text-secondary)' }}>Provider Engine</label>
            <select className="select" style={{ width: '100%' }} value={provider} onChange={e => setProvider(e.target.value)}>
              <option value="openai">OpenAI Compatible (Default)</option>
              <option value="anthropic">Anthropic (Claude)</option>
              <option value="gemini">Google Gemini</option>
              <option value="openrouter">OpenRouter</option>
            </select>
          </div>
          <div>
            <label style={{ display: 'block', fontSize: 13, marginBottom: 8, color: 'var(--text-secondary)' }}>Model Name (Optional)</label>
            <input 
              type="text" className="input" style={{ width: '100%' }} 
              placeholder={
                provider === 'openai' ? 'Leave blank for default (gpt-5.4)' : 
                provider === 'anthropic' ? 'Leave blank for default (claude-sonnet-4-20250514)' :
                provider === 'gemini' ? 'Leave blank for default (gemini-2.5-pro-preview-05-06)' :
                'Leave blank to use provider default'
              } 
              value={modelName} onChange={e => setModelName(e.target.value)}
            />
          </div>
          <div>
            <label style={{ display: 'block', fontSize: 13, marginBottom: 8, color: 'var(--text-secondary)' }}>Base URL (Optional Custom Proxy)</label>
            <input 
              type="text" className="input" style={{ width: '100%' }} 
              placeholder="e.g. https://api.deepseek.com/v1" 
              value={apiBase} onChange={e => setApiBase(e.target.value)}
              disabled={provider === 'gemini' || provider === 'openrouter'}
            />
          </div>
          <div>
            <label style={{ display: 'block', fontSize: 13, marginBottom: 8, color: 'var(--text-secondary)' }}>
              API Key (Syncs dynamically with .env)
            </label>
            <input 
              type="password" className="input" style={{ width: '100%' }} 
              placeholder="sk-..." 
              value={apiKey} onChange={e => setApiKey(e.target.value)}
            />
          </div>
        </div>
        <div style={{ borderTop: '1px solid var(--border)', paddingTop: 16, display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
             <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
               <button 
                 className="btn btn-secondary"
                 onClick={handleTestConnection}
                 disabled={testStatus === 'testing' || !apiKey.trim()}
                 style={{ fontSize: 12 }}
               >
                 {testStatus === 'testing' ? '🔄 Testing…' : '🔍 Test Connection'}
               </button>
               {testStatus === 'ok' && (
                 <span style={{ fontSize: 12, color: 'var(--success)', fontWeight: 600 }}>✓ {testMessage}</span>
               )}
               {testStatus === 'fail' && (
                 <span style={{ fontSize: 12, color: 'var(--danger)', fontWeight: 600 }}>✗ {testMessage}</span>
               )}
             </div>
             <button 
                className="btn btn-primary" 
                onClick={handleSave} 
                disabled={saveConfig.isPending}
             >
                {saveConfig.isPending ? 'Saving...' : 'Save Configuration'}
             </button>
        </div>
        {/* P1 #8: Show .env file path */}
        <div style={{ marginTop: 12, fontSize: 11, color: 'var(--text-muted)', fontFamily: 'var(--font-mono)' }}>
          Config file: {config?.env_path || '.env'}
        </div>
      </div>
    </div>
  );
}
