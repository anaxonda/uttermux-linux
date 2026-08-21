# Cloud provider contracts

Cloud adapters share UtterMux language routing, cancellation, and voice selection. They do not pretend that every service has identical controls: each row below records the request contract actually sent to the provider. Credentials are stored in a mode-0600 JSON file on Linux and with Android Keystore-backed AES-GCM encryption on Android.

| Provider | Credentials and discovery | Synthesis and audio | Native rate |
| --- | --- | --- | --- |
| Edge Read Aloud | None. Voice metadata comes from Microsoft's consumer endpoint. | Unofficial Edge Read Aloud WebSocket; decoded to PCM. | SSML percentage |
| [ElevenLabs](https://elevenlabs.io/docs/api-reference/text-to-speech/convert) | `xi-api-key`; authenticated account voices from `GET /v1/voices`. | `POST /v1/text-to-speech/{voice}/stream?output_format=pcm_24000`; Flash v2.5 receives ISO 639-1 `language_code`. | 0.7–1.2 |
| [xAI](https://docs.x.ai/developers/model-capabilities/audio/text-to-speech) | Bearer API key; voices from `GET /v1/tts/voices`. | `POST /v1/tts`; raw PCM16 mono at 24 kHz; BCP-47 language or `auto`. | 0.7–1.5 |
| [OpenAI](https://platform.openai.com/docs/guides/text-to-speech) | Bearer API key, HTTPS API-compatible endpoint, model ID. | `POST /v1/audio/speech`; raw PCM16 mono at 24 kHz. | 0.25–4.0 |
| [Azure Speech](https://learn.microsoft.com/azure/ai-services/speech-service/rest-text-to-speech) | Speech resource key and either region or HTTPS resource endpoint; live voice list. Resource endpoints use `/tts/cognitiveservices/*`; regional endpoints use `/cognitiveservices/*`. | SSML to the synthesis endpoint; raw PCM16 mono at 24 kHz. | SSML percentage |
| [Qwen / DashScope](https://www.alibabacloud.com/help/en/model-studio/qwen-tts-realtime-api) | Bearer API key; region selects the Beijing or international endpoint; workspace ID is optional. | Android uses Qwen3-TTS Realtime WebSocket with UUID `event_id` values, explicit `language_type`, and PCM at 24 kHz. Linux uses the HTTP `qwen3-tts-flash` response URL and decodes its audio. | Not supported by Qwen3-TTS Realtime; Linux HTTP also leaves rate unset |
| [Google Cloud TTS](https://cloud.google.com/text-to-speech/docs/reference/rest/v1/text/synthesize) | Restricted API key, or an explicit HTTPS UtterMux proxy. Android also sends package and signing-certificate restriction headers. Live voice list. | Direct REST returns base64 LINEAR16/WAV at 24 kHz; proxy returns raw PCM16 mono at 24 kHz. | 0.25–2.0 direct; proxy contract receives the requested multiplier |
| [Amazon Polly](https://docs.aws.amazon.com/polly/latest/APIReference/API_SynthesizeSpeech.html) | Direct SigV4 access key/secret, unauthenticated Cognito identity-pool temporary credentials, or explicit HTTPS proxy. Direct/Cognito discovery is paginated. Required IAM actions are `polly:DescribeVoices` and `polly:SynthesizeSpeech`. | Raw PCM16 mono at 16 kHz—the only higher raw-PCM rate Polly accepts. `LanguageCode` is omitted except for Hindi with bilingual Aditi. | No native field; proxy contract receives the requested multiplier |
| [Deepgram](https://developers.deepgram.com/docs/tts-websocket) | `Authorization: Token …`; current Aura 2 presets are declared by UtterMux. | WebSocket `Speak`, `Flush`, and `Close`; raw linear16 at 24 kHz. | 0.7–1.5 |
| [Cartesia](https://docs.cartesia.ai/api-reference/tts/websocket) | `X-API-Key` and `Cartesia-Version: 2026-03-01`; paginated live voice list. Bearer authorization is reserved for short-lived client access tokens, which UtterMux does not request. | Android uses contextual WebSocket streaming; Linux uses streaming `/tts/bytes`; raw PCM16 mono at 24 kHz with Sonic 3. | 0.6–1.5 through `generation_config.speed` |
| [PlayHT](https://docs.play.ht/reference/api-generate-tts-audio-stream) | `AUTHORIZATION` API key plus `X-USER-ID`; live voice list. | `POST /api/v2/tts/stream`; WAV at 24 kHz; language is the provider's lowercase language name. | 0.1–5.0 |
| [Resemble](https://docs.resemble.ai/api-reference/text-to-speech/stream-synthesize) | Bearer API token and one or more voice UUIDs; project UUID is optional. | `/stream` returns chunked WAV using PCM16 at 24 kHz. | No documented native rate field |
| Custom PCM | HTTPS endpoint; optional Bearer token; configured voice IDs. | `POST` JSON `{text, voice, language, speed}` and return headerless PCM16 mono at 24 kHz. | Defined by the endpoint |

## Proxy contract

Google and Polly proxy modes are deliberately narrow. The configured HTTPS base must expose:

```text
GET  /v1/voices
POST /v1/synthesize
```

`POST /v1/synthesize` receives `{text, voice, language, speed}` plus a provider-specific `model` for Polly, and must stream headerless signed 16-bit little-endian mono PCM at 24 kHz. An optional configured token is sent as `Authorization: Bearer …`. UtterMux does not provide or operate a public proxy.

## Credential guidance

- Restrict every cloud key to synthesis and voice-list operations where the provider supports scopes or IAM policies.
- A Google API key is not OAuth. Restrict it to Cloud Text-to-Speech, the Android package/signing certificate where applicable, and billing quotas.
- Cognito mode does not accept a permanent AWS secret. It exchanges an identity-pool ID for temporary credentials; the pool must explicitly permit unauthenticated identities and grant only the two Polly actions above.
- Direct AWS access keys are supported for desktop and Android, but a dedicated least-privilege IAM identity is safer than a general account key.
- Proxy mode is for a server the user controls. OAuth or workload credentials terminate at that server; the app only stores the optional proxy bearer token.

Network contracts are covered by deterministic request-shape tests. Paid live calls are not made by public CI and remain an explicit user verification step.
