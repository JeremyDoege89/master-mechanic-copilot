# Transcribe in realtime

Stream audio over a WebSocket and receive transcripts, turn boundaries, and speaker labels as the audio arrives.

Connect to `wss://api.meta.ai/v1/asr/realtime`, send the handshake as the first JSON text frame within 10 seconds, then stream raw PCM as binary frames. This endpoint does not read the HTTP `Authorization` header; the credential travels in the handshake. Send `{"type": "endStream"}` to end input and keep reading until the server closes with `1000`.

> This is a WebSocket session. The request body below is the handshake frame, and the event stream is the ordered sequence of server frames that follows the acknowledgement.

## WS /asr/realtime

Open a realtime transcription session.

### Parameters

#### Query parameters

| Field       | Type   | Required | Description                                                                                                                                                                                                                 |
| ----------- | ------ | -------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `sessionId` | string | No       | Correlation id used in server-side logs and returned in the handshake response. When omitted, the server generates one. It is supplied here rather than in the handshake so it is known before the handshake frame arrives. |

### Request body

#### Content Type: application/json

[`RealtimeHandshakeRequest`](https://dev.meta.ai/docs/api-reference/voice/schemas#realtime-handshake-request)

### Response

`HTTP 200` — The handshake acknowledgement, followed by the ordered stream of server events. The acknowledgement is the only server frame with no `type` field.

#### Content Type: application/json

[`RealtimeHandshakeResponse`](https://dev.meta.ai/docs/api-reference/voice/schemas#realtime-handshake-response)

#### Content Type: text/event-stream

[`ServerMessage`](https://dev.meta.ai/docs/api-reference/voice/schemas#server-message)

**Python (websockets)**

```python
import asyncio
import json
import os
import uuid

import websockets

API_KEY = os.environ["MODEL_API_KEY"]

SESSION_ID = f"stream-{uuid.uuid4()}"
ASR_URI = f"wss://api.meta.ai/v1/asr/realtime?sessionId={SESSION_ID}"

# 16-bit little-endian mono PCM at 24 kHz, paced ~real time (80 ms per frame).
RATE = 24_000
FRAME_BYTES = RATE * 2 * 80 // 1000

async def transcribe_stream() -> None:
    async with websockets.connect(ASR_URI, open_timeout=30) as ws:
        # 1. Handshake: the first JSON text frame. The credential travels here,
        #    not in the Authorization header.
        await ws.send(
            json.dumps(
                {
                    "authorization": {"accessToken": f"Bearer {API_KEY}"},
                    "audioEncoding": "PCM_24KHZ",
                    "model": "muse-voice-transcribe-1.0",
                    "mode": "PUSH_TO_TALK",
                }
            )
        )
        handshake = json.loads(await ws.recv())
        if "sessionId" not in handshake:
            raise RuntimeError(f"Handshake failed: {handshake}")

        # 2. Read server events until the socket closes with 1000.
        async def receive_events() -> None:
            async for message in ws:
                if isinstance(message, bytes):
                    continue
                event = json.loads(message)
                if event.get("type") == "error":
                    raise RuntimeError(event["message"])
                if event.get("type") == "transcript":
                    end = "\n" if event.get("final") else "\r"
                    print(event["transcript"], end=end, flush=True)

        receiver = asyncio.create_task(receive_events())

        # 3. Stream raw PCM binary frames.
        with open("recording.pcm", "rb") as audio:
            while chunk := audio.read(FRAME_BYTES):
                await ws.send(chunk)
                await asyncio.sleep(0.08)

        # 4. Half-close input and keep reading until the server closes.
        await ws.send(json.dumps({"type": "endStream"}))
        await receiver

asyncio.run(transcribe_stream())
```

## Client frames

Audio is sent as binary frames. The only client text frame after the handshake is the end-of-input marker, which half-closes the client-to-server direction and leaves the socket open so the server can flush pending results. Closing the socket instead also ends input, but can discard pending events.

**JSON**

```json
{ "type": "endStream" }
```

## Close codes

| Code   | Meaning                                                                                   | What to do                                                                                 |
| ------ | ----------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------ |
| `1000` | Normal completion                                                                         | Finish after consuming the final result.                                                   |
| `1008` | Invalid request, or a streaming-policy failure such as backlog or below-real-time ingress | Correct the configuration or the audio pacing. Retrying the same request fails again.      |
| `1011` | Internal or backend failure                                                               | Retry with backoff. When the reason is `Max session duration reached`, open a new session. |
| `1013` | Rate limited                                                                              | Back off before reconnecting.                                                              |

For modes, endpointing, diarization, session limits, and worked examples, see the [Speech to text](https://dev.meta.ai/docs/speech-to-text) feature page.
