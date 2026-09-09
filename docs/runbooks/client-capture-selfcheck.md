# Runbook: client capture self-check

Verifies that WASAPI loopback capture works on a given Windows machine, and that
the conversion and framing stages produce a continuous canonical timeline.

This is a **diagnostic**, not a quality test. It says nothing about how well
anything is transcribed (TEST-130); it says whether audio reaches the pipeline
and comes out the far side correctly framed.

## Before you start

**Something must be playing through the endpoint you are testing.**

A WASAPI loopback endpoint with nothing going through it delivers no callbacks at
all — it does not deliver silence. A run with nothing playing proves only that
the device opened, and the tool exits non-zero to say so rather than reporting a
pass. Start a video, a music player, or the meeting application itself first.

## Setup on the user machine

The machine has no Git working copy, so the source arrives as an archive.

1. On GitHub, open the branch you were asked to test and choose
   **Code → Download ZIP**.
2. Extract it anywhere, for example into your Downloads folder.
3. Open PowerShell in that folder.

```powershell
python --version
python -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements\client.in
```

Python must be 3.11.x. `requirements/client.in` lists the client dependency
domain only; nothing from the server or the ML stack is installed.

## Step 1 — list the loopback endpoints

```powershell
.venv\Scripts\python.exe tools\capture_selfcheck.py --list
```

Expected: one line per playback device, with the system default marked. For
example:

```text
[8] SAMSUNG (NVIDIA High Definition Audio) [Loopback] (48000 Hz, 2 ch, system default)
[9] Realtek Digital Output (Realtek(R) Audio) [Loopback] (48000 Hz, 2 ch)
```

An empty list means no playback endpoint is enabled and there is nothing to
capture. Enable one in Windows sound settings and try again.

## Step 2 — capture with audio playing

Start playback, then:

```powershell
.venv\Scripts\python.exe tools\capture_selfcheck.py --seconds 10
echo $LASTEXITCODE
```

To test a specific endpoint rather than the default, pass its name exactly as
`--list` printed it:

```powershell
.venv\Scripts\python.exe tools\capture_selfcheck.py --seconds 10 --device "Realtek Digital Output (Realtek(R) Audio) [Loopback]"
```

## What a good result looks like

```text
callback
  callbacks               470            <- well above zero
  device frames           481,280 (10.03 s)
  PortAudio overflows     0              <- must be 0

ring buffer
  device frames dropped   0              <- must be 0

conversion
  canonical samples       160,426 (10.03 s)
  expected                160,426        <- must match within a few samples
  resampler failures      0              <- must be 0

framing
  sequence contiguous     True           <- must be True
  sample offsets exact    True           <- must be True

idle endpoint
  idle periods            0              <- 0 while audio plays continuously

level (diagnostic only, never a quality claim)
  peak                    0.42           <- above 0.0001 means sound arrived
```

Exit code 0 means every structural check passed.

## What to send back

Paste the **whole** output, including the device line, plus:

- what was playing during the run;
- the Windows version and the machine's default playback device;
- the exit code.

## Failure modes and what they mean

| Symptom | Meaning |
|---|---|
| `NOTHING WAS CAPTURED` | Nothing was playing, or the endpoint is disabled. Not a code failure — rerun with audio playing |
| `PortAudio overflows` above 0 | The callback could not keep up. Report it; the callback is meant to do nothing but copy |
| `device frames dropped` above 0 | The consumer fell behind and the ring evicted audio. Report the number |
| `sequence contiguous False` | A framing bug. Report immediately — this should be impossible |
| `sample offsets exact False` | A timeline bug. Report immediately — this is the failure PROT-180 exists to prevent |
| `resampler failures` above 0 | Conversion failed on real device audio. Report the device's reported format |
| `idle periods` above 0 while audio played | The endpoint stopped delivering mid-run. Report what was playing |

## What this does not test

- The WebSocket transport. There is no server yet; that arrives in Phase 4.
- Anything about transcription, language, speakers or translation.
- Long-run behaviour. The mandatory soak test is a separate exercise over the
  full 30-minute recording (TEST-180).
