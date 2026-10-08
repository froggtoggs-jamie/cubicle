'use client';

import React, { useEffect, useState } from 'react';
import { FiChevronRight } from 'react-icons/fi';
import { fetchToolCatalog } from '../lib/api';
import { Switch } from './ToolsPopover';
import {
  countEnabled,
  isGroupOn,
  isToolOn,
  isToolkitOn,
  withGroup,
  withTool,
  withToolkit,
} from '../lib/toolSettings';

function ToolRow({ tool, on, parentOn, onChange }) {
  return (
    <label className={`flex items-start gap-2.5 px-3 py-1.5 rounded-lg hover:bg-[#16161a] cursor-pointer ${parentOn ? '' : 'opacity-50'}`}>
      <input
        type="checkbox"
        checked={on}
        disabled={!parentOn}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-0.5 accent-emerald-500"
      />
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-2">
          <span className="text-xs text-zinc-100 font-mono truncate">{tool.name}</span>
          {tool.needs_approval ? (
            <span className="text-[9px] uppercase tracking-wide text-amber-400/90 border border-amber-500/30 rounded px-1">asks first</span>
          ) : (
            <span className="text-[9px] uppercase tracking-wide text-zinc-500 border border-zinc-700 rounded px-1">read</span>
          )}
        </span>
        <span className="block text-[11px] text-zinc-500 leading-snug line-clamp-2">{tool.description}</span>
      </span>
    </label>
  );
}

function Section({ title, subtitle, on, disabled, onToggle, tools, settings, onChange }) {
  const [expanded, setExpanded] = useState(false);
  const enabledCount = tools.filter((tool) => isToolOn(settings, tool.name)).length;
  return (
    <div className="rounded-xl border border-[#26262b] bg-[#141417]">
      <div className="flex items-center gap-2 px-3 py-2">
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="flex items-center gap-2 min-w-0 flex-1 text-left"
          aria-expanded={expanded}
        >
          <FiChevronRight className={`text-zinc-500 flex-shrink-0 transition-transform ${expanded ? 'rotate-90' : ''}`} />
          <span className="min-w-0">
            <span className="block text-xs text-zinc-100 truncate">{title}</span>
            <span className="block text-[10px] text-zinc-500">
              {subtitle || (on ? `${enabledCount} of ${tools.length} tools on` : 'Off for this bot')}
            </span>
          </span>
        </button>
        <Switch on={on} disabled={disabled} onChange={onToggle} label={`Toggle ${title}`} />
      </div>
      {expanded && (
        <div className="border-t border-[#1f1f23] py-1">
          {tools.map((tool) => (
            <ToolRow
              key={tool.name}
              tool={tool}
              on={isToolOn(settings, tool.name)}
              parentOn={on}
              onChange={(checked) => onChange(withTool(settings, tool.name, checked))}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// The "Tools" part of the bot editor: group and app switches with
// per-tool checkboxes underneath. Edits the settings object passed in.
export default function BotToolsSection({ settings, onChange }) {
  const [catalog, setCatalog] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    fetchToolCatalog()
      .then(setCatalog)
      .catch((err) => setError(err?.message || 'Could not load the tool list.'));
  }, []);

  if (error) return <p className="text-xs text-rose-400">{error}</p>;
  if (!catalog) return <p className="text-xs text-zinc-500">Loading tools…</p>;

  const counts = countEnabled(catalog, settings);
  const disabledGlobally = new Set(catalog.disabled_toolkits || []);

  return (
    <div className="space-y-2">
      <p className="text-[11px] text-zinc-500">
        {counts.on} of {counts.total} tools will be offered to the model. Anything new that gets connected is on by default.
      </p>
      {catalog.groups.map((group) => (
        <Section
          key={group.id}
          title={group.label}
          on={isGroupOn(settings, group.id)}
          onToggle={(on) => onChange(withGroup(settings, group.id, on))}
          tools={group.tools}
          settings={settings}
          onChange={onChange}
        />
      ))}
      {catalog.toolkits.map((toolkit) => {
        const offForAll = disabledGlobally.has(toolkit.slug);
        return (
          <Section
            key={toolkit.slug}
            title={toolkit.name}
            subtitle={offForAll ? 'Turned off for all bots in Plugins' : undefined}
            on={!offForAll && isToolkitOn(settings, toolkit.slug)}
            disabled={offForAll}
            onToggle={(on) => onChange(withToolkit(settings, toolkit.slug, on))}
            tools={toolkit.tools}
            settings={settings}
            onChange={onChange}
          />
        );
      })}
      {!catalog.toolkits.length && <p className="text-[11px] text-zinc-500">No connected apps yet. Connect some in Plugins and their tools appear here.</p>}
    </div>
  );
}
