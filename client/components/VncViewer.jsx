'use client';

import React, { useEffect, useRef, useState } from 'react';

// Live view of a sandbox desktop through noVNC. The WebSocket goes to the
// API, which checks the session and bridges to the sandbox; nothing here
// knows the sandbox token. `viewOnly` decides whether this browser's mouse
// and keyboard reach the desktop.
export default function VncViewer({ url, viewOnly = true, onStatus }) {
  const containerRef = useRef(null);
  const rfbRef = useRef(null);
  const [status, setStatus] = useState('connecting');
  const [detail, setDetail] = useState('');

  useEffect(() => {
    let disposed = false;
    let rfb = null;
    setStatus('connecting');
    setDetail('');

    (async () => {
      // noVNC touches `window` at import time, so load it only in the browser.
      const { default: RFB } = await import('@novnc/novnc');
      if (disposed || !containerRef.current) return;
      rfb = new RFB(containerRef.current, url, { wsProtocols: [] });
      rfb.viewOnly = viewOnly;
      rfb.scaleViewport = true;
      rfb.resizeSession = false;
      rfb.clipViewport = false;
      rfb.background = '#000';
      rfb.addEventListener('connect', () => {
        if (disposed) return;
        setStatus('connected');
        if (onStatus) onStatus('connected');
      });
      rfb.addEventListener('disconnect', (event) => {
        if (disposed) return;
        setStatus('disconnected');
        setDetail(event?.detail?.clean ? 'Connection closed.' : 'Connection lost.');
        if (onStatus) onStatus('disconnected');
      });
      rfbRef.current = rfb;
    })().catch((err) => {
      if (disposed) return;
      setStatus('error');
      setDetail(err?.message || 'Could not load the viewer.');
    });

    return () => {
      disposed = true;
      rfbRef.current = null;
      try {
        if (rfb) rfb.disconnect();
      } catch {
        // Already closed.
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url]);

  useEffect(() => {
    if (rfbRef.current) rfbRef.current.viewOnly = viewOnly;
  }, [viewOnly]);

  return (
    <div className="relative w-full h-full">
      <div ref={containerRef} className="w-full h-full" style={{ outline: 'none' }} />
      {status !== 'connected' && (
        <div className="absolute inset-0 flex flex-col items-center justify-center bg-black/70 text-xs text-slate-300 gap-1">
          <span>{status === 'connecting' ? 'Connecting to the desktop…' : status === 'error' ? 'Viewer error' : 'Disconnected'}</span>
          {detail && <span className="text-[10px] text-slate-500">{detail}</span>}
        </div>
      )}
    </div>
  );
}
