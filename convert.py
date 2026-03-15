import mido
import math
import os

# Configuration
MIDI_INPUT = input('MIDI file path: ').replace("\\", "/")
OUTPUT_FILE = "scores/" + input("Output filename (no .txt): ") + ".txt"
SONG_NAME = input("Song Name: ")
AUTHOR = input("Author: ")

NOTE_MAP = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
TICKS_PER_BEAT = 4  # This defines a "tick" as a 16th note

def midi_to_score():
    if not os.path.exists(os.path.dirname(OUTPUT_FILE)):
        os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)

    try:
        mid = mido.MidiFile(MIDI_INPUT)
    except Exception as e:
        print(f"Error loading MIDI: {e}")
        return

    # 1. SETUP TEMPO & SCALE
    # Scale translates MIDI internal ticks to our Engine Ticks (16th notes)
    scale = mid.ticks_per_beat / TICKS_PER_BEAT
    tempo_events = [] # [(tick, bpm), ...]

    for track in mid.tracks:
        abs_tick = 0
        for msg in track:
            abs_tick += msg.time
            if msg.type == 'set_tempo':
                # Map the tempo change to our engine's tick grid
                engine_tick = round(abs_tick / scale)
                tempo_events.append((engine_tick, round(mido.tempo2bpm(msg.tempo))))

    tempo_events.sort()

    # Remove duplicates/conflicts at the same tick
    unique_tempos = []
    if tempo_events:
        unique_tempos.append(tempo_events[0])
        for i in range(1, len(tempo_events)):
            if tempo_events[i][0] != tempo_events[i-1][0]:
                unique_tempos.append(tempo_events[i])

    initial_bpm = unique_tempos[0][1] if unique_tempos else 120

    # 2. PROCESS NOTES
    all_notes = []
    active_notes = {}

    for track in mid.tracks:
        absolute_tick = 0
        for msg in track:
            absolute_tick += msg.time
            engine_tick = int(round(absolute_tick / scale))

            if msg.type == 'note_on' and msg.velocity > 0:
                # Store the start tick
                active_notes[msg.note] = engine_tick
            elif (msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0)):
                if msg.note in active_notes:
                    start_tick = active_notes[msg.note]
                    # Ensure duration is at least 1 engine tick
                    duration = max(1, engine_tick - start_tick)

                    octave = (msg.note // 12) - 1
                    note_name = NOTE_MAP[msg.note % 12] + str(octave)

                    all_notes.append({
                        'start': start_tick,
                        'end': start_tick + duration,
                        'name': note_name,
                        'duration': duration,
                        'pitch': msg.note
                    })
                    del active_notes[msg.note]

    # Sort notes by start time, then by pitch (highest notes first)
    all_notes.sort(key=lambda x: (x['start'], -x['pitch']))

    # 3. VOICE ALLOCATION (Polyphony Handling)
    voices_end_time = {} # Track when each voice becomes "free"
    voice_data = {}
    max_song_tick = 0

    for note in all_notes:
        max_song_tick = max(max_song_tick, note['end'])
        assigned = False

        # Look for an existing voice that finished before this note starts
        for v_idx in sorted(voices_end_time.keys()):
            if voices_end_time[v_idx] <= note['start']:
                gap = note['start'] - voices_end_time[v_idx]

                if gap > 0:
                    voice_data[v_idx].append(f"-:{gap}")

                voice_data[v_idx].append(f"{note['name']}:{note['duration']}")
                voices_end_time[v_idx] = note['end']
                assigned = True
                break

        # If no voice is free, create a new one
        if not assigned:
            new_idx = len(voices_end_time) + 1
            voice_data[new_idx] = []
            if note['start'] > 0:
                voice_data[new_idx].append(f"-:{note['start']}")

            voice_data[new_idx].append(f"{note['name']}:{note['duration']}")
            voices_end_time[new_idx] = note['end']

    # 4. FINAL ALIGNMENT
    # Ensure all voices have a rest at the end so they match the total song length
    for v_idx in voice_data:
        if voices_end_time[v_idx] < max_song_tick:
            final_gap = max_song_tick - voices_end_time[v_idx]
            voice_data[v_idx].append(f"-:{final_gap}")

    # 5. WRITE SCORE FILE
    with open(OUTPUT_FILE, "w", encoding='utf-8') as f:
        # Header
        f.write(f"SONG: {SONG_NAME}\nAUTHOR: {AUTHOR}\nBPM: {initial_bpm}\n\n")

        # Conductor Block (Always included for timing stability)
        f.write("[CONDUCTOR]\n")
        if not unique_tempos:
            f.write(f"{initial_bpm}:{max(4, max_song_tick)}")
        else:
            for i in range(len(unique_tempos)):
                curr_tick, curr_bpm = unique_tempos[i]
                if i < len(unique_tempos) - 1:
                    # Duration is the distance to the next tempo event
                    duration = unique_tempos[i+1][0] - curr_tick
                else:
                    # Final tempo lasts until the end of the song
                    duration = max(1, max_song_tick - curr_tick)

                if duration > 0:
                    f.write(f"{curr_bpm}:{duration} ")
        f.write("\n\n")

        # Voice Blocks
        for v_idx in sorted(voice_data.keys()):
            f.write(f"[Voice{v_idx}]\n")
            notes_on_line = 0
            for note_str in voice_data[v_idx]:
                f.write(f"{note_str} ")
                notes_on_line += 1
                if notes_on_line >= 10: # Format for readability
                    f.write("\n")
                    notes_on_line = 0
            f.write("\n\n")

    print(f"--- CONVERSION COMPLETE ---")
    print(f"Output: {OUTPUT_FILE}")
    print(f"Voices: {len(voice_data)}")
    print(f"Length: {max_song_tick} ticks")

if __name__ == "__main__":
    midi_to_score()
