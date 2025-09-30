# -*- coding: utf-8 -*- 

import cv2
import numpy as np
from moviepy.editor import vfx, concatenate_videoclips
from config import HAARCASCADE_FRONTALFACE_DEFAULT, HAARCASCADE_PROFILEFACE

def get_box_center(box):
    x, y, w, h = box
    return (x + w/2, y + h/2)

def distance(p1, p2):
    return ((p1[0] - p2[0])**2 + (p1[1] - p2[1])**2)**0.5

def create_face_tracked_clip(main_clip_raw, target_height, target_width):
    """
    Creates a clip with face tracking. The frame only moves if the speaker's face
    is about to leave the visible cropped area.
    """
    main_clip_resized = main_clip_raw.resize(height=target_height)
    
    if main_clip_resized.w <= target_width:
        return main_clip_resized

    try:
        face_cascade = cv2.CascadeClassifier(HAARCASCADE_FRONTALFACE_DEFAULT)
        # Also load the profile cascade to detect faces from the side
        profile_cascade = cv2.CascadeClassifier(HAARCASCADE_PROFILEFACE)
    except Exception as e:
        print(f"Could not load face cascade model(s): {e}. You may need to download 'haarcascade_profileface.xml'. Falling back to center crop.")
        return main_clip_resized.fx(vfx.crop, x_center=main_clip_resized.w / 2, width=target_width)

    subclips = []
    
    crop_x_center = main_clip_resized.w / 2
    target_crop_x_center = main_clip_resized.w / 2
    crop_half_width = target_width / 2
    tracked_face_box = None

    # Smoothing factor for camera movement. Lower value = smoother/slower.
    smoothing_factor = 1.0

    step = 0.25  # Process video in chunks of 0.25 seconds
    for t in np.arange(0, main_clip_resized.duration, step):
        frame = main_clip_resized.get_frame(t)
        gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
        
        # Detect faces using both frontal and profile cascades
        faces_frontal = face_cascade.detectMultiScale(gray, 1.1, 4, minSize=(80, 80))
        faces_profile = profile_cascade.detectMultiScale(gray, 1.1, 4, minSize=(80, 80))

        # For profile detection, also check the flipped image to find faces looking the other way
        gray_flipped = cv2.flip(gray, 1)
        faces_profile_flipped = profile_cascade.detectMultiScale(gray_flipped, 1.1, 4, minSize=(80, 80))
        
        all_faces = []
        if len(faces_frontal) > 0:
            all_faces.extend(list(faces_frontal))
        if len(faces_profile) > 0:
            all_faces.extend(list(faces_profile))
        
        # Convert coordinates of flipped detections back
        if len(faces_profile_flipped) > 0:
            for (x, y, w, h) in faces_profile_flipped:
                all_faces.append((gray.shape[1] - x - w, y, w, h))

        faces = np.array(all_faces)
        is_new_face = False

        if len(faces) > 0:
            if tracked_face_box is None:
                # Case 1: No face was tracked before. This is a new face.
                tracked_face_box = sorted(faces, key=lambda f: f[2]*f[3], reverse=True)[0]
                is_new_face = True
            else:
                # Case 2: A face was being tracked. Find it in the new frame.
                previous_center = get_box_center(tracked_face_box)
                closest_face = min(faces, key=lambda f: distance(get_box_center(f), previous_center))
                
                max_allowed_distance = tracked_face_box[2] * 1.5
                if distance(get_box_center(closest_face), previous_center) < max_allowed_distance:
                    # The same face is still being tracked.
                    tracked_face_box = closest_face
                else:
                    # Case 3: The old face was lost. This is a new face.
                    tracked_face_box = sorted(faces, key=lambda f: f[2]*f[3], reverse=True)[0]
                    is_new_face = True

            face_center_x, _ = get_box_center(tracked_face_box)
            
            if is_new_face:
                # INSTANT JUMP: A new face appeared, so snap the camera immediately.
                crop_x_center = face_center_x
                target_crop_x_center = face_center_x
            else:
                # SMOOTH PAN: The same face is moving. Only adjust if it nears the edge.
                face_width = tracked_face_box[2]
                visible_left = crop_x_center - crop_half_width
                visible_right = crop_x_center + crop_half_width
                buffer = face_width * 0.5

                if not (visible_left + buffer < face_center_x < visible_right - buffer):
                    target_crop_x_center = face_center_x
        else:
            # No faces detected, lose track.
            tracked_face_box = None

        # Apply smoothing towards the target. 
        # If no move is needed, target equals current, so crop_x_center stays put.
        # If it's a new face, this will have no effect since crop_x_center was already snapped.
        crop_x_center = (smoothing_factor * target_crop_x_center) + ((1 - smoothing_factor) * crop_x_center)

        # Clamp the crop_x_center to avoid black bars
        min_x = crop_half_width
        max_x = main_clip_resized.w - crop_half_width
        clamped_crop_x_center = max(min_x, min(crop_x_center, max_x))
        crop_x_center = clamped_crop_x_center
        target_crop_x_center = max(min_x, min(target_crop_x_center, max_x))

        subclip_end = min(t + step, main_clip_resized.duration)
        subclip = main_clip_resized.subclip(t, subclip_end)
        cropped_subclip = subclip.fx(vfx.crop, x_center=clamped_crop_x_center, width=target_width)
        subclips.append(cropped_subclip.without_audio())

    if not subclips:
        return main_clip_resized.fx(vfx.crop, x_center=main_clip_resized.w / 2, width=target_width)

    final_video = concatenate_videoclips(subclips)
    final_video.audio = main_clip_raw.audio
    return final_video

