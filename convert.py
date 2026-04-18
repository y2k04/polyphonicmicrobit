import mido, os

MIDI_INPUT = input('MIDI file path: ').replace("\\", "/")
OUTPUT_FILE = "scores/" + input("Output filename: ") + ".txt"
SONG_NAME = input("Song Name: ")
AUTHOR = input("Author: ")

NOTE_MAP = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']

def midi_to_score():
    if not os.path.exists(os.path.dirname(OUTPUT_FILE)): os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    try: mid = mido.MidiFile(MIDI_INPUT)
    except Exception as e: return print(f"Error: {e}")

    ticks_per_beat = mid.ticks_per_beat
    tempo_events = []
    for track in mid.tracks:
        abs_t = 0
        for msg in track:
            abs_t += msg.time
            if msg.type == 'set_tempo':
                tempo_events.append((abs_t, mido.tempo2bpm(msg.tempo)))

    tempo_events.sort()
    unique_tempos = [tempo_events[0]] if tempo_events else []
    for i in range(1, len(tempo_events)):
        if tempo_events[i][0] != tempo_events[i-1][0]: unique_tempos.append(tempo_events[i])

    initial_bpm = unique_tempos[0][1] if unique_tempos else 120.0
    all_notes, active_notes = [], {}

    for track_idx, track in enumerate(mid.tracks):
        is_drum_track = track_idx == 0 or any(msg.channel == 9 for msg in track if msg.type == 'note_on')  # Assume first track or has notes on channel 9
        abs_t = 0
        for msg in track:
            abs_t += msg.time
            eng_t = abs_t
            if msg.type == 'note_on' and msg.velocity > 0:
                active_notes[(msg.note, track_idx)] = (eng_t, msg.channel == 9)
            elif msg.type == 'note_off' or (msg.type == 'note_on' and msg.velocity == 0):
                if (msg.note, track_idx) in active_notes:
                    start, is_drum = active_notes[(msg.note, track_idx)]
                    dur = max(1, eng_t - start)
                    name = NOTE_MAP[msg.note % 12] + str((msg.note // 12) - 1) if not is_drum else f"P{msg.note}"
                    all_notes.append({'start': start, 'end': start + dur, 'name': name, 'dur': dur, 'pitch': msg.note, 'is_drum': is_drum})
                    del active_notes[(msg.note, track_idx)]

    all_notes.sort(key=lambda x: (x['start'], -x['pitch']))
    v_end, v_data, max_tick = {}, {}, 0

    for n in all_notes:
        max_tick = max(max_tick, n['end'])
        assigned = False
        for v_idx in sorted(v_end.keys()):
            if v_end[v_idx] <= n['start']:
                gap = n['start'] - v_end[v_idx]
                if gap > 0: v_data[v_idx].append(f"-:{gap}")
                v_data[v_idx].append(f"{n['name']}:{n['dur']}")
                v_end[v_idx] = n['end']
                assigned = True
                break
        if not assigned:
            idx = len(v_end) + 1
            v_data[idx] = ([f"-:{n['start']}"] if n['start'] > 0 else []) + [f"{n['name']}:{n['dur']}"]
            v_end[idx] = n['end']

    for idx in v_data:
        if v_end[idx] < max_tick: v_data[idx].append(f"-:{max_tick - v_end[idx]}")

    with open(OUTPUT_FILE, "w", encoding='utf-8') as f:
        f.write(f"SONG: {SONG_NAME}\nAUTHOR: {AUTHOR}\nBPM: {initial_bpm}\nTPB: {ticks_per_beat}\n\n[CONDUCTOR]\n")
        if not unique_tempos: f.write(f"{initial_bpm}:{max(1, max_tick)}")
        else:
            for i in range(len(unique_tempos)):
                curr_t, curr_bpm = unique_tempos[i]
                dur = (unique_tempos[i+1][0] - curr_t) if i < len(unique_tempos)-1 else max(1, max_tick - curr_t)
                f.write(f"{curr_bpm}:{dur} ")

        for idx in sorted(v_data.keys()):
            f.write(f"\n\n[Voice{idx}]\n")
            for i, note in enumerate(v_data[idx]): f.write(f"{note} " + ("\n" if (i+1)%10==0 else ""))

    print(f"Done: {len(v_data)} voices, {max_tick} ticks.")

if __name__ == "__main__": midi_to_score()
