import mido
import math
import os

MIDI_INPUT = input('MIDI file: ').replace("\\","/")
OUTPUT_FILE = "scores/" + input("Output filename: ") + ".txt"
SONG_NAME = input("Song Name: ")
AUTHOR = input("Author: ")
TICKS_PER_BEAT = 4

def midi_to_score():
    if not os.path.exists(os.path.dirname(OUTPUT_FILE)):
        os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)

    try:
        mid = mido.MidiFile(MIDI_INPUT)
    except Exception as e:
        print(f"Error loading MIDI: {e}")
        return

    bpm = 120
    # Search for tempo in all tracks
    for track in mid.tracks:
        for msg in track:
            if msg.type == 'set_tempo':
                bpm = round(mido.tempo2bpm(msg.tempo))
                break

    scale = mid.ticks_per_beat / TICKS_PER_BEAT
    all_notes = []
    active_notes = {} 

    for i, track in enumerate(mid.tracks):
        absolute_tick = 0

        # Ensure the track is actually a list of messages
        if not hasattr(track, '__iter__'):
            continue

        for msg in track:
            absolute_tick += msg.time
            engine_tick = round(absolute_tick / scale)

            if msg.type == 'note_on' and msg.velocity > 0:
                active_notes[msg.note] = engine_tick
            elif (msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0)):
                if msg.note in active_notes:
                    start_tick = active_notes[msg.note]
                    duration = max(1, engine_tick - start_tick)
                    
                    names = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
                    octave = (msg.note // 12) - 1
                    note_name = names[msg.note % 12] + str(octave)
                    
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

    for note in all_notes:
        assigned = False
        for v_idx in sorted(voices_end_time.keys()):
            if voices_end_time[v_idx] <= note['start']:
                gap = note['start'] - voices_end_time[v_idx]
                if gap > 0:
                    voice_data[v_idx].append(f"-:{gap}")
                voice_data[v_idx].append(f"{note['name']}:{note['duration']}")
                voices_end_time[v_idx] = note['end']
                assigned = True
                break
        
        if not assigned:
            new_idx = len(voices_end_time) + 1
            voice_data[new_idx] = []
            if note['start'] > 0:
                voice_data[new_idx].append(f"-:{note['start']}")
            voice_data[new_idx].append(f"{note['name']}:{note['duration']}")
            voices_end_time[new_idx] = note['end']

    with open(OUTPUT_FILE, "w", encoding='utf-8') as f:
        f.write(f"SONG: {SONG_NAME}\n")
        f.write(f"AUTHOR: {AUTHOR}\n")
        f.write(f"BPM: {bpm}\n\n")
        
        for v_idx in sorted(voice_data.keys()):
            f.write(f"[Voice{v_idx}]\n")
            line_len = 0
            for note_str in voice_data[v_idx]:
                f.write(note_str + " ")
                line_len += 1
                if line_len >= 8:
                    f.write("\n")
                    line_len = 0
            f.write("\n\n")

    print(f"Converted: {OUTPUT_FILE} ({len(voices_end_time)} voices)")

if __name__ == "__main__":
    midi_to_score()