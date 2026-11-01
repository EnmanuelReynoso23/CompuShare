import { useEffect, useRef } from 'react';
import { Send } from 'lucide-react';
import type { MessageRow } from '../red';

interface Props {
  myId: string;
  chat: MessageRow[];
  prompt: string;
  setPrompt: (v: string) => void;
  onAsk: () => void;
  awaitingAnswer: boolean;
}

const ChatView = ({ myId, chat, prompt, setPrompt, onAsk, awaitingAnswer }: Props) => {
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chat]);

  return (
    <div className="w-full h-full flex flex-col min-h-0">
      <div className="flex-1 min-h-0 overflow-y-auto space-y-5 pr-1">
        {chat.length === 0 && (
          <div className="text-paper-faint text-xs text-center mt-10">
            Sala compartida — todos los que abran esta página ven las mismas preguntas y respuestas.
          </div>
        )}

        {chat.map((m) => (
          <div key={m.$id} className="space-y-1.5">
            <div className="flex justify-end">
              <div className="max-w-[88%] sm:max-w-[70%] bg-paper text-ink rounded-sm px-4 py-2">
                <div className="text-[9px] uppercase tracking-widest opacity-60 mb-1">
                  {m.authorId === myId ? 'tú' : m.authorId.substring(0, 6)}
                </div>
                <div className="text-sm">{m.prompt}</div>
              </div>
            </div>

            <div className="flex justify-start">
              <div
                className={`max-w-[88%] sm:max-w-[70%] rounded-sm px-4 py-2 border ${
                  m.status === 'error' ? 'border-line-strong border-dashed' : 'border-line'
                }`}
              >
                <div className="text-[9px] uppercase tracking-widest text-paper-faint mb-1">
                  {m.status === 'pending' && 'buscando nodo'}
                  {m.status === 'answered' && `${m.workerId?.substring(0, 6)} · ${m.timeMs?.toFixed(0)}ms`}
                  {m.status === 'error' && 'error'}
                </div>
                {m.status === 'pending' ? (
                  m.answer ? (
                    <div className="text-sm text-paper whitespace-pre-wrap font-sans">
                      {m.answer}
                      <span className="inline-block w-1.5 h-3.5 bg-paper ml-1 animate-pulse align-middle" />
                    </div>
                  ) : (
                    <span className="inline-flex gap-1">
                      {[0, 1, 2].map((i) => (
                        <span
                          key={i}
                          className="w-1 h-1 rounded-full bg-paper-dim inline-block animate-pulse"
                          style={{ animationDelay: `${i * 150}ms` }}
                        />
                      ))}
                    </span>
                  )
                ) : (
                  <div className="text-sm text-paper whitespace-pre-wrap font-sans">
                    {m.status === 'error' ? `× ${m.answer}` : m.answer}
                  </div>
                )}
                {m.connectionProof && (
                  <div className="text-[9px] text-paper-faint mt-2 pt-1.5 border-t border-line font-mono">
                    ✓ {m.connectionProof}
                  </div>
                )}
              </div>
            </div>
          </div>
        ))}
        <div ref={endRef} />
      </div>

      <div className="flex gap-3 pt-4 mt-4 border-t border-line shrink-0">
        <input
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && onAsk()}
          placeholder="Escribe tu pregunta..."
          // 16px (text-base) stops iOS Safari from auto-zooming the page on
          // focus — anything smaller than 16px triggers that zoom, which is
          // most of why a chat input "feels broken" on iPhone.
          className="flex-1 min-w-0 min-h-[44px] bg-transparent border border-line rounded-sm px-4 py-2 text-base sm:text-sm outline-none focus:border-paper placeholder:text-paper-faint"
        />
        <button
          onClick={onAsk}
          disabled={awaitingAnswer}
          className="min-h-[44px] min-w-[44px] px-4 sm:px-5 py-2 border border-paper rounded-sm hover:bg-paper hover:text-ink transition disabled:opacity-30 disabled:hover:bg-transparent disabled:hover:text-paper flex items-center justify-center gap-2 text-xs uppercase tracking-widest shrink-0"
        >
          <Send className="w-3.5 h-3.5" />
          <span className="hidden sm:inline">Preguntar</span>
        </button>
      </div>
    </div>
  );
};

export default ChatView;
