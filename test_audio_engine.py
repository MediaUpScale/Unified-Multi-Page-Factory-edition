import os
import sys
import argparse
import random
import numpy as np

# --- MOVIEPY 1.x / 2.x COMPATIBILITY LAYER ---
try:
    from moviepy.editor import AudioFileClip, CompositeAudioClip, concatenate_audioclips
    from moviepy.audio.AudioClip import AudioArrayClip
except (ImportError, ModuleNotFoundError):
    from moviepy.audio.io.AudioFileClip import AudioFileClip
    from moviepy.audio.AudioClip import CompositeAudioClip, concatenate_audioclips, AudioArrayClip


def generate_dummy_voiceover(target_path: str, duration: float = 10.0):
    """
    Auto-synthesizes a test voiceover audio file inside the audio_tests directory if no real VO exists.
    """
    fps = 44100
    t = np.linspace(0, duration, int(fps * duration), False)
    tone = 0.08 * np.sin(2 * np.pi * 440 * t)
    audio_array = np.column_stack((tone, tone))

    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    clip = AudioArrayClip(audio_array, fps=fps)
    clip.write_audiofile(target_path, fps=fps)
    print(f"[VO] Auto-synthesized test voiceover artifact: {target_path} ({duration:.1f}s)")


def safe_volume(clip, factor: float):
    if hasattr(clip, "with_volume_scaling"):
        return clip.with_volume_scaling(factor)
    elif hasattr(clip, "volumex"):
        return clip.volumex(factor)
    else:
        try:
            from moviepy.audio.fx import MultiplyVolume
            return clip.with_effects([MultiplyVolume(factor)])
        except Exception:
            return clip


def safe_subclip(clip, start: float, end: float):
    if hasattr(clip, "subclipped"):
        return clip.subclipped(start, end)
    return clip.subclip(start, end)


def safe_set_start(clip, start_time: float):
    if hasattr(clip, "with_start"):
        return clip.with_start(start_time)
    return clip.set_start(start_time)


def safe_audio_loop(clip, duration: float):
    try:
        from moviepy.audio.fx import AudioLoop
        return clip.with_effects([AudioLoop(duration=duration)])
    except Exception:
        pass

    try:
        import moviepy.audio.fx.all as afx
        return afx.audio_loop(clip, duration=duration)
    except Exception:
        pass

    if clip.duration <= 0:
        return clip
    n_loops = int(duration // clip.duration) + 1
    multi_clip = concatenate_audioclips([clip] * n_loops)
    return safe_subclip(multi_clip, 0, duration)


def safe_audio_fades(clip, fade_in: float = 2.5, fade_out: float = 3.0):
    res = clip
    if hasattr(res, "audio_fadein"):
        res = res.audio_fadein(fade_in)
    else:
        try:
            from moviepy.audio.fx import AudioFadeIn
            res = res.with_effects([AudioFadeIn(fade_in)])
        except Exception:
            pass
            
    if hasattr(res, "audio_fadeout"):
        res = res.audio_fadeout(fade_out)
    else:
        try:
            from moviepy.audio.fx import AudioFadeOut
            res = res.with_effects([AudioFadeOut(fade_out)])
        except Exception:
            pass
            
    return res


def run_page_audio_test(page_id: str = "master_mei"):
    """
    Modular audio preview runner isolated inside `outputs/{page_id}/audio_tests/`.
    Keeps the main page outputs and root completely clean.
    """
    base_factory_dir = os.path.dirname(os.path.abspath(__file__))
    
    # 1. Modular Paths Setup
    page_config_dir = os.path.join(base_factory_dir, "channels_config", page_id)
    
    # Dedicated Subdirectory for Audio Tests
    test_output_dir = os.path.join(base_factory_dir, "outputs", page_id, "audio_tests")
    os.makedirs(test_output_dir, exist_ok=True)

    bgm_folder = os.path.join(page_config_dir, "audio", "bgm")
    sfx_path = os.path.join(page_config_dir, "audio", "sfx", "dark_atmosphere_loop.wav")
    
    # Resolve Voiceover source (prioritizes production VO if available)
    prod_vo_path = os.path.join(base_factory_dir, "outputs", page_id, "temp_vo.mp3")
    test_vo_path = os.path.join(test_output_dir, "temp_test_vo.mp3")

    if os.path.exists(prod_vo_path):
        vo_path = prod_vo_path
    elif os.path.exists(test_vo_path):
        vo_path = test_vo_path
    else:
        print("[VO] No existing voiceover found. Synthesizing test audio inside audio_tests/...")
        generate_dummy_voiceover(test_vo_path, duration=10.0)
        vo_path = test_vo_path

    output_preview_path = os.path.join(test_output_dir, "audio_preview_test.mp3")

    print("--------------------------------------------------")
    print(f"[AUDIO TEST ENGINE] Target Page: {page_id}")
    print(f"[AUDIO TEST ENGINE] Output Directory: {test_output_dir}")
    print("--------------------------------------------------")

    # 2. Load Voiceover
    vo_clip = AudioFileClip(vo_path)
    total_duration = vo_clip.duration
    print(f"[VO] File: {os.path.basename(vo_path)} | Duration: {total_duration:.2f}s")

    # 3. Continuous SFX Loop
    if os.path.exists(sfx_path):
        sfx_clip = AudioFileClip(sfx_path)
        sfx_loop = safe_audio_loop(sfx_clip, total_duration)
        sfx_loop = safe_volume(sfx_loop, 0.15)
        print(f"[SFX] Continuous loop applied ({total_duration:.2f}s)")
    else:
        print("[SFX] Warning: Real SFX file not found, skipping SFX layer.")
        sfx_loop = None

    # 4. Dynamic BGM Selection
    bgm_files = [
        os.path.join(bgm_folder, f) 
        for f in os.listdir(bgm_folder) 
        if f.lower().endswith(('.mp3', '.wav'))
    ] if os.path.exists(bgm_folder) else []

    if bgm_files:
        chosen_bgm = random.choice(bgm_files)
        print(f"[BGM] Track: {os.path.basename(chosen_bgm)}")
        bgm_clip = AudioFileClip(chosen_bgm)
        bgm_duration = max(1.0, total_duration - 4.0)
        
        bgm_sub = safe_subclip(bgm_clip, 0, min(bgm_clip.duration, bgm_duration))
        bgm_start = safe_set_start(bgm_sub, 4.0)
        bgm_faded = safe_audio_fades(bgm_start, fade_in=2.5, fade_out=3.0)
        bgm_timed = safe_volume(bgm_faded, 0.20)
    else:
        print("[BGM] Warning: No BGM tracks found in page folder.")
        bgm_timed = None

    # 5. Audio Compositing
    clips_to_mix = [c for c in [sfx_loop, bgm_timed, vo_clip] if c is not None]
    final_audio = CompositeAudioClip(clips_to_mix)
    final_audio.write_audiofile(output_preview_path, fps=44100)

    print("--------------------------------------------------")
    print(f"[SUCCESS] Isolated test preview saved at:\n  {output_preview_path}")
    print("--------------------------------------------------")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Multi-Page Audio Test Engine")
    parser.add_argument("--page", type=str, default="master_mei", help="Page ID identifier")
    args = parser.parse_args()
    
    run_page_audio_test(page_id=args.page)