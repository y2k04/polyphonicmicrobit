# Polyphonic micro:bit

### Features
 - Converts .mid files to a micro:bit melody compatible note array
 - Plays the score file by simulating the micro:bit networking protocol I made for playing music synchronously.

### To-do
 - Document the networking protocol
 - Rewrite the micro:bit code generation for public use (JavaScript version)
 - Rewrite playback and convert code to be easier to maintain
 - Force a hard limit of 1000 threads for the player (Lags heavily with Rush E, and causes a lot of memory and CPU usage -> <img height="25" src="https://github.com/user-attachments/assets/ea667167-6257-4fec-b286-5f25b2917a3d" />)
