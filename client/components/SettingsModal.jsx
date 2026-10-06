'use client';

import React, { useState, useEffect } from 'react';
import {
  FiX,
  FiKey,
  FiGlobe,
  FiCpu,
  FiCheck,
  FiSave,
  FiLock,
  FiEye,
  FiEyeOff,
  FiChevronDown
} from 'react-icons/fi';
import { fetchSettings, saveSettings } from '../lib/api';

// Compact credentials dialog. The sidebar uses AppSettingsDrawer; this stays
// as a lightweight alternative that writes the same provider-neutral settings.
export default function SettingsModal({ isOpen, onClose }) {
  const [provider, setProvider] = useState('openai_compatible');
  const [apiKey, setApiKey] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [defaultModel, setDefaultModel] = useState('');
  const [apiKeyConfigured, setApiKeyConfigured] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [savedSuccess, setSavedSuccess] = useState(false);

  useEffect(() => {
    if (isOpen) {
      fetchSettings()
        .then((data) => {
          if (data) {
            setProvider(data.llm_provider || 'openai_compatible');
            setApiKey('');
            setApiKeyConfigured(Boolean(data.llm_api_key_configured));
            setBaseUrl(data.llm_base_url || '');
            setDefaultModel(data.default_model || '');
          }
        })
        .catch(console.error);
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const handleSave = async (e) => {
    e.preventDefault();
    try {
      const saved = await saveSettings({
        llm_provider: provider,
        llm_api_key: apiKey,
        llm_base_url: baseUrl.trim(),
        default_model: defaultModel.trim(),
        theme: 'dark'
      });
      setApiKey('');
      setApiKeyConfigured(Boolean(saved?.llm_api_key_configured));
      setSavedSuccess(true);
      setTimeout(() => {
        setSavedSuccess(false);
        onClose();
      }, 800);
    } catch (err) {
      console.error('Failed to save settings:', err);
    }
  };

  const defaultBaseUrl = provider === 'muapi' ? 'https://api.muapi.ai/api/v1' : 'https://openrouter.ai/api/v1';

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm p-4 select-none font-sans">
      <div className="w-full max-w-lg bg-[#11131b] border border-[#222636] rounded-2xl shadow-2xl p-6 relative text-zinc-100 animate-fade-in space-y-5">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-[#1d2130] pb-4">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-[#1a1d2c] text-blue-400 border border-[#2b3048] flex items-center justify-center text-base shadow-sm">
              <FiKey />
            </div>
            <div>
              <h2 className="text-xs font-bold text-zinc-100 tracking-wide">App Settings & API Credentials</h2>
              <p className="text-[11px] text-zinc-400 mt-0.5">Configure the model server, key & default model</p>
            </div>
          </div>

          <button
            suppressHydrationWarning={true}
            onClick={onClose}
            className="p-1.5 rounded-lg text-zinc-400 hover:text-white hover:bg-[#1a1d2c] transition"
          >
            <FiX className="text-base" />
          </button>
        </div>

        <form onSubmit={handleSave} className="space-y-4">
          {/* Provider */}
          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-zinc-300 flex items-center gap-1.5">
              <FiCpu className="text-emerald-400 text-xs" /> Provider
            </label>
            <div className="relative">
              <select
                suppressHydrationWarning={true}
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
                className="w-full rounded-full bg-[#1c202d] border border-[#2a3045] px-4 py-2 pr-10 text-xs text-zinc-100 font-sans focus:outline-none focus:border-blue-500 appearance-none cursor-pointer"
              >
                <option value="openai_compatible">OpenAI-compatible (OpenRouter, Ollama, LM Studio, llama.cpp…)</option>
                <option value="muapi">MUAPI (legacy)</option>
              </select>
              <FiChevronDown className="absolute right-4 top-3 text-zinc-400 text-xs pointer-events-none" />
            </div>
          </div>

          {/* API Key Field */}
          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-zinc-300 flex items-center gap-1.5">
              <FiLock className="text-amber-400 text-xs" /> API Key
            </label>
            <div className="relative">
              <input
                suppressHydrationWarning={true}
                type={showKey ? 'text' : 'password'}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
                placeholder={apiKeyConfigured ? 'Stored securely — enter to replace' : 'Paste API key (optional for local servers)...'}
                className="w-full rounded-full bg-[#1c202d] border border-[#2a3045] px-4 py-2 pr-10 text-xs text-zinc-100 placeholder-zinc-500 font-mono focus:outline-none focus:border-blue-500 transition"
              />
              <button
                suppressHydrationWarning={true}
                type="button"
                onClick={() => setShowKey(!showKey)}
                className="absolute right-3.5 top-2.5 text-zinc-400 hover:text-zinc-200 text-sm transition"
              >
                {showKey ? <FiEyeOff /> : <FiEye />}
              </button>
            </div>
            <p className="text-[10px] text-zinc-500 font-mono">
              Keys are never returned. Leave blank to keep the stored key.
            </p>
          </div>

          {/* Base URL Field */}
          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-zinc-300 flex items-center gap-1.5">
              <FiGlobe className="text-blue-400 text-xs" /> Base URL
            </label>
            <input
              suppressHydrationWarning={true}
              type="text"
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder={defaultBaseUrl}
              className="w-full rounded-full bg-[#1c202d] border border-[#2a3045] px-4 py-2 text-xs text-cyan-300 font-mono focus:outline-none focus:border-blue-500 transition"
            />
          </div>

          {/* Default Model Field */}
          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-zinc-300 flex items-center gap-1.5">
              <FiCpu className="text-purple-400 text-xs" /> Default model ID
            </label>
            <input
              suppressHydrationWarning={true}
              type="text"
              value={defaultModel}
              onChange={(e) => setDefaultModel(e.target.value)}
              placeholder={provider === 'muapi' ? 'grok-4-5' : 'x-ai/grok-4.5'}
              className="w-full rounded-full bg-[#1c202d] border border-[#2a3045] px-4 py-2 text-xs text-zinc-100 font-mono focus:outline-none focus:border-blue-500 transition"
            />
          </div>

          {/* Footer Save Action Buttons */}
          <div className="pt-4 border-t border-[#1d2130] flex items-center justify-end gap-3">
            <button
              suppressHydrationWarning={true}
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-full text-xs font-medium text-zinc-400 hover:text-white transition"
            >
              Cancel
            </button>

            <button
              suppressHydrationWarning={true}
              type="submit"
              className="flex items-center gap-1.5 px-5 py-2 rounded-full bg-gradient-to-r from-blue-600 to-blue-500 hover:from-blue-500 hover:to-blue-400 text-white text-xs font-semibold shadow-lg shadow-blue-600/30 transition"
            >
              {savedSuccess ? <FiCheck className="text-sm" /> : <FiSave className="text-sm" />}
              <span>{savedSuccess ? 'Saved!' : 'Save Credentials'}</span>
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
