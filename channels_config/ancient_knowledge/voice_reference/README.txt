ancient_knowledge — F5-TTS voice reference
==========================================

Drop the page-specific reference clip here (used when
audio production is remote_gpu / F5-TTS). Each channel has its own
folder; do not reuse another page's sample.

Expected files
--------------
  ancient_knowledge_voice_ref_10s.wav   (preferred)
  OR ancient_knowledge_voice_ref_10s.mp3
      ~10s mono reference cut — LoadAudio accepts wav/mp3/flac/ogg.

  ancient_knowledge_voice_ref_10s.txt
      Exact transcript of that clip (F5-TTS sample_text). Must match
      the spoken words in the audio file.

Optional page_config.py overrides
---------------------------------
  VOICE_REFERENCE_AUDIO = "ancient_knowledge_voice_ref_10s.mp3"
  VOICE_REFERENCE_TEXT  = "ancient_knowledge_voice_ref_10s.txt"

Resolution order (RemoteGPUManager)
-----------------------------------
  1. Page voice_reference/ (this folder) — .wav / .mp3 / .flac / .ogg
  2. Global REMOTE_GPU_DEFAULT_REF_AUDIO only if it is a **local file**
     that exists (bare server names like sample_10s.wav are rejected —
     they waste GPU on LoadAudio validation failures).

Do not run production F5 jobs for this page until a local .wav/.mp3
and matching .txt are present.
