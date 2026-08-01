Optional: drop ambient_cinematic_pad.mp3 here.
If present, the reel pipeline prefers this local cinematic pad over ElevenLabs SFX.
Otherwise AMBIENT_SFX_PROMPT from page_config.py is sent to ElevenLabs sound-generation.

Target soundscape: Dark Atmospheric Synth Pad + Inspiring Cinematic Sub-Bass Drone.
DO NOT use rain / storm / white-noise loops — they cause hiss and clipping.
Legacy ambient_martial_loop.mp3 (rain) is intentionally ignored.

Bed volume: AMBIENT_VOLUME=0.16 (14–18%), ducked to ~55% while voiceover plays,
then restored for CTA/tail. All tracks force-resampled to 48000 Hz.
