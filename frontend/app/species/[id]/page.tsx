'use client';

import { useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import {
  ChevronLeft,
  Leaf,
  Flower2,
  Bug,
  Sprout,
  TreePine,
  Wheat,
  Utensils,
  MessageCircle,
  Send,
  Loader2,
  AlertCircle,
} from 'lucide-react';
import { fetchSpecies, postChat } from '@/lib/api';
import type { ChatMessage, SpeciesOut } from '@/lib/types';
import { ScoreRow } from '@/components/ScoreBar';

// ─── Species header ───────────────────────────────────────────────────────────

function SpeciesHeader({ species }: { species: SpeciesOut }) {
  const primaryName =
    species.common_names[0] ?? species.scientific_name;
  const isFoodWorthy = (species.food_utility_score ?? 0) > 0.6;

  return (
    <section className="space-y-6">
      {/* Name block */}
      <div>
        <div className="flex items-start gap-3">
          <h1 className="text-3xl font-bold text-green-950 leading-tight flex-1">
            {primaryName}
          </h1>
          {isFoodWorthy && (
            <span className="flex-shrink-0 flex items-center gap-1 text-xs font-semibold text-amber-700 bg-amber-50 border border-amber-100 rounded-full px-3 py-1 mt-1">
              <Utensils className="w-3 h-3" />
              Edible
            </span>
          )}
        </div>
        <p className="mt-1 text-lg italic text-stone-400">
          {species.scientific_name}
        </p>
        {species.family && (
          <p className="mt-0.5 text-sm text-stone-500">
            Family: <span className="font-medium">{species.family}</span>
          </p>
        )}
        {species.common_names.length > 1 && (
          <p className="mt-1 text-sm text-stone-500">
            Also known as:{' '}
            {species.common_names.slice(1, 6).join(', ')}
          </p>
        )}
      </div>

      {/* Description */}
      {species.description && (
        <div>
          <h2 className="text-base font-semibold text-stone-800 mb-2">
            About
          </h2>
          <p className="text-sm text-stone-700 leading-relaxed">
            {species.description}
          </p>
        </div>
      )}

      {/* Ecological scores */}
      <div>
        <h2 className="text-base font-semibold text-stone-800 mb-3">
          Ecological profile
        </h2>
        <div className="space-y-3">
          <ScoreRow
            icon={<Flower2 className="w-4 h-4" />}
            label="Pollinators"
            score={species.pollinator_score}
          />
          <ScoreRow
            icon={<Bug className="w-4 h-4" />}
            label="Insect host"
            score={species.insect_host_score}
          />
          <ScoreRow
            icon={<Sprout className="w-4 h-4" />}
            label="Soil health"
            score={species.soil_benefit_score}
          />
          <ScoreRow
            icon={<TreePine className="w-4 h-4" />}
            label="Environment"
            score={species.environmental_score}
          />
          <ScoreRow
            icon={<Wheat className="w-4 h-4" />}
            label="Food & utility"
            score={species.food_utility_score}
          />
        </div>
      </div>

      {/* Food notes */}
      {species.food_utility_notes && (
        <div className="bg-amber-50 border border-amber-100 rounded-xl px-5 py-4">
          <h2 className="text-sm font-semibold text-amber-800 flex items-center gap-1.5 mb-2">
            <Utensils className="w-3.5 h-3.5" />
            Food &amp; medicinal uses
          </h2>
          <p className="text-sm text-amber-900 leading-relaxed">
            {species.food_utility_notes}
          </p>
        </div>
      )}
    </section>
  );
}

// ─── Chat ─────────────────────────────────────────────────────────────────────

function ChatBubble({ msg }: { msg: ChatMessage }) {
  const isUser = msg.role === 'user';
  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}>
      <div
        className={`max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-relaxed whitespace-pre-wrap ${
          isUser
            ? 'bg-green-800 text-white rounded-tr-none'
            : 'bg-white border border-stone-200 text-stone-800 rounded-tl-none'
        }`}
      >
        {msg.content}
      </div>
    </div>
  );
}

function ChatSection({ species }: { species: SpeciesOut }) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [streamBuffer, setStreamBuffer] = useState('');
  const [chatError, setChatError] = useState('');

  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // Scroll to bottom whenever messages or stream buffer change
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamBuffer]);

  const sendMessage = async () => {
    const text = input.trim();
    if (!text || streaming) return;

    const userMsg: ChatMessage = { role: 'user', content: text };
    const historyForRequest = [...messages];
    setMessages((prev) => [...prev, userMsg]);
    setInput('');
    setStreaming(true);
    setStreamBuffer('');
    setChatError('');

    try {
      const response = await postChat({
        species_id: species.id,
        message: text,
        history: historyForRequest,
      });

      const reader = response.body!.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      let fullResponse = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buf += decoder.decode(value, { stream: true });
        // Split on SSE line boundaries
        const lines = buf.split('\n');
        buf = lines.pop() ?? '';  // keep incomplete line

        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const raw = line.slice(6).trim();
          if (!raw) continue;
          try {
            const payload = JSON.parse(raw) as { text?: string; event?: string; message?: string };
            if (payload.text) {
              fullResponse += payload.text;
              setStreamBuffer(fullResponse);
            }
            if (payload.event === 'done') {
              break;
            }
            if (payload.event === 'error') {
              setChatError(payload.message ?? 'An error occurred.');
            }
          } catch {
            /* ignore parse errors on individual chunks */
          }
        }
      }

      // Commit streamed response as a permanent message
      if (fullResponse) {
        setMessages((prev) => [
          ...prev,
          { role: 'assistant', content: fullResponse },
        ]);
      }
    } catch (err) {
      setChatError(
        err instanceof Error ? err.message : 'Failed to reach the server.',
      );
    } finally {
      setStreaming(false);
      setStreamBuffer('');
      inputRef.current?.focus();
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  };

  const isEmpty = messages.length === 0 && !streaming;

  return (
    <section id="chat" className="mt-10">
      <h2 className="text-xl font-bold text-green-950 mb-1 flex items-center gap-2">
        <MessageCircle className="w-5 h-5 text-green-700" />
        Ask about this plant
      </h2>
      <p className="text-sm text-stone-500 mb-4">
        Claude has full access to this species' ecological profile. Ask anything
        — growing tips, companion planting, harvest notes, or ecology.
      </p>

      {/* Message list */}
      <div className="bg-stone-100 rounded-2xl p-4 min-h-48 max-h-[480px] overflow-y-auto space-y-3 flex flex-col">
        {isEmpty && (
          <div className="flex-1 flex flex-col items-center justify-center text-center py-8 text-stone-400">
            <MessageCircle className="w-8 h-8 mb-3 opacity-40" />
            <p className="text-sm">
              Ask a question about {species.scientific_name}…
            </p>
          </div>
        )}

        {messages.map((msg, i) => (
          <ChatBubble key={i} msg={msg} />
        ))}

        {/* Streaming response in-flight */}
        {streaming && streamBuffer && (
          <div className="flex justify-start">
            <div className="max-w-[85%] bg-white border border-stone-200 rounded-2xl rounded-tl-none px-4 py-3 text-sm text-stone-800 leading-relaxed whitespace-pre-wrap">
              {streamBuffer}
              <span className="inline-block w-1.5 h-3.5 bg-green-600 rounded-sm ml-1 animate-pulse align-middle" />
            </div>
          </div>
        )}

        {/* Waiting for first token */}
        {streaming && !streamBuffer && (
          <div className="flex justify-start">
            <div className="bg-white border border-stone-200 rounded-2xl rounded-tl-none px-4 py-3">
              <Loader2 className="w-4 h-4 text-green-700 animate-spin" />
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Error */}
      {chatError && (
        <div className="flex items-center gap-2 mt-2 text-sm text-red-600 bg-red-50 border border-red-100 rounded-xl px-4 py-3">
          <AlertCircle className="w-4 h-4 flex-shrink-0" />
          {chatError}
        </div>
      )}

      {/* Input row */}
      <div className="flex gap-2 mt-3">
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          rows={2}
          placeholder="Ask a question… (Enter to send, Shift+Enter for newline)"
          disabled={streaming}
          className="flex-1 px-4 py-3 rounded-xl border border-stone-200 bg-white text-stone-900 placeholder:text-stone-400 focus:outline-none focus:ring-2 focus:ring-green-700 text-sm leading-relaxed resize-none disabled:opacity-50"
        />
        <button
          onClick={sendMessage}
          disabled={!input.trim() || streaming}
          className="flex-shrink-0 self-end p-3 rounded-xl bg-green-800 hover:bg-green-900 disabled:bg-stone-200 disabled:text-stone-400 text-white transition-colors"
          aria-label="Send message"
        >
          {streaming ? (
            <Loader2 className="w-5 h-5 animate-spin" />
          ) : (
            <Send className="w-5 h-5" />
          )}
        </button>
      </div>

      {messages.length > 0 && (
        <button
          onClick={() => {
            setMessages([]);
            setStreamBuffer('');
            setChatError('');
          }}
          className="mt-2 text-xs text-stone-400 hover:text-stone-600 transition-colors"
        >
          Clear conversation
        </button>
      )}
    </section>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function SpeciesDetailPage() {
  const { id } = useParams<{ id: string }>();

  const { data: species, isLoading, error } = useQuery<SpeciesOut, Error>({
    queryKey: ['species', id],
    queryFn: () => fetchSpecies(id),
    enabled: !!id,
  });

  return (
    <div className="min-h-dvh bg-stone-50">
      {/* Header */}
      <header className="sticky top-0 z-10 bg-stone-50/90 backdrop-blur border-b border-stone-200 px-6 py-4 flex items-center gap-4">
        <Link
          href="/"
          className="flex items-center gap-1.5 text-sm text-stone-500 hover:text-stone-800 transition-colors"
        >
          <ChevronLeft className="w-4 h-4" />
          Back
        </Link>
        <div className="flex items-center gap-2 ml-auto">
          <Leaf className="w-4 h-4 text-green-700" />
          <span className="font-serif font-bold text-green-900 text-base">
            jardin
          </span>
        </div>
      </header>

      <main className="max-w-2xl mx-auto px-4 sm:px-6 py-8">
        {isLoading && (
          <div className="flex items-center justify-center py-24 gap-3 text-stone-500">
            <Loader2 className="w-5 h-5 animate-spin" />
            Loading species…
          </div>
        )}

        {error && (
          <div className="flex items-start gap-3 bg-red-50 border border-red-200 rounded-xl px-5 py-4">
            <AlertCircle className="w-5 h-5 text-red-600 mt-0.5 flex-shrink-0" />
            <div>
              <p className="font-semibold text-red-800 text-sm">
                Could not load species
              </p>
              <p className="text-xs text-red-700 mt-1">{error.message}</p>
            </div>
          </div>
        )}

        {species && (
          <>
            <SpeciesHeader species={species} />
            <ChatSection species={species} />
          </>
        )}
      </main>
    </div>
  );
}
