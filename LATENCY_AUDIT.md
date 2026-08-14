# Alyssa voice latency audit

## Bottlenecks found

1. Turn endpointing waited 0.8 seconds initially and could adapt as high as
   1.6 seconds. Speech onset also required 300 ms of uninterrupted VAD-positive
   audio.
2. Microphone audio was always accumulated completely before local Faster
   Whisper inference; no partial transcript path existed.
3. Every supported LLM request used a non-streaming response. The system prompt
   repeated many examples, and history was limited only by turn count, so model
   prefill and time to first token grew unnecessarily.
4. LLM completion and TTS were serial. TTS wrote complete MP3 files before
   playback; ElevenLabs downloaded chunks but still waited for the whole file.
5. Barge-in began only while final TTS was playing. It could not cancel an LLM
   request or synthesis occurring during the silent thinking period.
6. Timing metrics printed synchronously on the latency-critical threads.

## Implemented pipeline

```text
16 kHz mic -> 30 ms WebRTC VAD frames -> 120 ms WebSocket STT packets
           -> 300 ms endpoint -> streaming LLM SSE/NDJSON
           -> punctuation-aware clause queue -> streaming TTS
           -> 100 ms PCM prebuffer -> direct sounddevice playback
```

- ElevenLabs Scribe realtime uses one reconnecting WebSocket, emits partials,
  and manually commits as soon as local endpointing fires. `auto` falls back to
  local Faster Whisper when no ElevenLabs key is configured.
- Gemini, Ollama, OpenAI-compatible, and Anthropic calls now stream text while
  retaining complete tool-call reconstruction. A shared HTTP session and
  startup connection warm-up retain transport connections between turns.
- The default prompt is a compact rules-equivalent voice prompt that explicitly
  requests an early useful punctuation boundary. History keeps complete
  user/assistant pairs and slides by both turn count and character budget.
- LLM deltas are released to TTS at safe sentence/clause boundaries. ElevenLabs
  TTS uses a persistent text-input WebSocket and plays raw 24 kHz PCM chunks;
  Edge TTS keeps the compatible clause-at-a-time encoded-file fallback.
- Interruption listening starts before the LLM request. Sustained new speech or
  typed input sets the shared cancellation event, closes generation, closes the
  active TTS WebSocket, and aborts/clears PCM playback.
- Latency metrics now go through one daemon queue worker.

## Recommended values now configured

| Parameter | Value | Rationale |
| --- | ---: | --- |
| VAD frame | 30 ms | WebRTC-supported frame size with low callback overhead |
| VAD aggressiveness | 2 | Balanced speech retention and noise rejection |
| Speech onset | 120 ms | Faster pickup while filtering isolated clicks |
| Base end silence | 300 ms | Fast turn completion without a hair-trigger cutoff |
| Adaptive end silence | 240–360 ms effective | Faster for quick speakers, modest pause allowance for slow speech |
| Barge-in onset | 150 ms | Near-immediate cancellation after sustained speech |
| STT packet | 120 ms | Within realtime STT guidance while keeping packet overhead low |
| STT final wait | 2.0 s maximum | Bounded cloud finalization before local fallback |
| Context history | 4 turns / 4,000 chars | Bounded prefill while preserving follow-up context |
| LLM output cap | 256 tokens | More than enough for short spoken replies and tool turns |
| TTS clause minimum | 28 characters | Early synthesis without tiny unnatural fragments |
| PCM playback prebuffer | 100 ms | Low TTFA with enough jitter tolerance for desktop playback |

## Compatibility note

True WebSocket STT and direct PCM TTS require an ElevenLabs key and ElevenLabs
TTS selection, respectively. Existing keyless/local and Edge configurations
continue to work through Faster Whisper and clause-pipelined Edge TTS, but those
fallback providers cannot offer the same network partials/raw PCM behavior.
