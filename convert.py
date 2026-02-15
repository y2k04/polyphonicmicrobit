import mido
import math
import os

MIDI_INPUT = input('MIDI file: ').replace("\\","/")
OUTPUT_FILE = "scores/" + input("Output filename: ") + ".txt"
SONG_NAME = input("Song Name: ")
AUTHOR = input("Author: ")

NOTE_MAP = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
TICKS_PER_BEAT = 4

def midi_to_score():
    if not os.path.exists(os.path.dirname(OUTPUT_FILE)):
        os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)

    try:
        mid = mido.MidiFile(MIDI_INPUT)
    except Exception as e:
        print(f"Error loading MIDI: {e}")
        return

    # Setup Tempo Map
    scale = mid.ticks_per_beat / TICKS_PER_BEAT
    tempo_events = [] # [(tick, bpm), ...]
    
    for track in mid.tracks:
        abs_tick = 0
        for msg in track:
            abs_tick += msg.time
            if msg.type == 'set_tempo':
                tempo_events.append((round(abs_tick / scale), round(mido.tempo2bpm(msg.tempo))))

    # Sort and remove duplicates at the same tick
    tempo_events.sort()
    unique_tempos = []
    if tempo_events:
        unique_tempos.append(tempo_events[0])
        for i in range(1, len(tempo_events)):
            if tempo_events[i][0] != tempo_events[i-1][0]:
                unique_tempos.append(tempo_events[i])

    initial_bpm = unique_tempos[0][1] if unique_tempos else 120

    # Note processing
    all_notes = []
    active_notes = {} 

    for track in mid.tracks:
        absolute_tick = 0
        if not hasattr(track, '__iter__'): continue

        for msg in track:
            absolute_tick += msg.time
            # Round to the nearest tick to prevent micro-shifts
            engine_tick = int(round(absolute_tick / scale))

            if msg.type == 'note_on' and msg.velocity > 0:
                active_notes[msg.note] = engine_tick
            elif (msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0)):
                if msg.note in active_notes:
                    start_tick = active_notes[msg.note]
                    # Ensure duration is at least 1 tick
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

    all_notes.sort(key=lambda x: (x['start'], -x['pitch']))

    voices_end_time = {}
    voice_data = {} 

    # Calculate number of voices and distribute note events
    for note in all_notes:
        assigned = False
        # Try to find a voice that is free at note['start']
        for v_idx in sorted(voices_end_time.keys()):
            if voices_end_time[v_idx] <= note['start']:
                gap = note['start'] - voices_end_time[v_idx]
                
                # Only add a rest if there is a gap of 1 tick or more
                if gap > 0: 
                    voice_data[v_idx].append(f"-:{gap}")

                voice_data[v_idx].append(f"{note['name']}:{note['duration']}")
                # Set the next available time for this voice to the end of this note
                voices_end_time[v_idx] = note['end']
                assigned = True
                break
        
        if not assigned:
            new_idx = len(voices_end_time) + 1
            voice_data[new_idx] = []
            # Start the new voice with a rest if the note doesn't start at 0
            if note['start'] > 0: 
                voice_data[new_idx].append(f"-:{note['start']}")

            voice_data[new_idx].append(f"{note['name']}:{note['duration']}")
            voices_end_time[new_idx] = note['end']

    # Write file to scores folder
    with open(OUTPUT_FILE, "w", encoding='utf-8') as f:
        f.write(f"SONG: {SONG_NAME}\nAUTHOR: {AUTHOR}\nBPM: {initial_bpm}\n\n")
        
        # Only write CONDUCTOR section if there are actually tempo changes
        if len(unique_tempos) > 1:
            f.write("[CONDUCTOR]\n")
            for i in range(len(unique_tempos)):
                curr_tick, curr_bpm = unique_tempos[i]

                # Calculate duration until next tempo change
                duration = (unique_tempos[i+1][0] - curr_tick) if i < len(unique_tempos) - 1 else 4
                
                f.write(f"{curr_bpm}:{duration} ")
            f.write("\n\n")

        for v_idx in sorted(voice_data.keys()):
            f.write(f"[Voice{v_idx}]\n")
            line_len = 0

            for note_str in voice_data[v_idx]:
                f.write(f"{note_str} ")
                line_len += 1
                if line_len >= 8:
                    f.write("\n")
                    line_len = 0
            f.write("\n\n")

    print(f"Converted: {OUTPUT_FILE} ({len(voices_end_time)} voices)")

if __name__ == "__main__":
    midi_to_score()