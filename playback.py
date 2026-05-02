import os
import sys
import time
import math
import warnings
import subprocess

import numpy as np
import sounddevice as sd

# Audio Settings
EQ_LOW = 1.3
EQ_MID = 1.6
EQ_HIGH = 0.06
VOLUME = 0.8

SAMPLE_RATE = 44100
CHANNELS = 2

# Envelope (In decimal percent)
ATTACK = 0.1
DECAY = 0.1

# Simulator Things
SCORE_FILENAME = input("Score file: ") + ".txt"
AUDIO_ACTIVE = True

# Structures
NOTE_MAP = {'C':0, 'C#':1, 'D':2, 'D#':3, 'E':4, 'F':5, 'F#':6, 'G':7, 'G#':8, 'A':9, 'A#':10, 'B':11}
SONG_METADATA = {"SONG": "Unknown", "AUTHOR": "Unknown", "BPM": 120.0, "TPB": 4}

warnings.filterwarnings("ignore", category=UserWarning, module='sounddevice')

def load_score_file(filename):
    file_path = os.path.join("scores", filename)
    if not os.path.exists(file_path): sys.exit(f"Error: {file_path} not found!")

    global SONG_METADATA
    parts, tempo_map = {}, {}
    current_part_name = None

    with open(file_path, 'r', encoding='utf-8') as score:
        for line in score:
            line = line.strip()
            if not line or line.startswith('#'): continue
            if ":" in line and not line.startswith('[') and current_part_name is None:
                key, value = line.split(':', 1)
                key = key.strip().upper()
                if key in SONG_METADATA: SONG_METADATA[key] = value.strip()
                continue
            if line.startswith('[') and line.endswith(']'):
                current_part_name = line[1:-1]
                parts[current_part_name] = ""
            elif current_part_name:
                parts[current_part_name] += line + " "

    if "CONDUCTOR" in parts:
        raw_conductor, t_ptr = parts.pop("CONDUCTOR"), 0
        for item in raw_conductor.split():
            bpm_val, dur = item.split(':') if ":" in item else (item, 4)
            tempo_map[t_ptr] = float(bpm_val)
            t_ptr += int(dur)
    else:
        tempo_map = {0: float(SONG_METADATA["BPM"])}

    return parts, tempo_map

def note_to_frequency(note_str):
    if not note_str or any(c in note_str.upper() for c in ["R", "-", " "]):
        return 0
    try:
        raw_name = note_str.split(':')[0].upper()
        octave = int(raw_name[-1]) if raw_name[-1].isdigit() else 4
        note_name = raw_name[:-1] if raw_name[-1].isdigit() else raw_name
        n = NOTE_MAP[note_name] + (octave + 2) * 12
        return 440 * (2 ** ((n - 69) / 12))
    except:
        return 0

def generate_tone(frequency, duration_seconds, pan=0.0):
    if frequency <= 0 or duration_seconds <= 0: return None

    total_samples = max(1, int(round(SAMPLE_RATE * duration_seconds)))
    t = np.linspace(0, duration_seconds, total_samples, False)

    # Base Wave & Bass Reinforcement
    val = np.sin(2 * np.pi * frequency * t)
    if frequency > 1000:
        val *= 0.55
        if frequency > 1500: val = np.convolve(val, np.ones(10) / 10, mode='same')
    elif frequency < 261:
        sub = 0.5 * np.sin(1 * np.pi * frequency * t)
        body = 0.3 * np.sin(4 * np.pi * frequency * t)
        val = np.tanh((val + sub + body) / 1.2)

    # Envelope
    attack_samples = int(total_samples * ATTACK)
    release_samples = min(int(SAMPLE_RATE * 0.05), total_samples // 2)
    envelope = np.ones(total_samples, dtype=np.float32)
    if attack_samples > 0: envelope[:attack_samples] = np.linspace(0, 1, attack_samples)
    if release_samples > 0: envelope[-release_samples:] = np.linspace(1, 0, release_samples)

    # Gain & Pan
    current_vol = VOLUME * (EQ_LOW if frequency < 261 else EQ_HIGH if frequency > 2000 else EQ_MID)
    final_signal = np.tanh((val * envelope * current_vol) / 0.12) * 0.12

    left_gain = math.sqrt((1.0 - np.clip(-pan, -1.0, 0)) / 2.0)
    right_gain = math.sqrt((1.0 + np.clip(pan, -1.0, 0)) / 2.0)
    stereo = np.column_stack((final_signal * left_gain, final_signal * right_gain)).astype(np.float32)
    return stereo

def build_mix(parts, tempo_map):
    tempo_ticks = sorted(tempo_map.keys())
    ticks_per_beat = int(SONG_METADATA.get('TPB', 4))
    voice_count = len(parts)
    pan_positions = np.linspace(-0.7, 0.7, voice_count) if voice_count > 1 else [0.0]
    events, total_seconds = [], 0.0

    for voice_idx, (part_name, score_string) in enumerate(parts.items()):
        pan, current_time, tick_ptr = float(pan_positions[voice_idx]), 0.0, 0
        for item in score_string.split():
            note, duration_ticks = item.split(':') if ":" in item else (item, 4)
            duration_ticks = int(duration_ticks)
            duration_seconds = 0.0
            for i in range(duration_ticks):
                active_bpm = tempo_map[0]
                for t in tempo_ticks:
                    if t <= (tick_ptr + i): active_bpm = tempo_map[t]
                    else: break
                duration_seconds += (60.0 / active_bpm) / ticks_per_beat

            hz = note_to_frequency(note)
            if hz > 0:
                samples = generate_tone(hz, duration_seconds, pan=pan)
                if samples is not None: events.append((current_time, samples))

            current_time += duration_seconds
            tick_ptr += duration_ticks
        total_seconds = max(total_seconds, current_time)

    total_samples = max(1, int(math.ceil(total_seconds * SAMPLE_RATE)))
    mix = np.zeros((total_samples, CHANNELS), dtype=np.float32)
    for start_time, samples in events:
        start_idx = int(round(start_time * SAMPLE_RATE))
        end_idx = min(total_samples, start_idx + len(samples))
        if start_idx < total_samples: mix[start_idx:end_idx] += samples[:end_idx - start_idx]

    return np.clip(mix, -1.0, 1.0), total_seconds

def run_conductor_ui(total_seconds):
    global AUDIO_ACTIVE
    start_time = time.perf_counter()
    sd.play(MIX_BUFFER, SAMPLE_RATE)

    try:
        while AUDIO_ACTIVE:
            elapsed = time.perf_counter() - start_time
            if elapsed >= total_seconds:
                break

            progress = int(30 * min(elapsed / total_seconds, 1.0))
            bar = "█" * progress + "░" * (30 - progress)
            cur_time = f"{int(elapsed // 60)}:{int(elapsed % 60):02}"
            total_time = f"{int(total_seconds // 60)}:{int(total_seconds % 60):02}"
            sys.stdout.write(f"\r [{bar}] {cur_time} / {total_time} \033[K")
            sys.stdout.flush()
            time.sleep(0.05)
    except KeyboardInterrupt:
        AUDIO_ACTIVE = False
        sd.stop()
        raise

    sd.wait()
    print("\n\nPlayback Finished.")
    AUDIO_ACTIVE = False


def shutdown_audio():
    global AUDIO_ACTIVE
    AUDIO_ACTIVE = False
    try:
        sd.stop()
    except Exception:
        pass


if __name__ == "__main__":
    PARTS_DATA, TEMPO_MAP = load_score_file(SCORE_FILENAME)
    MIX_BUFFER, TOTAL_SECONDS = build_mix(PARTS_DATA, TEMPO_MAP)

    subprocess.call('cls' if os.name == 'nt' else 'clear', shell=True)

    print(f"\n 🎵 {SONG_METADATA['SONG']}")
    print(f"    {SONG_METADATA['AUTHOR']}\n")

    try:
        run_conductor_ui(TOTAL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopping playback...")
    finally:
        shutdown_audio()
