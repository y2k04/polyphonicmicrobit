import os
import sys
import subprocess
import time
import math
import warnings
import argparse
from typing import Dict, Tuple, List, Any

# Third-party imports
try:
    import numpy as np
    import sounddevice as sd
except ImportError as e:
    print(f"Error importing required libraries: {e}. Please install dependencies (numpy, sounddevice).")
    sys.exit(1)

# Global Constants (Assuming standard defaults)
SAMPLE_RATE = 44100
CHANNELS = 2
VOLUME = 2

EQ_LOW = 1
EQ_MID = 1
EQ_HIGH = 1

ATTACK = 0.1
DECAY = 0.1

AUDIO_ACTIVE = False

class AudioPlayer:
    # Manages loading a score file and generating/playing the corresponding audio mix.
    NOTE_MAP = {'C': 0, 'C#': 1, 'D': 2, 'D#': 3, 'E': 4, 'F': 5, 'F#': 6, 'G': 7, 'G#': 8, 'A': 9, 'A#': 10, 'B': 11}
    DEFAULT_METADATA = {"SONG": "Unknown", "ARTIST": "Unknown", "BPM": 120.0, "TPB": 4}

    def __init__(self):
        self._metadata: Dict[str, Any] = self.DEFAULT_METADATA.copy()
        self.score_file_path: str = ""
        self.parts: Dict[str, str] = {}
        self.tempo_map: Dict[int, float] = {}
        self.frequency_ranges = {'LOW': self._note_to_frequency('G3'), 'HIGH': self._note_to_frequency('C6')}

    def load_score_file(self, filename: str) -> bool:
        """Loads metadata and structured parts from the specified score text file."""
        self.score_file_path = os.path.join("scores", filename + ".txt")
        if not os.path.exists(self.score_file_path):
            print(f"Error: {self.score_file_path} not found!")
            return False

        self._metadata = self.DEFAULT_METADATA.copy()
        self.parts = {}
        self.tempo_map = {}
        current_part_name: str | None = None

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
                    try:
                        if ":" not in item: continue
                        bpm_val_str, dur_str = item.split(':')
                        self.tempo_map[t_ptr] = float(bpm_val_str)
                        t_ptr += int(dur_str)
                    except ValueError as e:
                        print(f"Warning: Skipping malformed conductor entry '{item}'. Error: {e}")

            if 0 not in self.tempo_map and "BPM" in self._metadata:
                self.tempo_map[0] = float(self._metadata["BPM"])
        except IOError as e:
            print(f"Error reading score file: {e}")
            return False

        return True

    @staticmethod
    def _note_to_frequency(note_str: str) -> float:
        """Converts a musical note string (e.g., 'C4') to frequency in Hz."""
        import re
        match = re.findall(r'([A-G]#?)([0-9])', note_str.upper())
        if not match:
            return 0.0
        last_name, last_octave_str = match[-1]
        try:
            octave = int(last_octave_str)
            base_freq = AudioPlayer._get_midi_note_frequency(last_name, octave)
            return base_freq if base_freq > 0 else 0.0
        except Exception:
            return 0.0

    @staticmethod
    def _get_midi_note_frequency(base_name: str, octave: int) -> float:
        """Helper to calculate frequency from a robust name/octave pair."""
        if base_name in AudioPlayer.NOTE_MAP:
            midi_note = AudioPlayer.NOTE_MAP[base_name] + 12 * (octave + 1)
            return 440.0 * (2 ** ((midi_note - 69) / 12))
        return 0.0

    def _generate_tone(self, frequency: float, duration_seconds: float, pan: float = 0.0, note_vol: float = 1.0) -> np.ndarray | None:
        if frequency <= 0 or duration_seconds <= 0: return None

        total_samples = max(1, int(round(SAMPLE_RATE * duration_seconds)))
        t = np.linspace(0, duration_seconds, total_samples, False)

        # Base Wave & Bass Reinforcement
        val = np.sin(2 * np.pi * frequency * t)
        if frequency > self.frequency_ranges['HIGH']:
            val *= 0.6
            if frequency > self._note_to_frequency('C6'):
                #val = np.convolve(val, np.ones(10) / 10, mode='same')
                val *= 0.75
        elif frequency < self.frequency_ranges['LOW']:
            sub = 0.5 * np.sin(1 * np.pi * frequency * t)
            body = 0.3 * np.sin(4 * np.pi * frequency * t)
            val = np.tanh((val + sub + body) * 1.2)

        # Envelope
        attack_samples = int(total_samples * ATTACK)
        release_samples = min(int(SAMPLE_RATE * 0.05), total_samples // 2)
        envelope = np.ones(total_samples, dtype=np.float32)
        if attack_samples > 0: envelope[:attack_samples] = np.linspace(0, 1, attack_samples)
        if release_samples > 0: envelope[-release_samples:] = np.linspace(1, 0, release_samples)

        # Gain & Pan
        current_vol = VOLUME * note_vol * (EQ_LOW if frequency < self.frequency_ranges['LOW'] else EQ_HIGH if frequency > self.frequency_ranges['HIGH'] else EQ_MID)
        final_signal = VOLUME * np.tanh((val * envelope * current_vol) / 0.12) * 0.12

        left_gain = math.sqrt((1.0 - np.clip(-pan, -1.0, 0)) / 2.0)
        right_gain = math.sqrt((1.0 + np.clip(pan, -1.0, 0)) / 2.0)
        stereo = np.column_stack((final_signal * left_gain, final_signal * right_gain)).astype(np.float32)
        return stereo

    def _build_mix(self) -> tuple[np.ndarray, float]:
        """Calculates the full audio mix by iterating through all parts and notes."""
        tempo_ticks = sorted(self.tempo_map.keys())
        ticks_per_beat = int(self._metadata.get('TPB', 4))
        voice_count = len(self.parts)
        pan_positions = np.linspace(-0.7, 0.7, voice_count) if voice_count > 1 else [0.0]
        events: List[tuple[float, np.ndarray]] = []

        # Calculate overall notes/items to build a smooth global progress indicator
        total_items = sum(len(score_string.split()) for score_string in self.parts.values())
        items_processed = 0

        for voice_idx, part_name in enumerate(self.parts.keys()):
            pan = pan_positions[voice_idx]
            current_time = 0.0
            tick_ptr = 0
            score_string = self.parts[part_name]

            for item in score_string.split():
                items_processed += 1
                if total_items > 0:
                    progress = int(30 * (items_processed / total_items))
                    bar = "█" * progress + "░" * (30 - progress)
                    percentage = int(100 * items_processed / total_items)
                    sys.stdout.write(f"\r Building Mix:  [{bar}] {percentage}% ({part_name}) \033[K")
                    sys.stdout.flush()

                if ":" not in item: continue
                try:
                    parts = item.split(':')
                    if len(parts) == 3:
                        note, duration_ticks_str, volume_str = parts
                        volume_val = int(volume_str) / 255.0
                    else:
                        note, duration_ticks_str = parts
                        volume_val = 1.0

                    duration_ticks = int(duration_ticks_str)
                except ValueError:
                    continue

                total_seconds_for_note = 0.0
                for i in range(duration_ticks):
                    time_check_ptr = tick_ptr + i
                    active_bpm = self._metadata['BPM']
                    for t in reversed(tempo_ticks):
                        if t <= time_check_ptr:
                            active_bpm = self.tempo_map[t]
                            break

                    if active_bpm > 0:
                        time_for_one_tick = (60.0 / active_bpm) / ticks_per_beat
                        total_seconds_for_note += time_for_one_tick

                hz = self._note_to_frequency(note)
                if hz > 0:
                    samples = self._generate_tone(hz, total_seconds_for_note, pan=pan, note_vol=volume_val)
                    if samples is not None:
                        events.append((current_time, samples))

                current_time += total_seconds_for_note
                tick_ptr += duration_ticks

        print()  # Add a clean newline after building completes
        if not events: return np.zeros((1, CHANNELS), dtype=np.float32), 0.0

        max_time = max(e[0] + len(e[1])/SAMPLE_RATE for e in events)
        total_samples = int(math.ceil(max_time * SAMPLE_RATE))
        mix = np.zeros((total_samples, CHANNELS), dtype=np.float32)

        for start_time, samples in events:
            start_idx = int(round(start_time * SAMPLE_RATE))
            end_idx = start_idx + len(samples)
            if start_idx < total_samples:
                actual_end = min(end_idx, total_samples)
                mix[start_idx:actual_end] += samples[:actual_end - start_idx]

        return np.clip(mix, -1.0, 1.0), max_time

    def run_conductor_ui(self, mixed_buffer, total_seconds):
        start_time = time.perf_counter()
        sd.play(mixed_buffer, SAMPLE_RATE)
        try:
            subprocess.call('cls' if os.name == 'nt' else 'clear', shell=True)

            print(f"\n 🎵 {self._metadata['SONG']}")
            print(f"    {self._metadata['ARTIST']}\n")

            while True:
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
            print("\nPlayback finished.")
        except KeyboardInterrupt:
            print("\n\nPlayback interrupted by user.")
        finally:
            sd.stop()

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
