'use client';

import React, { useCallback, useState, useEffect } from 'react';
import Sidebar from './Sidebar';
import ChatWindow from './ChatWindow';
import ComputerPanel from './ComputerPanel';
import Marketplace from './Marketplace';
import AuditPanel from './AuditPanel';
import AppSettingsDrawer from './AppSettingsDrawer';

import { 
  fetchBots, 
  fetchModels, 
  fetchChatHistory, 
  fetchSettings,
  createBot, 
  updateBot 
} from '../lib/api';
import { establishSession, setAuthFailureHandler, fetchTurnsOverview } from '../lib/api';
import LoginScreen from './LoginScreen';

export default function Dashboard() {
  const [bots, setBots] = useState([]);
  const [models, setModels] = useState([]);
  const [catalogError, setCatalogError] = useState(null);
  // 'checking' -> 'ready' | 'login'. Any later 401 flips back to 'login'.
  const [authState, setAuthState] = useState('checking');
  const [activeBotId, setActiveBotId] = useState('');
  const [activeTab, setActiveTab] = useState('chat'); // 'chat' | 'computer' | 'marketplace' | 'audit'
  // Transcripts are cached per bot so a reply that is still streaming for
  // one bot survives switching to another bot or another tab.
  const [messagesByBot, setMessagesByBot] = useState({});
  const [streamingBots, setStreamingBots] = useState({});
  // The server's view of running turns and pending approvals, by bot.
  const [turnStates, setTurnStates] = useState({});
  // Text to drop into the chat input, e.g. after handing a computer back.
  const [chatPrefill, setChatPrefill] = useState(null);
  const [isSettingsOpen, setIsSettingsOpen] = useState(false);
  const [defaultModel, setDefaultModel] = useState('');
  const [userName, setUserName] = useState(() => {
    if (typeof window !== 'undefined') {
      return localStorage.getItem('open_grok_user_name') || 'You';
    }
    return 'You';
  });

  // Session check. On loopback this succeeds silently; elsewhere the login
  // screen collects the access token.
  useEffect(() => {
    setAuthFailureHandler(() => setAuthState('login'));
    establishSession()
      .then(() => setAuthState('ready'))
      .catch(() => setAuthState('login'));
    return () => setAuthFailureHandler(null);
  }, []);

  // Poll the server for turns that are running or waiting on an approval.
  // This is what lets another machine (or a reloaded page) see and attach
  // to work in progress, and what drives the sidebar indicators.
  useEffect(() => {
    if (authState !== 'ready') return undefined;
    let disposed = false;
    const poll = async () => {
      const overview = await fetchTurnsOverview();
      if (disposed) return;
      const next = {};
      for (const turn of overview.turns || []) next[turn.thread_id] = turn;
      setTurnStates((prev) => (JSON.stringify(prev) === JSON.stringify(next) ? prev : next));
    };
    poll();
    const interval = window.setInterval(poll, 4000);
    return () => {
      disposed = true;
      window.clearInterval(interval);
    };
  }, [authState]);

  // Initial Data Fetch
  useEffect(() => {
    if (authState !== 'ready') return;
    async function initData() {
      try {
        const [botsData, catalog, settingsData] = await Promise.all([fetchBots(), fetchModels(), fetchSettings()]);
        setBots(botsData);
        setModels(catalog.models);
        setCatalogError(catalog.error);
        if (settingsData?.default_model) {
          setDefaultModel(settingsData.default_model);
        }
        if (botsData.length > 0) {
          setActiveBotId(botsData[0].id);
        }
      } catch (err) {
        console.error('Initialization error:', err);
      }
    }
    initData();
  }, [authState]);

  // Reload the model list, bypassing the server cache (used after the
  // connection settings change or from the picker's refresh button).
  const refreshModels = async () => {
    const catalog = await fetchModels(true);
    setModels(catalog.models);
    setCatalogError(catalog.error);
    return catalog;
  };

  const setMessagesFor = useCallback((botId, updater) => {
    setMessagesByBot((prev) => {
      const current = prev[botId] || [];
      const next = typeof updater === 'function' ? updater(current) : updater;
      return { ...prev, [botId]: next };
    });
  }, []);

  const handleStreamingChange = useCallback((botId, value) => {
    setStreamingBots((prev) => (Boolean(prev[botId]) === Boolean(value) ? prev : { ...prev, [botId]: Boolean(value) }));
  }, []);

  const activeBotStreaming = Boolean(activeBotId && streamingBots[activeBotId]);

  // Fetch chat history whenever the active bot changes, and again once a
  // reply finishes so the cache picks up the persisted message ids. A bot
  // that is mid-reply keeps its live transcript.
  useEffect(() => {
    if (!activeBotId || activeBotStreaming) return;
    let cancelled = false;
    fetchChatHistory(activeBotId)
      .then((history) => {
        if (!cancelled) setMessagesFor(activeBotId, history);
      })
      .catch((err) => console.error('Failed to load history:', err));
    return () => {
      cancelled = true;
    };
  }, [activeBotId, activeBotStreaming, setMessagesFor]);

  const activeBot = bots.find((b) => b.id === activeBotId) || bots[0];

  const handleUpdateBotModel = async (botId, newModel) => {
    try {
      const updated = await updateBot(botId, { model: newModel });
      setBots((prev) => prev.map((b) => (b.id === botId ? updated : b)));
      // Keep defaultModel in sync whenever the active bot's model changes
      if (botId === activeBotId) {
        setDefaultModel(newModel);
      }
    } catch (err) {
      console.error('Failed to update bot model:', err);
    }
  };

  const handleCreateNewBot = async () => {
    const name = prompt('Enter Bot Name:', 'New Assistant');
    if (!name) return;
    const role = prompt('Enter Role:', 'General Intelligence');
    const model = prompt('Enter model ID (change it later from the chat header):', defaultModel || '');
    const chosenModel = (model || defaultModel || '').trim();

    try {
      const newBot = await createBot({
        name,
        role: role || 'AI Assistant',
        ...(chosenModel ? { model: chosenModel } : {}),
        description: `Custom agent running ${chosenModel || 'the default model'}.`,
        avatar: '🤖',
        system_prompt: `You are ${name}, a helpful AI assistant.`
      });
      setBots((prev) => [...prev, newBot]);
      setActiveBotId(newBot.id);
      setActiveTab('chat');
    } catch (err) {
      console.error('Failed to create bot:', err);
    }
  };

  if (authState === 'login') {
    return <LoginScreen onAuthenticated={() => setAuthState('ready')} />;
  }

  if (authState === 'checking') {
    return (
      <div className="h-screen w-screen flex items-center justify-center bg-[#09090b] text-zinc-500 text-xs font-sans select-none">
        Connecting to the API…
      </div>
    );
  }

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-[#09090b] text-zinc-100 font-sans">
      {/* Sidebar Navigation & Bot Roster */}
      <Sidebar
        bots={bots}
        activeBotId={activeBotId}
        userName={userName}
        onSelectBot={(id) => {
          setActiveBotId(id);
          setActiveTab('chat');
        }}
        activeTab={activeTab}
        onSelectTab={setActiveTab}
        turnStates={turnStates}
        onOpenSettings={() => setIsSettingsOpen(!isSettingsOpen)}
        onOpenNewBot={handleCreateNewBot}
      />

      {/* Main Workspace Display Area */}
      <main className="flex-1 flex flex-col h-screen overflow-hidden relative">
        {/* The chat stays mounted on every tab so the draft, the open SSE
            stream, approvals, and tool events survive a visit to the
            Computer tab. `contents` keeps it out of the layout when shown. */}
        <div className={activeTab === 'chat' ? 'contents' : 'hidden'}>
          <ChatWindow
            bot={activeBot}
            models={models}
            catalogError={catalogError}
            onRefreshModels={refreshModels}
            messages={activeBotId ? messagesByBot[activeBotId] || [] : []}
            setMessagesFor={setMessagesFor}
            streamingBots={streamingBots}
            onStreamingChange={handleStreamingChange}
            turnStates={turnStates}
            onUpdateBotModel={handleUpdateBotModel}
            onToggleComputer={() => setActiveTab('computer')}
            defaultModel={defaultModel}
            prefill={chatPrefill}
          />
        </div>

        {activeTab === 'computer' && (
          <ComputerPanel
            bot={activeBot}
            onBackToChat={() => setActiveTab('chat')}
            onHandBack={() => {
              setChatPrefill({
                text: "I'm done on the computer and have handed control back to you. Please continue.",
                nonce: Date.now(),
              });
              setActiveTab('chat');
            }}
          />
        )}

        {activeTab === 'marketplace' && (
          <Marketplace onOpenSettings={() => setIsSettingsOpen(true)} />
        )}

        {activeTab === 'audit' && <AuditPanel />}
      </main>

      {/* Right Side App Settings Drawer Panel */}
      <AppSettingsDrawer
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
        currentModel={defaultModel}
        models={models}
        catalogError={catalogError}
        onRefreshModels={refreshModels}
        onConnectionSaved={async (saved) => {
          if (saved?.default_model) setDefaultModel(saved.default_model);
          await refreshModels();
        }}
        onUpdateDefaultModel={(newModel) => {
          setDefaultModel(newModel);
          if (activeBotId) {
            handleUpdateBotModel(activeBotId, newModel);
          }
        }}
        onProfileUpdate={(name) => setUserName(name || 'You')}
        onSignOut={() => {
          setIsSettingsOpen(false);
          setAuthState('login');
        }}
      />
    </div>
  );
}
