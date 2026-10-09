'use client';

import React, { useState } from 'react';
import { FiLock, FiEye, FiEyeOff, FiArrowRight } from 'react-icons/fi';
import { loginWithToken } from '../lib/api';

// Shown when the API requires a token and no session cookie is present. This
// is the case for any deployment reached from another machine; on loopback
// without APP_AUTH_TOKEN the session is established automatically instead.
export default function LoginScreen({ onAuthenticated }) {
  const [token, setToken] = useState('');
  const [showToken, setShowToken] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!token.trim() || busy) return;
    setBusy(true);
    setError('');
    try {
      await loginWithToken(token.trim());
      setToken('');
      if (onAuthenticated) onAuthenticated();
    } catch (err) {
      setError(err?.message || 'Sign in failed.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="h-screen w-screen flex items-center justify-center bg-[#09090b] text-zinc-100 font-sans select-none p-4">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm bg-[#111113] border border-[#1c1c20] rounded-2xl shadow-2xl p-6 space-y-5 animate-fade-in"
      >
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-[#18181b] border border-[#27272a] text-amber-400 flex items-center justify-center text-lg">
            <FiLock />
          </div>
          <div>
            <h1 className="text-sm font-bold tracking-wide">Cubicle</h1>
            <p className="text-xs text-zinc-400 mt-0.5">Enter the access token for this server.</p>
          </div>
        </div>

        <div className="space-y-1.5">
          <label className="text-xs font-semibold text-zinc-300">Access token</label>
          <div className="relative">
            <input
              suppressHydrationWarning={true}
              type={showToken ? 'text' : 'password'}
              value={token}
              onChange={(e) => setToken(e.target.value)}
              autoFocus
              autoComplete="current-password"
              placeholder="Paste the APP_AUTH_TOKEN value"
              className="w-full bg-[#222226] border border-[#2e2e34] rounded-xl pl-3.5 pr-9 py-2.5 text-xs text-zinc-100 font-mono placeholder-zinc-500 focus:outline-none focus:border-zinc-400 transition"
            />
            <button
              suppressHydrationWarning={true}
              type="button"
              onClick={() => setShowToken(!showToken)}
              className="absolute right-2.5 top-1/2 -translate-y-1/2 text-zinc-400 hover:text-white transition text-xs"
              title={showToken ? 'Hide token' : 'Show token'}
            >
              {showToken ? <FiEyeOff /> : <FiEye />}
            </button>
          </div>
          <p className="text-[10px] leading-relaxed text-zinc-500">
            The token is set on the server as <span className="font-mono">APP_AUTH_TOKEN</span>. It is exchanged for an
            HttpOnly session cookie and never stored in the page.
          </p>
        </div>

        {error && (
          <p className="text-[11px] text-red-300 bg-red-500/10 border border-red-500/20 rounded-xl px-3 py-2">{error}</p>
        )}

        <button
          suppressHydrationWarning={true}
          type="submit"
          disabled={busy || !token.trim()}
          className="w-full flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl bg-zinc-100 text-zinc-900 text-xs font-semibold hover:bg-white transition disabled:opacity-50 disabled:cursor-not-allowed"
        >
          <span>{busy ? 'Signing in…' : 'Sign in'}</span>
          <FiArrowRight className="text-sm" />
        </button>
      </form>
    </div>
  );
}
