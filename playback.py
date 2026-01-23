import threading
import time
import math
import array
import sys
import os
from string import punctuation

os.environ['PYGAME_HIDE_SUPPORT_PROMPT'] = '1'
import pygame

SCORE_FILE = input("Score file: ") + ".txt"
RADIO_BUS = -1
RUNNING = True
current_status = {}

# Metadata Defaults
# (BPB = Beats per bar)
METADATA = {
    "SONG": "Unknown",
    "AUTHOR": "Unknown",
    "BPM": 120,
    "BPB": 16
}

def load_score(filename):
    global METADATA
    parts = {}
    current_part = None
    filename = "scores/" + filename
    
    if not os.path.exists(filename):
        print(f"Error: {filename} not found!")
        sys.exit()

    with open(filename, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'): continue
            
            if ":" in line and not line.startswith('[') and current_part is None:
                key, value = line.split(':', 1)
                key = key.strip().upper()
                if key in METADATA:
                    if key == "BPM": METADATA["BPM"] = int(value.strip())
                    elif key == "BPB": METADATA["BPB"] = int(value.strip())
                    else: METADATA[key] = value.strip()
                continue

            if line.startswith('[') and line.endswith(']'):
                current_part = line[1:-1]
                parts[current_part] = ""
            elif current_part:
                parts[current_part] += line + " "
    return parts

# Audio engine
pygame.mixer.pre_init(44100, -16, 1, 512)
pygame.init()

def note_to_freq(note_str):
    if not note_str or any(c in note_str.upper() for c in ["R", "-", " "]): 
        return 0
    notes = {'C': 0, 'C#': 1, 'D': 2, 'D#': 3, 'E': 4, 'F': 5, 
             'F#': 6, 'G': 7, 'G#': 8, 'A': 9, 'A#': 10, 'B': 11}
    try:
        name = note_str.split(':')[0].upper()
        octave = int(name[-1]) if name[-1].isdigit() else 4
        note_name = name[:-1] if name[-1].isdigit() else name
        n = notes[note_name] + (octave + 1) * 12
        return 440 * (2 ** ((n - 69) / 12))
    except: return 0

def parse_score(score_string):
    raw_notes = score_string.split()
    processed_notes = []
    current_time = 0
    for item in raw_notes:
        note_part, duration = item.split(':') if ":" in item else (item, 4)
        processed_notes.append({'start': current_time, 'note': note_part, 'duration': int(duration)})
        current_time += int(duration)
    return processed_notes, current_time

def generate_tone(frequency, duration_beats, volume=0.12): 
    if frequency <= 0: return None
    duration_ms = (duration_beats / 4) * (60000 / METADATA["BPM"])
    sample_rate = 44100
    n_samples = int(sample_rate * (duration_ms / 1000.0))
    samples = array.array('h')
    
    for i in range(n_samples):
        t = float(i) / sample_rate
        val = math.sin(2.0 * math.pi * frequency * t)
        attack, decay = int(n_samples * 0.05), int(n_samples * 0.2)
        if i < attack: val *= (i / attack)
        elif i > (n_samples - decay): val *= ((n_samples - i) / decay)
        samples.append(int(32767 * val * volume))
    return pygame.mixer.Sound(buffer=samples)

def virtual_microbit(name, score_string):
    global RADIO_BUS, RUNNING, current_status
    score, _ = parse_score(score_string)
    last_tick = -1
    while RUNNING:
        if RADIO_BUS != last_tick:
            tick = RADIO_BUS
            last_tick = tick
            for note_data in score:
                if note_data['start'] == tick:
                    current_status[name] = note_data['note']
                    freq = note_to_freq(note_data['note'])
                    if freq > 0:
                        sound = generate_tone(freq, note_data['duration'])
                        if sound: sound.play()
        time.sleep(0.001)

def conductor(total_ticks):
    global RADIO_BUS, RUNNING, current_status
    os.system('') 
    
    bpm = METADATA["BPM"]
    seconds_per_tick = (60 / bpm) / 4
    
    total_duration_sec = total_ticks * seconds_per_tick
    total_min = int(total_duration_sec // 60)
    total_sec = int(total_duration_sec % 60)
    total_time_str = f"{total_min}:{total_sec:02}"
    
    VOICES_PER_ROW = 8
    num_voices = len(current_status)
    rows_needed = math.ceil(num_voices / VOICES_PER_ROW)
    total_dashboard_lines = rows_needed + 5
    
    print("\n" * total_dashboard_lines) 
    
    for t in range(total_ticks + 1):
        if not RUNNING: break
        RADIO_BUS = t
        
        # Calculate current time
        current_duration_sec = t * seconds_per_tick
        cur_min = int(current_duration_sec // 60)
        cur_sec = int(current_duration_sec % 60)
        time_display = f"{cur_min}:{cur_sec:02} / {total_time_str}"
        
        measure = (t // METADATA["BPB"]) + 1
        
        sys.stdout.write("\033[F" * total_dashboard_lines)

        sys.stdout.write(f"🎵 {METADATA['AUTHOR']} - {METADATA['SONG']}\n")
        sys.stdout.write(f"TICK: {t:04} | MEASURE: {measure:02}\n\n")
        
        voice_keys = sorted(current_status.keys(), key=lambda x: int(''.join(filter(str.isdigit, x)) or 0))
        for i in range(0, len(voice_keys), VOICES_PER_ROW):
            row_slice = voice_keys[i:i+VOICES_PER_ROW]
            row_str = ""
            for name in row_slice:
                note = current_status[name]
                voice_num = "".join(filter(str.isdigit, name))
                short_id = f"V{voice_num}"
                
                row_str += f"[{short_id}] {note:<5} "
            sys.stdout.write(f"{row_str:<100}\n")
            
        bar_len = 40
        progress = t / total_ticks if total_ticks > 0 else 0
        filled = int(bar_len * progress)
        bar = "█" * filled + "░" * (bar_len - filled)
        sys.stdout.write(f"\n[{bar}] {time_display}\n")
        
        sys.stdout.flush()
        
        time.sleep(seconds_per_tick)
    
    RUNNING = False

if __name__ == "__main__":
    PARTS = load_score(SCORE_FILE)
    current_status = {name: "-" for name in PARTS.keys()}
    max_ticks = 0
    for name, score_str in PARTS.items():
        _, length = parse_score(score_str)
        max_ticks = max(max_ticks, length)
    
    for name, score_str in PARTS.items():
        threading.Thread(target=virtual_microbit, args=(name, score_str), daemon=True).start()
    
    try:
        conductor(max_ticks)
    except KeyboardInterrupt:
        RUNNING = False

    pygame.mixer.stop()
    pygame.quit()