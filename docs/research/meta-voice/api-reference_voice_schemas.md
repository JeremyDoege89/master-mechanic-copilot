# Voice schemas

The full schema and model definitions referenced by the [Audio](https://dev.meta.ai/docs/api-reference/voice/transcribe) endpoints. Each endpoint page links here for the detailed shape of its request and response objects.

## Schemas

### Audio Progress Event

One update on the independent audio-progress channel, multiplexed onto the same ordered event stream as transcript, lifecycle, and speaker events. Not part of any speech turn.

| Field              | Type                     | Required | Description                                                                 |
| ------------------ | ------------------------ | -------- | --------------------------------------------------------------------------- |
| `type`             | `enum ('audioProgress')` | Yes      | Event discriminator; always `audioProgress` for this event.                 |
| `audioProcessedMs` | `integer (int64)`        | Yes      | Total audio processed so far, in milliseconds from the start of the stream. |

### Authorization

Credential authorizing the session. Consumed only by the server's authentication step and never forwarded to the model.

| Field         | Type     | Required | Description                                                                |
| ------------- | -------- | -------- | -------------------------------------------------------------------------- |
| `accessToken` | `string` | Yes      | `Bearer ` followed by your Model API key. The prefix is part of the value. |

### Error Event

Fatal error the server sends just before it closes the session. The message is client-safe; internal detail is logged server-side only.

| Field        | Type             | Required | Description                                                             |
| ------------ | ---------------- | -------- | ----------------------------------------------------------------------- |
| `type`       | `enum ('error')` | Yes      | Event discriminator; always `error` for this event.                     |
| `message`    | `string`         | Yes      | Human-readable, client-safe error message.                              |
| `sessionId`  | `string`         | Yes      | Session id for log correlation. Empty if one has not been assigned yet. |
| `errorType`  | `string`         | No       | Model API error taxonomy for this failure.                              |
| `errorCode`  | `string`         | No       | Machine-readable error code, when the failure has one.                  |
| `errorParam` | `string`         | No       | Request field the error relates to, when applicable.                    |

### Realtime Handshake Request

The first JSON text frame the client sends to open a realtime session, within 10 seconds of the socket opening. Configuration is fixed once the handshake is accepted.

| Field               | Type                                                                                  | Required | Description                                                                                                                                                                                                                                                                                                                                            |
| ------------------- | ------------------------------------------------------------------------------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `authorization`     | [`Authorization`](https://dev.meta.ai/docs/api-reference/voice/schemas#authorization) | Yes      |                                                                                                                                                                                                                                                                                                                                                        |
| `audioEncoding`     | `enum ('PCM_24KHZ', 'PCM_16KHZ')`                                                     | Yes      | Encoding of the streamed binary frames, sent as the enum name. Both values are signed 16-bit little-endian mono. `PCM_24KHZ` is 48,000 bytes per second and is the model's native rate; `PCM_16KHZ` is 32,000 bytes per second and is resampled server-side. There is no default; an unset or unrecognized value is rejected.                          |
| `model`             | `string`                                                                              | Yes      | Public model id. The model must support the requested mode.                                                                                                                                                                                                                                                                                            |
| `mode`              | `enum ('PUSH_TO_TALK', 'ENDPOINTING', 'DIARIZATION')`                                 | No       | Transcription behavior, sent as the enum name. `PUSH_TO_TALK` is single-turn and the client delimits the turn by ending the stream. `ENDPOINTING` detects speech onset and endpoint, one turn per detected segment. `DIARIZATION` additionally attributes turns to speakers. Mode availability depends on the model. (default: PUSH\_TO\_TALK)         |
| `partialMode`       | `enum ('CUMULATIVE', 'DELTA')`                                                        | No       | Representation of partial transcript text, sent as the enum name. `CUMULATIVE` sends the complete current hypothesis each time, replacing the previous partial, so the model can revise text it already emitted. `DELTA` sends only newly emitted text for the client to append, and is not compatible with every model or mode. (default: CUMULATIVE) |
| `emitAudioProgress` | `boolean`                                                                             | No       | Whether the server emits `audioProgress` events. Compatible runtimes emit one event per processed audio chunk. (default: true)                                                                                                                                                                                                                         |
| `keywords`          | `array of string`                                                                     | No       | Terms to bias recognition toward, such as names, jargon, and product words the model would otherwise mishear. Biasing does not guarantee an exact spelling.                                                                                                                                                                                            |
| `languageBias`      | `array of string`                                                                     | No       | Languages to bias transcription toward, each as a language name, e.g. `["English", "French"]`. A list of languages, not free-form context. Omit it to let the model detect the language.                                                                                                                                                               |

### Realtime Handshake Response

Server reply to a successful handshake, sent before any transcript frames. This is the only server frame with no `type` field.

| Field       | Type     | Required | Description                                |
| ----------- | -------- | -------- | ------------------------------------------ |
| `sessionId` | `string` | Yes      | Session id for end-to-end log correlation. |

### Server Message

Envelope for every server event after the handshake. The `type` field names the event and its payload is merged alongside it. Transcript, lifecycle, speaker, and progress events are multiplexed into one ordered stream. Dispatch on `type` and ignore unknown values.

Type: [`TranscriptEvent`](https://dev.meta.ai/docs/api-reference/voice/schemas#transcript-event) | [`SpeechStartEvent`](https://dev.meta.ai/docs/api-reference/voice/schemas#speech-start-event) | [`SpeechEndEvent`](https://dev.meta.ai/docs/api-reference/voice/schemas#speech-end-event) | [`SpeechCompleteEvent`](https://dev.meta.ai/docs/api-reference/voice/schemas#speech-complete-event) | [`SpeakerEvent`](https://dev.meta.ai/docs/api-reference/voice/schemas#speaker-event) | [`AudioProgressEvent`](https://dev.meta.ai/docs/api-reference/voice/schemas#audio-progress-event) | [`ErrorEvent`](https://dev.meta.ai/docs/api-reference/voice/schemas#error-event)

### Speaker Event

Labels the transcript span immediately before it: the span starts at the previous `speechStart` or `speaker` event, whichever came later, and ends here. Emitted in `DIARIZATION`. Does not indicate a speech boundary, a speaker change, or a final transcript, and the same label can repeat.

| Field              | Type               | Required | Description                                                                                                                       |
| ------------------ | ------------------ | -------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `type`             | `enum ('speaker')` | Yes      | Event discriminator; always `speaker` for this event.                                                                             |
| `label`            | `string`           | Yes      | Model-generated speaker label, such as `A`. Meaningful only within one session, and not a verified identity.                      |
| `audioProcessedMs` | `integer (int64)`  | Yes      | Audio processed when the model emitted this label, in milliseconds from the start of the stream. Not a precise acoustic boundary. |

### Speech Complete Event

The completed transcript for one speech turn, including any post processing.

| Field              | Type                      | Required | Description                                                                                                                         |
| ------------------ | ------------------------- | -------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| `type`             | `enum ('speechComplete')` | Yes      | Event discriminator; always `speechComplete` for this event.                                                                        |
| `audioProcessedMs` | `integer (int64)`         | Yes      | Audio processed when the model emitted this event, in milliseconds from the start of the stream. Not a precise acoustic boundary.   |
| `turnId`           | `integer (int32)`         | Yes      | Id for this speech turn. Correlate lifecycle events by equality within the session; numbering is not otherwise meaningful.          |
| `transcript`       | `string`                  | Yes      | Transcript for the entire turn. It can differ from the last partial, because the model may post-process the turn after speech ends. |

### Speech End Event

The model detected the end of speech. A boundary event, not the final transcript: use `speechComplete` for the turn's text.

| Field              | Type                 | Required | Description                                                                                                                       |
| ------------------ | -------------------- | -------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `type`             | `enum ('speechEnd')` | Yes      | Event discriminator; always `speechEnd` for this event.                                                                           |
| `audioProcessedMs` | `integer (int64)`    | Yes      | Audio processed when the model emitted this event, in milliseconds from the start of the stream. Not a precise acoustic boundary. |
| `turnId`           | `integer (int32)`    | Yes      | Id for this speech turn.                                                                                                          |

### Speech Start Event

The model detected the beginning of speech.

| Field              | Type                   | Required | Description                                                                                                                        |
| ------------------ | ---------------------- | -------- | ---------------------------------------------------------------------------------------------------------------------------------- |
| `type`             | `enum ('speechStart')` | Yes      | Event discriminator; always `speechStart` for this event.                                                                          |
| `audioProcessedMs` | `integer (int64)`      | Yes      | Audio processed when the model emitted this event, in milliseconds from the start of the stream. Not a precise acoustic boundary.  |
| `turnId`           | `integer (int32)`      | Yes      | Id for this speech turn. A later turn can open before an earlier turn's `speechComplete` arrives, so keep state keyed by `turnId`. |

### Transcribe Request

The `request` part of the multipart body, sent as JSON.

| Field               | Type                                                  | Required | Description                                                                                                                                                                                                                                                                                                                |
| ------------------- | ----------------------------------------------------- | -------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `model`             | `string`                                              | Yes      | Public model id. The model must support the requested mode.                                                                                                                                                                                                                                                                |
| `audioEncoding`     | `enum ('WAV')`                                        | Yes      | Container of the uploaded `audio` part, sent as the enum name. `WAV` is a RIFF/WAVE container holding mono integer PCM at 16 kHz or 24 kHz. There is no default; an unset or unrecognized value is rejected.                                                                                                               |
| `mode`              | `enum ('PUSH_TO_TALK', 'ENDPOINTING', 'DIARIZATION')` | No       | Transcription behavior, sent as the enum name. `PUSH_TO_TALK` is single-turn and the uploaded clip is the turn. `ENDPOINTING` detects speech onset and endpoint, one turn per detected segment. `DIARIZATION` additionally attributes turns to speakers. Mode availability depends on the model. (default: PUSH\_TO\_TALK) |
| `keywords`          | `array of string`                                     | No       | Terms to bias recognition toward, such as names, jargon, and product words the model would otherwise mishear. Biasing does not guarantee an exact spelling.                                                                                                                                                                |
| `languageBias`      | `array of string`                                     | No       | Languages to bias transcription toward, each as a language name, e.g. `["English", "French"]`. A list of languages, not free-form context. Omit it to let the model detect the language.                                                                                                                                   |
| `partialMode`       | `enum ('CUMULATIVE', 'DELTA')`                        | No       | Representation of partial transcript text, sent as the enum name. Applies to `text/event-stream` responses only. `CUMULATIVE` replaces the previous partial; `DELTA` sends only newly emitted text for the client to append. (default: CUMULATIVE)                                                                         |
| `emitAudioProgress` | `boolean`                                             | No       | Whether to emit `audioProgress` events. Applies to `text/event-stream` responses only; the buffered responses never surface them. (default: true)                                                                                                                                                                          |

### Transcribe Response

The buffered `application/json` response.

| Field             | Type                                                                         | Required | Description                                                                                                                                                                                |
| ----------------- | ---------------------------------------------------------------------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `sessionId`       | `string`                                                                     | Yes      | The `sessionId` query parameter, or the id the server generated when it was absent.                                                                                                        |
| `transcript`      | `string`                                                                     | Yes      | Final transcript for the whole clip. Populated in every mode; in the multi-turn modes it is the turn transcripts joined in turn order.                                                     |
| `audioDurationMs` | `integer (int64)`                                                            | Yes      | Total audio duration processed, in milliseconds.                                                                                                                                           |
| `turns`           | array of [`Turn`](https://dev.meta.ai/docs/api-reference/voice/schemas#turn) | Yes      | Every turn the model reported, in ascending `turnId` order. Empty in `PUSH_TO_TALK`. A turn the clip ended part-way through carries the boundaries the model gave and an empty transcript. |

### Transcript Event

One streamed transcript update.

| Field              | Type                  | Required | Description                                                                                                                                                                             |
| ------------------ | --------------------- | -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `type`             | `enum ('transcript')` | Yes      | Event discriminator; always `transcript` for this event.                                                                                                                                |
| `transcript`       | `string`              | Yes      | Transcript text for this segment. Under `CUMULATIVE` it replaces the previous partial; under `DELTA` it is appended.                                                                    |
| `final`            | `boolean`             | Yes      | Transcript stability as reported by the model. In `PUSH_TO_TALK`, `true` marks stream completion. In `ENDPOINTING` and `DIARIZATION`, use `speechComplete` for turn completion instead. |
| `audioProcessedMs` | `integer (int64)`     | Yes      | Total audio processed so far, in milliseconds.                                                                                                                                          |

### Turn

One speech turn the model detected, reported in the buffered transcribe response. Populated in `ENDPOINTING` and `DIARIZATION` only.

| Field        | Type              | Required | Description                                                                                                                                   |
| ------------ | ----------------- | -------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `turnId`     | `integer (int32)` | Yes      | Id for this turn. Turns are reported in ascending id order; numbering is not otherwise meaningful.                                            |
| `startMs`    | `integer (int64)` | Yes      | Audio processed when the model detected the turn's speech onset, in milliseconds from the start of the clip. Not a precise acoustic boundary. |
| `endMs`      | `integer (int64)` | Yes      | Audio processed when the model detected the turn's speech end, in milliseconds from the start of the clip. Not a precise acoustic boundary.   |
| `transcript` | `string`          | Yes      | Finalized transcript for this turn, including any post processing.                                                                            |
| `speaker`    | `string`          | No       | Model-generated speaker label, such as `A`. Present in `DIARIZATION` only.                                                                    |
