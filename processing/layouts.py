# -*- coding: utf-8 -*- 

import random
from moviepy.editor import (
    VideoFileClip,
    CompositeVideoClip,
    ColorClip,
    clips_array,
    vfx,
)
from .face_tracker import create_face_tracked_clip

def _build_video_canvas(layout, main_clip_raw, bottom_video_path, final_width, final_height):
    if layout == 'square_top_brainrot_bottom':
        video_height = int(final_height * 0.6)
        bottom_height = final_height - video_height

        main_clip = create_face_tracked_clip(main_clip_raw, video_height, final_width)

        if bottom_video_path:
            full_bottom_clip = VideoFileClip(str(bottom_video_path))
            if full_bottom_clip.duration > main_clip.duration:
                random_start = random.uniform(0, full_bottom_clip.duration - main_clip.duration)
                bottom_clip = full_bottom_clip.subclip(random_start, random_start + main_clip.duration)
            else:
                bottom_clip = full_bottom_clip
            bottom_clip = bottom_clip.resize(height=bottom_height)
            if bottom_clip.w > final_width:
                bottom_clip = bottom_clip.fx(vfx.crop, x_center=bottom_clip.w / 2, width=final_width)
            bottom_clip = bottom_clip.set_duration(main_clip.duration)
        else:
            bottom_clip = ColorClip(size=(final_width, bottom_height), color=(0,0,0), duration=main_clip.duration)

        video_canvas = clips_array([[main_clip], [bottom_clip]])
        subtitle_y_pos = video_height - 60 # Сдвигаем субтитры вверх
        subtitle_width = final_width - 40

    elif layout == 'full_top_brainrot_bottom':
        # Layout heights: 50/50 split
        video_container_height = final_height / 2
        bottom_height = final_height / 2

        # Main clip preparation
        main_clip = main_clip_raw.resize(width=final_width)

        # Position main_clip at the bottom of the top half
        main_clip_y_pos = video_container_height - main_clip.h
        main_clip = main_clip.set_position(('center', main_clip_y_pos))

        # Bottom clip preparation
        if bottom_video_path:
            full_bottom_clip = VideoFileClip(str(bottom_video_path))
            if full_bottom_clip.duration > main_clip.duration:
                random_start = random.uniform(0, full_bottom_clip.duration - main_clip.duration)
                bottom_clip = full_bottom_clip.subclip(random_start, random_start + main_clip.duration)
            else:
                bottom_clip = full_bottom_clip

            bottom_clip = bottom_clip.resize(height=bottom_height)
            if bottom_clip.w > final_width:
                bottom_clip = bottom_clip.fx(vfx.crop, x_center=bottom_clip.w / 2, width=final_width)
            bottom_clip = bottom_clip.set_duration(main_clip.duration)
        else:
            bottom_clip = ColorClip(size=(final_width, bottom_height), color=(0,0,0), duration=main_clip.duration)

        bottom_clip = bottom_clip.set_position(('center', 'bottom'))

        # Create final canvas
        bg = ColorClip(size=(final_width, final_height), color=(0,0,0), duration=main_clip.duration)
        video_canvas = CompositeVideoClip([bg, main_clip, bottom_clip])

        # Subtitles position
        subtitle_y_pos = video_container_height - 60
        subtitle_width = final_width - 40

    elif layout == 'full_center':
        main_clip = main_clip_raw.resize(width=final_width)
        bg = ColorClip(size=(final_width, final_height), color=(0,0,0), duration=main_clip.duration)
        video_canvas = CompositeVideoClip([bg, main_clip.set_position('center', 'center')])
        subtitle_y_pos = (final_height + main_clip.h) / 2 + 20
        subtitle_width = main_clip.w - 40

    elif layout == 'face_track_9_16':
        main_clip = create_face_tracked_clip(main_clip_raw, final_height, final_width)
        video_canvas = main_clip
        subtitle_y_pos = final_height * 0.75
        subtitle_width = final_width - 40

    else: # square_center
        video_height = int(final_height * 0.7)
        
        main_clip = create_face_tracked_clip(main_clip_raw, video_height, final_width)
        
        bg = ColorClip(size=(final_width, final_height), color=(0,0,0), duration=main_clip.duration)
        video_canvas = CompositeVideoClip([bg, main_clip.set_position('center', 'center')])
        subtitle_y_pos = final_height * 0.75
        subtitle_width = main_clip.w - 40
    
    return video_canvas, subtitle_y_pos, subtitle_width

