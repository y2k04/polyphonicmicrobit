import os, sys, warnings, threading, time, math, array
import numpy as np

os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
warnings.filterwarnings("ignore", category=UserWarning, module='pygame')

import pygame

# Audio Settings
EQ_LOW = 1.4
EQ_MID = 1.5
EQ_HIGH = 0.2
VOLUME = 0.12

SAMPLE_RATE = 44100
SAMPLE_SIZE = -16
CHANNELS = 1
BUFFER_SIZE = 64
MAX_VOICES = 8000

# Envelope (In decimal percent)
ATTACK = 0.1
DECAY = 0.1

# Simulator Things
SCORE_FILENAME = input("Score file: ") + ".txt"
RADIO_BUS = -1
AUDIO_ACTIVE = True
CURRENT_BPM = 0

# Structures
NOTE_MAP = {'C':0, 'C#':1, 'D':2, 'D#':3, 'E':4, 'F':5, 'F#':6, 'G':7, 'G#':8, 'A':9, 'A#':10, 'B':11}
SONG_METADATA = {"SONG": "Unknown", "AUTHOR": "Unknown", "BPM": 120, "BPB": 16}

def load_score_file(filename):
    file_path = f"scores/{filename}"

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
                
                if key in SONG_METADATA: SONG_METADATA[key] = float(val) if val.isdigit() else val

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
            t_ptr += float(dur)
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
    audio_buffer = array.array('h')
    current_note_volume = VOLUME

    if frequency < 261:
        current_note_volume *= EQ_LOW
    elif frequency > 2000:
        current_note_volume *= EQ_HIGH
    else:
        current_note_volume *= EQ_MID

    attack_pct, decay_pct = ATTACK, (DECAY if frequency >= 261 else ATTACK)
    attack_samples, decay_samples = float(total_samples * attack_pct), float(total_samples * decay_pct)

    for i in range(total_samples):
        time = float(i / SAMPLE_RATE)
        val = math.sin(2.0 * math.pi * frequency * time)

        if frequency < 261:
            val = math.tanh(((val * 0.75) + (0.25 * math.sin(4.0 * math.pi * frequency * time))) * 1.1)
        if frequency > 2000:
            val = math.tanh(val * 1.1)

        if i < attack_samples:
            val *= (i / attack_samples)
        elif i > (total_samples - decay_samples):
            val *= ((total_samples - i) / decay_samples)

        audio_buffer.append(int(np.iinfo(np.int16).max * math.tanh(val * current_note_volume)))
    
    return pygame.mixer.Sound(buffer=audio_buffer)

def voice_worker_thread(part_batch, tempo_map):
    minute, second, quarter = 60000.0, 1000, 4

    global RADIO_BUS, AUDIO_ACTIVE
    parsed_voices, tempo_ticks = [], sorted(tempo_map.keys())

    for part_name, score_string in part_batch:
        events, tick_ptr = [], 0

        for item in score_string.split():
            note, duration_ticks = item.split(':') if ":" in item else (item, 4)
            duration_ticks = float(duration_ticks)
            active_bpm = tempo_map[0]

            for t in tempo_ticks:
                if t <= tick_ptr:
                    active_bpm = tempo_map[t]
                else:
                    break
            
            dur_seconds = (duration_ticks / quarter) * (minute / active_bpm) / second

            events.append({'time': tick_ptr, 'hz': note_to_frequency(note), 'duration': dur_seconds})
            tick_ptr += duration_ticks
        
        parsed_voices.append(events)

    last_tick = -1
    while AUDIO_ACTIVE:
        if RADIO_BUS != last_tick:
            tick = RADIO_BUS
            last_tick = tick

            for voice in parsed_voices:
                for ev in voice:
                    if ev['time'] == tick:
                        snd = generate_tone(ev['hz'], ev['duration'])
                        if snd: snd.play()
        
        time.sleep(0)

def run_conductor_ui(total_ticks, tempo_map):
    global RADIO_BUS, AUDIO_ACTIVE, CURRENT_BPM
    minute, half_min, quarter = 60.0, 30, 4.0

    os.system(("cls||clear"))
    
    total_song_seconds, temp_bpm = 0, 120.0
    for t in range(total_ticks + 1):
        if t in tempo_map: temp_bpm = tempo_map[t]
        total_song_seconds += (minute / temp_bpm) / quarter
    formatted_total = f"{int(total_song_seconds // minute)}:{int(total_song_seconds % minute):02}"

    song_elapsed_seconds = 0.0
    last_tick_time = time.perf_counter()
    
    print("\n")
    sys.stdout.write(f" 🎵 {SONG_METADATA['SONG']}\n    {SONG_METADATA['AUTHOR']}\n\n")

    for tick in range(total_ticks + 1):
        if not AUDIO_ACTIVE: break

        RADIO_BUS = tick
        if tick in tempo_map:
            CURRENT_BPM = tempo_map[tick]
        sec_per_tick = (minute / CURRENT_BPM) / quarter
        song_elapsed_seconds += sec_per_tick
        
        if tick % 4 == 0:
            sys.stdout.write("\033[1F") # Move cursor up 1 line to overwrite progress and time

            progress = int(half_min * np.clip(tick / total_ticks, 0.0, np.inf))
            bar = "█" * progress + "░" * (half_min - progress)

            cur_time = f"{int(song_elapsed_seconds // minute)}:{int(song_elapsed_seconds % minute):02}"

            sys.stdout.write(f"\n [{bar}] {cur_time} / {formatted_total} ")
            sys.stdout.flush()
        
        time.sleep(np.clip((last_tick_time + sec_per_tick) - time.perf_counter(), 0.0, np.inf))
        last_tick_time = time.perf_counter()

    AUDIO_ACTIVE = False

if __name__ == "__main__":
    pygame.mixer.pre_init(SAMPLE_RATE, SAMPLE_SIZE, CHANNELS, BUFFER_SIZE)
    pygame.init()
    pygame.mixer.set_num_channels(MAX_VOICES)

    PARTS_DATA, TEMPO_MAP = load_score_file(SCORE_FILENAME)

    max_time = 0
    for _, score in PARTS_DATA.items():
        duration = sum(int(i.split(':')[1] if ":" in i else 4) for i in score.split())
        max_time = max(max_time, duration)

    items = list(PARTS_DATA.items())
    chunk = math.ceil(len(items) / 10)

    for i in range(0, len(items), chunk):
        threading.Thread(target=voice_worker_thread, args=(items[i: i+chunk], TEMPO_MAP), daemon=True).start()
    
    try: run_conductor_ui(max_time, TEMPO_MAP)
    except KeyboardInterrupt: AUDIO_ACTIVE = False

    pygame.quit()