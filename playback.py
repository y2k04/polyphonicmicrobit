import os
import sys
import subprocess
import time
import math
import re
import warnings
from typing import Dict, Tuple, List, Any
from functools import lru_cache

import select
import tty
import termios
import argparse

try:
    import numpy as np
    import sounddevice as sd
except ImportError as e:
    print(f"Error importing required libraries: {e}. Please install dependencies (numpy, sounddevice).")
    sys.exit(1)

SAMPLE_RATE = 44100
CHANNELS = 2
VOLUME = 2.0

EQ_LOW = 1.0
EQ_MID = 1.0
EQ_HIGH = 1.0

ATTACK = 0.1

class AudioPlayer:
    NOTE_MAP = {'C': 0, 'C#': 1, 'D': 2, 'D#': 3, 'E': 4, 'F': 5, 'F#': 6, 'G': 7, 'G#': 8, 'A': 9, 'A#': 10, 'B': 11}
    DEFAULT_METADATA = {"SONG": "Unknown", "ARTIST": "Unknown", "BPM": 120.0, "TPB": 4}

    def __init__(self):
        self._metadata: Dict[str, Any] = self.DEFAULT_METADATA.copy()
        self.score_file_path: str = ""
        self.parts: Dict[str, str] = {}
        self.tempo_map: Dict[int, float] = {}

        self.freq_low = self._note_to_frequency('G3')
        self.freq_high = self._note_to_frequency('C5')
        self.freq_c6 = self._note_to_frequency('C6')

        self.is_paused = False

    @staticmethod
    @lru_cache(maxsize=256)
    def _note_to_frequency(note_str: str) -> float:
        match = re.findall(r'([A-G]#?)([0-9])', note_str.upper())
        if not match:
            return 0.0
        last_name, last_octave_str = match[-1]
        if last_name in AudioPlayer.NOTE_MAP:
            midi_note = AudioPlayer.NOTE_MAP[last_name] + 12 * (int(last_octave_str) + 1)
            return 440.0 * (2 ** ((midi_note - 69) / 12.0))
        return 0.0

    @lru_cache(maxsize=1024)
    def _generate_tone_cached(self, frequency: float, total_samples: int, pan: float, note_vol: float) -> np.ndarray:
        duration_seconds = total_samples / SAMPLE_RATE
        t = np.linspace(0, duration_seconds, total_samples, endpoint=False, dtype=np.float32)

        val = np.sin(2 * np.pi * frequency * t, dtype=np.float32)
        if frequency > self.freq_high:
            val *= 0.6
            if frequency > self.freq_c6:
                val *= 0.75
        elif frequency < self.freq_low:
            sub = 0.5 * np.sin(1 * np.pi * frequency * t, dtype=np.float32)
            body = 0.3 * np.sin(4 * np.pi * frequency * t, dtype=np.float32)
            val = np.tanh((val + sub + body) * 1.2)

        attack_samples = int(total_samples * ATTACK)
        release_samples = min(int(SAMPLE_RATE * 0.05), total_samples // 2)

        envelope = np.ones(total_samples, dtype=np.float32)
        if attack_samples > 0:
            envelope[:attack_samples] = np.linspace(0, 1, attack_samples, dtype=np.float32)
        if release_samples > 0:
            envelope[-release_samples:] = np.linspace(1, 0, release_samples, dtype=np.float32)

        eq_gain = EQ_LOW if frequency < self.freq_low else (EQ_HIGH if frequency > self.freq_high else EQ_MID)
        current_vol = VOLUME * note_vol * eq_gain
        final_signal = VOLUME * np.tanh((val * envelope * current_vol) / 0.12) * 0.12

        left_gain = math.sqrt((1.0 - np.clip(-pan, -1.0, 0.0)) / 2.0)
        right_gain = math.sqrt((1.0 + np.clip(pan, -1.0, 0.0)) / 2.0)

        gains = np.array([left_gain, right_gain], dtype=np.float32)
        return final_signal[:, None] * gains

    def load_score_file(self, filename: str) -> bool:
        self.score_file_path = os.path.join("scores", filename + ".txt")
        if not os.path.exists(self.score_file_path):
            print(f"Error: {self.score_file_path} not found!")
            return False

        self._metadata = self.DEFAULT_METADATA.copy()
        self.parts = {}
        self.tempo_map = {}
        current_part_name = None

        try:
            with open(self.score_file_path, 'r', encoding='utf-8') as score:
                for line in score:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue

                    if ":" in line and not line.startswith('[') and current_part_name is None:
                        key, value = line.split(':', 1)
                        key = key.strip().upper()
                        if key in self.DEFAULT_METADATA:
                            self._metadata[key] = value.strip()
                        continue

                    if line.startswith('[') and line.endswith(']'):
                        current_part_name = line[1:-1]
                        self.parts[current_part_name] = ""
                    elif current_part_name:
                        self.parts[current_part_name] += line + " "

            if "CONDUCTOR" in self.parts:
                raw_conductor = self.parts.pop("CONDUCTOR")
                t_ptr = 0
                for item in raw_conductor.split():
                    if ":" not in item: continue
                    try:
                        bpm_val_str, dur_str = item.split(':')
                        self.tempo_map[t_ptr] = float(bpm_val_str)
                        t_ptr += int(dur_str)
                    except ValueError:
                        continue

            if 0 not in self.tempo_map and "BPM" in self._metadata:
                self.tempo_map[0] = float(self._metadata["BPM"])
        except IOError as e:
            print(f"Error reading score file: {e}")
            return False

        return True

    def _build_mix(self) -> tuple[np.ndarray, float]:
        tempo_ticks = np.array(sorted(self.tempo_map.keys()), dtype=np.int32)
        tempo_bpms = np.array([self.tempo_map[t] for t in tempo_ticks], dtype=np.float64)
        ticks_per_beat = int(self._metadata.get('TPB', 4))

        voice_count = len(self.parts)
        pan_positions = np.linspace(-0.7, 0.7, voice_count) if voice_count > 1 else [0.0]

        max_duration_ticks = 0
        parsed_parts = {}
        total_items = 0

        for voice_idx, (part_name, score_string) in enumerate(self.parts.items()):
            tokens = score_string.split()
            total_items += len(tokens)
            parsed_tokens = []
            part_tick = 0
            for item in tokens:
                if ":" not in item: continue
                parts = item.split(':')
                if len(parts) == 3:
                    note, dur_str, vol_str = parts
                    vol = int(vol_str) / 255.0
                else:
                    note, dur_str = parts
                    vol = 1.0
                dur = int(dur_str)
                parsed_tokens.append((note, dur, vol, part_tick))
                part_tick += dur
            parsed_parts[part_name] = parsed_tokens
            if part_tick > max_duration_ticks:
                max_duration_ticks = part_tick

        tick_indices = np.arange(max_duration_ticks + 1)
        tempo_indices = np.searchsorted(tempo_ticks, tick_indices, side='right') - 1
        active_bpms = tempo_bpms[tempo_indices]
        tick_durations = (60.0 / active_bpms) / ticks_per_beat
        tick_times = np.zeros(max_duration_ticks + 1, dtype=np.float64)
        np.cumsum(tick_durations[:-1], out=tick_times[1:])

        total_seconds = tick_times[-1] + 1.0  # Buffer tail
        total_samples = int(math.ceil(total_seconds * SAMPLE_RATE))
        mix = np.zeros((total_samples, CHANNELS), dtype=np.float32)

        items_processed = 0

        for voice_idx, (part_name, tokens) in enumerate(parsed_parts.items()):
            pan = pan_positions[voice_idx]

            for note, dur, vol, start_tick in tokens:
                items_processed += 1
                if total_items > 0 and items_processed % 10 == 0:
                    progress = int(30 * (items_processed / total_items))
                    bar = "█" * progress + "░" * (30 - progress)
                    percentage = int(100 * items_processed / total_items)
                    sys.stdout.write(f"\r Building Mix:  [{bar}] {percentage}% ({part_name}) \033[K")
                    sys.stdout.flush()

                hz = self._note_to_frequency(note)
                if hz <= 0:
                    continue

                note_start_sec = tick_times[start_tick]
                note_end_sec = tick_times[start_tick + dur]
                duration_sec = note_end_sec - note_start_sec

                note_samples_cnt = max(1, int(round(SAMPLE_RATE * duration_sec)))
                tone = self._generate_tone_cached(hz, note_samples_cnt, round(pan, 3), round(vol, 3))

                start_idx = int(round(note_start_sec * SAMPLE_RATE))
                end_idx = start_idx + len(tone)

                if start_idx < total_samples:
                    actual_end = min(end_idx, total_samples)
                    mix[start_idx:actual_end] += tone[:actual_end - start_idx]

        print()
        actual_max_time = tick_times[max_duration_ticks]
        return np.clip(mix, -1.0, 1.0), actual_max_time

    def run_conductor_ui(self, mixed_buffer, total_seconds):
        self.is_paused = True

        old_settings = None
        if os.name != 'nt' and sys.stdin.isatty():
            old_settings = termios.tcgetattr(sys.stdin)
            tty.setcbreak(sys.stdin.fileno())

        sys.stdout.write("\033[?25l")
        sys.stdout.flush()

        sd.play(mixed_buffer, SAMPLE_RATE)
        start_time = time.perf_counter()
        accumulated_pause_duration = 0.0
        pause_start_time = 0.0
        currently_audio_paused = False

        try:
            subprocess.call('cls' if os.name == 'nt' else 'clear', shell=True)
            print(f"\n 🎵 {self._metadata['SONG']}")
            print(f"    {self._metadata['ARTIST']}\n")

            while True:
                if select.select([sys.stdin], [], [], 0)[0]:
                    ch = sys.stdin.read(1)
                    if ch == ' ':
                        self.is_paused = not self.is_paused

                if self.is_paused and not currently_audio_paused:
                    sd.stop()
                    pause_start_time = time.perf_counter()
                    currently_audio_paused = True

                elif not self.is_paused and currently_audio_paused:
                    pause_delta = time.perf_counter() - pause_start_time
                    accumulated_pause_duration += pause_delta
                    elapsed = time.perf_counter() - start_time - accumulated_pause_duration
                    resume_sample = int(round(elapsed * SAMPLE_RATE))

                    if resume_sample < len(mixed_buffer):
                        sd.play(mixed_buffer[resume_sample:], SAMPLE_RATE)
                    currently_audio_paused = False

                if currently_audio_paused:
                    elapsed = pause_start_time - start_time - accumulated_pause_duration
                else:
                    elapsed = time.perf_counter() - start_time - accumulated_pause_duration

                if elapsed >= total_seconds:
                    break

                progress = int(30 * min(max(0.0, elapsed) / total_seconds, 1.0))
                bar = "█" * progress + "░" * (30 - progress)
                cur_time = f"{int(max(0, elapsed) // 60)}:{int(max(0, elapsed) % 60):02}"
                total_time = f"{int(total_seconds // 60)}:{int(total_seconds % 60):02}"

                status_str = "[Press SPACE to resume]" if currently_audio_paused else ""

                progress_line = f"\r [{bar}] {cur_time} / {total_time}\033[K"
                status_line = f"\r{status_str}\033[K"
                sys.stdout.write(f"{progress_line}\n{status_line}\033[1A")
                sys.stdout.flush()

                time.sleep(0.05)

            print("\nPlayback finished.")
        except KeyboardInterrupt:
            print("\n\nPlayback interrupted by user.")
        finally:
            sd.stop()
            sys.stdout.write("\033[?25h\033[0 q\n")
            sys.stdout.flush()
            if old_settings:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

    def play_score(self):
        if not self.parts:
            print("Error: Score parts are empty. Run load_score_file first.")
            return
        mixed_buffer, total_seconds = self._build_mix()
        self.run_conductor_ui(mixed_buffer, total_seconds)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Play a structured score file.")
    parser.add_argument("score_file", help="The structured score text file.")
    args = parser.parse_args()

    player = AudioPlayer()
    if player.load_score_file(args.score_file):
        player.play_score()
