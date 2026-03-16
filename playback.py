import os, sys, warnings, threading, time, math, array
import numpy as np

os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
warnings.filterwarnings("ignore", category=UserWarning, module='pygame')

import pygame

# Audio Settings
EQ_LOW = 1.8
EQ_MID = 1.5
EQ_HIGH = 0.2
VOLUME = 0.2

SAMPLE_RATE = 44100
SAMPLE_SIZE = -16
CHANNELS = 2
BUFFER_SIZE = 2048
MAX_VOICES = 8000

# Envelope (In decimal percent)
ATTACK = 0.1
DECAY = 0.1

# Simulator Things
SCORE_FILENAME = input("Score file: ") + ".txt"
RADIO_BUS = -1
AUDIO_ACTIVE = True
CURRENT_BPM = 0

FINISHED_THREADS = 0
TOTAL_THREADS_STARTED = 0

# Structures
NOTE_MAP = {'C':0, 'C#':1, 'D':2, 'D#':3, 'E':4, 'F':5, 'F#':6, 'G':7, 'G#':8, 'A':9, 'A#':10, 'B':11}
SONG_METADATA = {"SONG": "Unknown", "AUTHOR": "Unknown", "BPM": 120, "BPB": 16}

def load_score_file(filename):
    file_path = os.path.join("scores", filename)

    if not os.path.exists(file_path):
        sys.exit(f"Error: {file_path} not found!")

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
                val = value.strip()

                if key in SONG_METADATA: SONG_METADATA[key] = int(val) if val.isdigit() else val

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
            tempo_map[t_ptr] = int(bpm_val)
            t_ptr += int(dur)
    else:
        tempo_map = {0: SONG_METADATA["BPM"]}

    return parts, tempo_map

def note_to_frequency(note_str):
    if not note_str or any(c in note_str.upper() for c in ["R", "-", " "]): return 0

    try:
        raw_name = note_str.split(':')[0].upper()

        octave = int(raw_name[-1]) if raw_name[-1].isdigit() else 4
        note_name = raw_name[:-1] if raw_name[-1].isdigit() else raw_name

        n = NOTE_MAP[note_name] + (octave + 1) * 12
        return 440 * (2 ** ((n - 69) / 12))
    except:
        return 0

def generate_tone(frequency, duration_seconds):
    if frequency <= 0: return None

    total_samples = int(SAMPLE_RATE * duration_seconds)
    t = np.linspace(0, duration_seconds, total_samples, False)

    # 1. BASE WAVE
    val = np.sin(2 * np.pi * frequency * t)

    # 2. BASS REINFORCEMENT (The "Sub" Fix)
    if frequency < 261:
        # Add a sub-octave (0.5x freq) for depth and a 2nd harmonic (2x freq) for body
        sub = 0.5 * np.sin(1 * np.pi * frequency * t)
        body = 0.3 * np.sin(4 * np.pi * frequency * t)

        # Blend them and use a gentle tanh to glue it together
        # We divide by 1.8 to keep the amplitude from clipping early
        val = np.tanh((val + sub + body) / 1.2)

    # 3. ENVELOPE
    attack_samples = int(total_samples * ATTACK)
    fixed_release_sec = 0.05
    release_samples = min(int(SAMPLE_RATE * fixed_release_sec), total_samples // 2)

    envelope = np.ones(total_samples)
    if attack_samples > 0:
        envelope[:attack_samples] = np.linspace(0, 1, attack_samples)
    if release_samples > 0:
        envelope[-release_samples:] = np.linspace(1, 0, release_samples)

    # 4. GAIN STAGING
    current_note_volume = VOLUME
    if frequency < 261:
        current_note_volume *= EQ_LOW
    elif frequency > 2000:
        current_note_volume *= EQ_HIGH
    else:
        current_note_volume *= EQ_MID

    # 5. MASTER SOFT-LIMITER
    # We round off the final signal at 0.15 to prevent high-end buzzing
    # while allowing the bass to remain thick.
    raw_signal = val * envelope * current_note_volume
    final_signal = np.tanh(raw_signal / 0.15) * 0.15

    # 6. CONVERT TO INT16
    audio_data = (final_signal * 32767).astype(np.int16)
    stereo_data = np.column_stack((audio_data, audio_data))

    return pygame.mixer.Sound(buffer=stereo_data)

def voice_worker_thread(part_batch, tempo_map):
    global RADIO_BUS, AUDIO_ACTIVE
    parsed_voices = []
    tempo_ticks = sorted(tempo_map.keys())
    quarter = 4

    # --- STEP 1: PRE-PROCESS AND PRE-GENERATE ---
    for part_name, score_string in part_batch:
        events = []
        tick_ptr = 0
        for item in score_string.split():
            note, duration_ticks = item.split(':') if ":" in item else (item, 4)
            duration_ticks = int(duration_ticks)

            temp_tick = tick_ptr
            total_dur_sec = 0
            for _ in range(duration_ticks):
                active_bpm = tempo_map[0]
                for t in tempo_ticks:
                    if t <= temp_tick:
                        active_bpm = tempo_map[t]
                    else:
                        break
                total_dur_sec += (1 / quarter) * (60.0 / active_bpm)
                temp_tick += 1

            hz = note_to_frequency(note)
            # Pre-generate the sound object here!
            snd = generate_tone(hz, total_dur_sec) if hz > 0 else None

            events.append({'time': tick_ptr, 'snd': snd})
            tick_ptr += duration_ticks

        parsed_voices.append(events)

    global FINISHED_THREADS
    FINISHED_THREADS += 1

    # --- STEP 2: HIGH-SPEED PLAYBACK LOOP ---
    last_tick = -1
    while AUDIO_ACTIVE:
        if RADIO_BUS != last_tick:
            tick = RADIO_BUS
            for voice in parsed_voices:
                for ev in voice:
                    if ev['time'] == tick:
                        if ev['snd']:
                            ev['snd'].play()
            last_tick = tick

        time.sleep(0.001) # Small sleep to prevent 100% CPU usage

def run_conductor_ui(total_ticks, tempo_map):
    global RADIO_BUS, AUDIO_ACTIVE, CURRENT_BPM

    while FINISHED_THREADS < TOTAL_THREADS_STARTED:
        sys.stdout.write(f"\r[SYSTEM] Loading Voices... ({FINISHED_THREADS}/{TOTAL_THREADS_STARTED}) ")
        sys.stdout.flush()
        time.sleep(0.1)

    minute = 60.0
    half_min = 30

    # Clear screen safely
    os.system('cls' if os.name == 'nt' else 'clear')

    # Pre-calculate total time (existing logic is fine)
    total_song_seconds = 0
    temp_bpm = 120
    for t in range(total_ticks + 1):
        if t in tempo_map: temp_bpm = tempo_map[t]
        total_song_seconds += (minute / temp_bpm) / 4.0
    formatted_total = f"{int(total_song_seconds // minute)}:{int(total_song_seconds % minute):02}"

    print(f"\n 🎵 {SONG_METADATA['SONG']}")
    print(f"    {SONG_METADATA['AUTHOR']}\n")

    song_elapsed_seconds = 0
    last_tick_time = time.perf_counter()

    for tick in range(total_ticks + 1):
        if not AUDIO_ACTIVE: break

        RADIO_BUS = tick
        if tick in tempo_map:
            CURRENT_BPM = tempo_map[tick]

        sec_per_tick = (minute / CURRENT_BPM) / 4.0
        song_elapsed_seconds += sec_per_tick

        if tick % 4 == 0:
            progress = int(half_min * np.clip(tick / total_ticks, 0, 1.0))
            bar = "█" * progress + "░" * (half_min - progress)
            cur_time = f"{int(song_elapsed_seconds // minute)}:{int(song_elapsed_seconds % minute):02}"

            # \r returns to start of line, \033[K clears the line
            sys.stdout.write(f"\r [{bar}] {cur_time} / {formatted_total} \033[K")
            sys.stdout.flush()

        # High-precision sleep
        sleep_time = (last_tick_time + sec_per_tick) - time.perf_counter()
        if sleep_time > 0:
            time.sleep(sleep_time)
        last_tick_time = time.perf_counter()

    print("\n\nPlayback Finished.")
    AUDIO_ACTIVE = False

if __name__ == "__main__":
    pygame.mixer.pre_init(SAMPLE_RATE, SAMPLE_SIZE, CHANNELS, BUFFER_SIZE)
    pygame.init()
    pygame.mixer.init()
    pygame.mixer.set_num_channels(MAX_VOICES)

    PARTS_DATA, TEMPO_MAP = load_score_file(SCORE_FILENAME)

    max_time = 0
    for _, score in PARTS_DATA.items():
        duration = sum(int(i.split(':')[1] if ":" in i else 4) for i in score.split())
        max_time = max(max_time, duration)

    items = list(PARTS_DATA.items())
    chunk = math.ceil(len(items) / 10)

    thread_count = 0
    for i in range(0, len(items), chunk):
        threading.Thread(target=voice_worker_thread, args=(items[i: i+chunk], TEMPO_MAP), daemon=True).start()
        thread_count += 1

    TOTAL_THREADS_STARTED = thread_count

    try: run_conductor_ui(max_time, TEMPO_MAP)
    except KeyboardInterrupt: AUDIO_ACTIVE = False

    pygame.mixer.quit()
    pygame.quit()
