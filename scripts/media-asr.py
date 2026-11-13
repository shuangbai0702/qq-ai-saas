"""One bounded, offline CPU ASR invocation owned by the existing media process tree.

Install faster-whisper==1.2.1 in a dedicated venv; provision the model separately.
No model downloads, service, credentials, speaker identity or audio-event claims.
"""
import argparse
import importlib.metadata
import json
import math
import os
from pathlib import Path
import sys
import wave


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True, type=Path)
    parser.add_argument('--audio', type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    if not args.model.is_absolute() or not args.model.is_dir():
        raise ValueError('Absolute offline model directory required')
    for name in ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt'):
        if not (args.model / name).is_file():
            raise ValueError('Incomplete offline model: ' + name)
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['OMP_NUM_THREADS'] = '1'
    from faster_whisper import WhisperModel
    import numpy as np
    version = importlib.metadata.version('faster-whisper')
    if args.check:
        print(json.dumps({'engine': 'faster-whisper', 'version': version,
                          'modelFilesPresent': True, 'inferenceVerified': False}))
        return
    if not args.audio or not args.output or not args.audio.is_absolute() or not args.output.is_absolute():
        raise ValueError('Absolute audio/output paths required')
    if args.output.exists():
        raise ValueError('Output already exists')
    # Validate before loading the model; the caller supplies only a bounded PCM clip.
    with wave.open(str(args.audio), 'rb') as wav:
        duration = wav.getnframes() / wav.getframerate()
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000):
            raise ValueError('Expected mono 16kHz PCM16 WAV')
        if not 0 < duration <= 120.1:
            raise ValueError('Clip outside 120 second bound')
        pcm = wav.readframes(wav.getnframes())
        if len(pcm) != wav.getnframes() * 2:
            raise ValueError('Truncated PCM input')
        audio = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768.0
    model = WhisperModel(str(args.model), device='cpu', compute_type='int8',
                         cpu_threads=1, num_workers=1, local_files_only=True)
    segments, info = model.transcribe(audio, language='zh', beam_size=1,
        condition_on_previous_text=False, vad_filter=True, word_timestamps=False)
    rows = []
    for segment in segments:
        start, end, text = float(segment.start), float(segment.end), segment.text.strip()
        if not all(map(math.isfinite, (start, end))) or not 0 <= start <= end <= duration + .1:
            raise ValueError('Invalid model timestamp')
        if len(rows) >= 1000 or len(text) > 2000:
            raise ValueError('Transcript exceeds output bound')
        if text:
            rows.append({'start': start, 'end': min(end, duration), 'text': text})
    result = {'segments': rows, 'engine': 'faster-whisper', 'version': version,
              'language': info.language, 'duration': duration,
              'timestampBasis': 'model segment estimate; not sample alignment'}
    data = json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8')
    if len(data) > 262144:
        raise ValueError('Transcript exceeds byte bound')
    # Exclusive creation prevents an earlier result from being silently adopted.
    with args.output.open('xb') as out:
        out.write(data)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('ASR failed: ' + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
